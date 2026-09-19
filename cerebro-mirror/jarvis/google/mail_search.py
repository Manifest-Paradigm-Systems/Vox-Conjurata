"""Reading the mail archive — the half that never existed.

`gmail_index.py` writes `message_fts` and nothing has ever read it: there is no
`MATCH` anywhere in this module. The index has been write-only since the day it
was built, which is why Jarvis cannot answer a question about the owner's mail.
This is the read path, in three parts:

    search_archive  the local index. Fast, offline, and the default answer.
    read_message    one message's full body.
    search_live     Gmail itself, for anything the crawl has not reached.

TWO AXES, AGAIN. `account` is a MAILBOX, not a stream — the same conflation that
made `control.status()` report 0 for every stream and made `--stream gmail` match
nothing, because no account is labelled "gmail". The stream says which TABLE; the
account says which mailbox.

THE EASIEST THING TO GET WRONG: the index is not uniform. `message_fts` holds
62,657 of 100,095 messages — promotions and social are metadata-only *by design*
(schema.sql: "TWO TIERS, NOT A FILTER"). So a search that drives from
`message_fts` searches 63% of the archive and returns a confidently empty answer
for everything else — including receipts and order confirmations, which live in
promotions. This module queries the full-text tier AND the metadata tier and
merges them, so "no results" means the archive has nothing, not that the query
missed a tier.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time

import auth
import control
import gmail_index

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))

# How deep into the metadata tier a LIKE scan will look. The tier is ~37k rows;
# this bounds the work on a query that matches something very common.
META_SCAN_LIMIT = 4000


# ---------------------------------------------------------------- query terms
# Mirrored from brain/db.py (`_words`, `STOPWORDS`, `content_terms`, `_rank`),
# deliberately rather than imported: this module ships as a standalone service
# and must not depend on the brain's module tree, which is deployed separately
# and lives on a different host. If the list is ever retuned there, retune here.

def _words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Za-z0-9_]{3,}", text or "")]


STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "does", "did", "not", "you", "your",
    "are", "was", "were", "has", "have", "had", "but", "can", "could", "would", "should",
    "what", "when", "where", "which", "who", "why", "how", "about", "into", "from",
    "they", "them", "then", "than", "there", "here", "his", "her", "its", "our", "their",
    "will", "shall", "may", "might", "must", "been", "being", "get", "got", "make", "made",
    "use", "used", "using", "any", "all", "some", "more", "most", "other", "such", "only",
    "own", "same", "too", "very", "just", "now", "also", "out", "off", "over", "under",
    "again", "once", "tell", "say", "said", "know", "want", "need", "like", "well", "yes",
}


def content_terms(text: str) -> list[str]:
    """Query words that actually carry meaning.

    Without this an OR'd FTS match on a natural-language question matches almost
    every row in the mailbox ('the', 'what', 'did'), which is worse than returning
    nothing: the model is handed confident-looking, irrelevant mail.
    """
    return [w for w in _words(text) if w not in STOPWORDS]


def _rank(rows, terms: list[str], limit: int, keys: tuple[str, ...]):
    """Keep only rows sharing a content term with the query, best overlap first.

    FTS5's ranking rewards documents matching many of the OR'd terms, but it will
    still hand back a row that matched only one common word. This is the precision
    gate in front of the model.
    """
    scored = []
    for i, r in enumerate(rows):
        hay = " ".join(str(r[k] or "") for k in keys if k in r.keys()).lower()
        score = sum(1 for t in terms if t in hay)
        if score:
            scored.append((score, -i, r))       # -i keeps source order on ties
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [r for _, _, r in scored[:limit]]


# ---------------------------------------------------------------- connection

def connect(path: str | None = None) -> sqlite3.Connection:
    """A read-only connection to the index.

    Read-only because this path only ever reads, and a search that cannot write
    cannot damage an archive that took eight hours to build. `busy_timeout` is
    long because the crawlers hold the write lock for a whole page at a time, and
    a search that errors under a crawl would be a search that fails every hour on
    the hour.

    `check_same_thread=False` because the API serves through a threadpool:
    SQLite's default guard refuses a connection touched from a second thread, and
    that guard fired the first time `/health` was called. Each request opens its
    own connection and closes it, and nothing here uses one concurrently — which
    is the actual rule the guard exists to enforce.
    """
    conn = sqlite3.connect(f"file:{path or DB_PATH}?mode=ro", uri=True, timeout=30,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# ---------------------------------------------------------------- exclusions

def _field(row, key, default=""):
    """Read a field from either a sqlite3.Row or a plain dict.

    Live results are dicts and archive hits are Rows; the exclusion rules must
    apply identically to both, so this is the one place that knows the difference.
    sqlite3.Row raises IndexError for an absent column, dict raises KeyError.
    """
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _excluded(exclusions, row) -> bool:
    """Apply the owner's rules to one hit.

    `control.is_excluded` covers `address` and `contains` by substring — the same
    call the crawler makes before writing, so the two cannot drift. It does NOT
    implement `between`/`thread`/`id`, because those are row-shaped rules rather
    than text rules, and it silently ignores anything it is not handed a field
    for. They are applied here instead.

    An erasure is a RULE, not an event (control.py's own docstring): the rows have
    already been purged, so this is the guard for the window between a rule being
    set and the next purge, and for anything a re-crawl might reintroduce.
    """
    text_rules = [(k, v) for k, v in exclusions if k in ("address", "contains")]
    if text_rules and control.is_excluded(
            text_rules, _field(row, "from_addr"), _field(row, "subject"),
            _field(row, "body", _field(row, "snippet"))):
        return True
    to_addrs = str(_field(row, "to_addrs", "[]")).lower()
    if any(k == "address" and v in to_addrs for k, v in exclusions if v):
        return True
    for kind, value in exclusions:
        if not value:
            continue
        if kind == "thread" and str(_field(row, "thread_id")) == value:
            return True
        if kind == "id" and str(_field(row, "id")) == value:
            return True
        if kind == "between":
            try:
                lo, hi = value.split("..")
                low = int(time.mktime(time.strptime(lo, "%Y-%m-%d")) * 1000)
                high = int(time.mktime(time.strptime(hi, "%Y-%m-%d")) * 1000)
            except (ValueError, TypeError):
                continue                      # a malformed rule must not kill a search
            when = _field(row, "internal_date", 0) or 0
            if low <= when <= high:
                return True
    return False


def load_rules(conn, stream: str = "gmail"):
    """The owner's rules, re-read per request.

    Not cached: control.py's docstring is explicit that these are read every page
    by the crawlers rather than once at startup, because an exclusion is what you
    reach for the moment you decide something should not be indexed. A cached
    copy would mean "takes effect after a restart".
    """
    try:
        return control.load_exclusions(conn, stream)
    except sqlite3.Error:
        return []


# ---------------------------------------------------------------- archive

_COLS = ("m.id, m.account, m.thread_id, m.internal_date, m.from_addr, m.to_addrs,"
         " m.subject, m.snippet, m.has_attachment, m.replied")


def _fts_hits(conn, terms, account, limit):
    """The full-text tier: messages whose body was worth indexing.

    Each term is double-quoted so FTS5 reads it as a term and not as syntax. The
    reference implementation joins bare terms and swallows `sqlite3.Error`, which
    means a query containing an operator returns nothing, silently — the exact
    shape of failure this fleet keeps paying for. Quoting removes the class.
    """
    match = " OR ".join(f'"{t}"' for t in terms)
    sql = (f"SELECT {_COLS}, t.body AS body,"
           " snippet(message_fts, 4, '', '', ' … ', 18) AS snip,"
           " bm25(message_fts) AS score"
           " FROM message_fts"
           " JOIN messages m ON m.id = message_fts.message_id"
           " LEFT JOIN message_text t ON t.message_id = m.id"
           " WHERE message_fts MATCH ? AND m.status = 'active'")
    args: list = [match]
    if account:
        sql += " AND m.account = ?"
        args.append(account)
    sql += " ORDER BY score LIMIT ?"
    args.append(limit * 4)
    # Deliberately NOT swallowed here, unlike the reference implementation in
    # brain/db.py: a malformed query there returns [], which at this boundary
    # would be indistinguishable from "the archive has nothing". The caller
    # catches this and reports which tier failed.
    return conn.execute(sql, args).fetchall()


def _meta_hits(conn, terms, account, limit):
    """The metadata tier: everything else, matched on subject and sender.

    This is what makes the answer complete. Promotions, social and forum mail has
    no body in the index by design, but it still has a subject and a sender, and
    that is often enough to find a receipt.
    """
    like = " OR ".join("lower(COALESCE(m.subject,'')) LIKE ?" for _ in terms)
    sql = (f"SELECT {_COLS}, '' AS body, m.snippet AS snip, 0.0 AS score"
           " FROM messages m"
           f" WHERE m.status = 'active' AND ({like})")
    args: list = [f"%{t}%" for t in terms]
    if account:
        sql += " AND m.account = ?"
        args.append(account)
    sql += " ORDER BY m.internal_date DESC LIMIT ?"
    args.append(min(limit * 4, META_SCAN_LIMIT))
    return conn.execute(sql, args).fetchall()


def search_archive(conn, query: str, account: str | None = None, limit: int = 10):
    """Find messages in the local index. Returns (hits, error).

    `error` is None on success and a short string when a tier failed — the caller
    must not present a failed search as an empty archive.
    """
    terms = content_terms(query)
    if not terms:
        return [], None

    error = None
    rows = []
    try:
        rows.extend(_fts_hits(conn, terms, account, limit))
    except sqlite3.Error as exc:
        error = f"full-text tier: {exc}"
    try:
        rows.extend(_meta_hits(conn, terms, account, limit))
    except sqlite3.Error as exc:
        error = (error + "; " if error else "") + f"metadata tier: {exc}"

    # Merge, keeping the richer row when a message appears in both tiers.
    by_id: dict[str, sqlite3.Row] = {}
    for r in rows:
        prev = by_id.get(r["id"])
        if prev is None or (len(r["body"] or "") > len(prev["body"] or "")):
            by_id[r["id"]] = r

    rules = load_rules(conn)
    kept = [r for r in by_id.values() if not _excluded(rules, r)]
    return _rank(kept, terms, limit, ("subject", "from_addr", "body")), error


def read_message(conn, message_id: str) -> dict | None:
    """One message, with its full body. None if it is not in the archive."""
    row = conn.execute(
        "SELECT m.*, COALESCE(t.body,'') AS body FROM messages m"
        " LEFT JOIN message_text t ON t.message_id = m.id"
        " WHERE m.id = ?", (message_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    rules = load_rules(conn)
    d["excluded"] = _excluded(rules, d)
    return d


# ---------------------------------------------------------------- live

def search_live(account: str, query: str, limit: int = 10) -> tuple[list[dict], str | None]:
    """Ask Gmail itself. Returns (messages, error).

    For what the crawl has not reached — mail that arrived since the last pass, or
    something in a label the crawler treats as metadata-only. Uses the same
    `api_get` the crawlers use, so it inherits the real backoff and the 401
    re-auth rather than reimplementing a worse version of both.

    `auth.TokenSource` and not the near-duplicate in gmail_index: this one holds a
    lock, and the duplicate does not.

    EXCLUSIONS APPLY HERE TOO. The archive has the rules baked in — the rows were
    purged — so a live query is the one path that can hand back something the
    owner explicitly erased, purely because it was asked a different way. Caught
    live: `newer_than:7d` returned a message from the excluded no-reply@substack
    address, which the archive would never have returned.
    """
    try:
        token = auth.TokenSource(account)
        listing = gmail_index.api_get(
            token, "/messages", {"q": query, "maxResults": min(limit, 25)})
    except Exception as exc:                      # noqa: BLE001 — reported, not raised
        return [], f"{type(exc).__name__}: {exc}"

    out: list[dict] = []
    for ref in (listing.get("messages") or [])[:limit]:
        try:
            full = gmail_index.api_get(token, f"/messages/{ref['id']}", {
                "format": "metadata",
                "metadataHeaders": ["From", "To", "Subject", "Date"],
            })
        except Exception as exc:                  # noqa: BLE001
            return out, f"{type(exc).__name__}: {exc}"
        headers = {h["name"].lower(): h["value"]
                   for h in (full.get("payload") or {}).get("headers") or []}
        out.append({
            "id": full.get("id"),
            "thread_id": full.get("threadId"),
            "subject": headers.get("subject", ""),
            "from_addr": headers.get("from", ""),
            "to_addrs": headers.get("to", ""),
            "date": headers.get("date", ""),
            "snippet": full.get("snippet", ""),
        })

    # The owner's rules, applied to the live tier exactly as to the archive tier.
    # A failure to read the rules must not silently reopen the path they close, so
    # this returns an error rather than a filtered-looking success.
    try:
        rules = load_rules(connect())
    except sqlite3.Error as exc:
        return out, f"could not read exclusions: {exc}"
    return [m for m in out if not _excluded(rules, m)], None


# ---------------------------------------------------------------- cli

def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        print("\nusage: mail_search.py search <query> [--account X] [--limit N]"
              "\n       mail_search.py read <message-id>"
              "\n       mail_search.py live <query> [--account X]")
        return 1
    cmd = argv[1]
    account = argv[argv.index("--account") + 1] if "--account" in argv else None
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else 10

    if cmd == "search":
        conn = connect()
        hits, error = search_archive(conn, argv[2], account, limit)
        if error:
            print(f"  !! {error}")
        print(f"  {len(hits)} hit(s)")
        for h in hits:
            when = time.strftime("%Y-%m-%d", time.localtime((h["internal_date"] or 0) / 1000))
            print(f"  {when}  {h['from_addr'][:38]:<38} {h['subject'][:56]}")
        return 0

    if cmd == "read":
        conn = connect()
        d = read_message(conn, argv[2])
        if d is None:
            print("  not in the archive")
            return 1
        print(json.dumps(d, indent=2, default=str)[:4000])
        return 0

    if cmd == "live":
        msgs, error = search_live(account or "mnmeyer@gmail.com", argv[2], limit)
        if error:
            print(f"  !! {error}")
        print(f"  {len(msgs)} live message(s)")
        for m in msgs:
            print(f"  {m['date'][:31]:<31} {m['from_addr'][:34]:<34} {m['subject'][:46]}")
        return 0

    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
