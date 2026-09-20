"""Reading the document index — the half that never existed.

`drive_index.py` has written `document_fts` and `document_text` since the day it was
built, and nothing has ever read them. No `MATCH`, no `SELECT body`, nothing. So Jarvis
could not answer a question about a single Drive file — not the plumbing estimate, not
the MRI report, not the service record — while 914 documents of text sat in the index.
This is the read path. It is the same shape as `mail_search.py`, for the same reason mail
needed one: an index nothing reads is a very expensive way of storing nothing.

TWO TIERS AGAIN, AND THIS TIME THE SECOND ONE MEANS SOMETHING DIFFERENT.

In mail, the metadata tier is promotions and social — real messages whose bodies were
never indexed, deliberately. Here it is **documents whose contents have not been read**:
2,713 of them, all PDFs, because the host had no poppler. Those are not the same thing
and must not be labelled the same way, or a coverage gap reads as an answer. Every result
therefore carries `read` (do we have the text?) and `text_state` (why not, when we do
not), and the caller is expected to say so out loud. A file whose name matches is still a
real answer — "Statement_65769.PDF" tells the owner something — but it is a weaker one.

THERE IS NO `search_live`. Mail has one because Gmail can be asked for a message the
crawl has not reached. No API reads inside a PDF, and Drive's own search does not index
these MIME types. The documents lane can only ever be as good as the crawl, so the
response says `"live": "unsupported"` rather than leaving a fallback that was never
written looking like one that was merely not needed today.

REUSE, NOT RE-MIRRORING. `mail_search.py` mirrors `_words`/`content_terms`/`_rank` from
`brain/db.py` because the brain is a separate deploy on a different host. This module is
the same tree, on the same host, in the same service as `mail_search.py`, so it imports
them. If you are about to copy those functions in here, that is a mistake, not a
consistency fix.

Usage:
    python3 document_search.py search <query> [--account X] [--limit N] [--source S]
    python3 document_search.py read <file-id>
    python3 document_search.py stats
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Imported, not mirrored — see the docstring.
from mail_search import (                                        # noqa: E402
    META_SCAN_LIMIT, _excluded, _field, _rank, connect, content_terms, load_rules,
)

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))

# Which source's hits are read first. The service record outranks the Drive: it is the
# owner's own military file, it is not reproducible from anywhere else, and when a
# question is about his service the answer is in there rather than in a scanned receipt.
SOURCE_ORDER = ("lifepacket", "drive")

_COLS = ("f.id, f.account, f.name, f.mime_type, f.modified_time, f.source, "
         "f.text_state, f.size")


def _shape(row, read: bool) -> dict:
    """One result, with the two fields that stop a coverage gap reading as an answer."""
    d = {k: row[k] for k in row.keys() if k not in ("body",)}
    d["read"] = read
    d["snippet"] = (d.get("snip") or d.get("snippet") or "").strip()
    d.pop("snip", None)
    return d


def _fts_query(conn, match, limit, account, source):
    sql = (f"SELECT {_COLS}, t.body AS body,"
           " snippet(document_fts, 3, '[', ']', ' … ', 16) AS snip,"
           " bm25(document_fts) AS score"
           " FROM document_fts"
           " JOIN drive_files f ON f.id = document_fts.file_id"
           " LEFT JOIN document_text t ON t.file_id = f.id"
           " WHERE document_fts MATCH ? AND f.trashed = 0 AND f.status = 'active'")
    args: list = [match]
    if account:
        sql += " AND f.account = ?"
        args.append(account)
    if source:
        sql += " AND f.source = ?"
        args.append(source)
    sql += " ORDER BY score LIMIT ?"
    args.append(limit * 4)
    return conn.execute(sql, args).fetchall()


def _fts_hits(conn, terms, limit, account=None, source=None):
    """Documents whose CONTENTS match. bm25-ranked within the source.

    OR, like mail, and an AND-first variant was tried here and REMOVED. It was added
    after a birth certificate could not be found by the words "state file number", and
    the reasoning looked sound — a corpus of forms shares so much vocabulary that an OR
    match buries the one document that matters. Measured against four real queries, AND
    and OR returned identical results on three and neither could find that certificate on
    the fourth.

    The certificate was unfindable because our own OCR read "STATE FILE NUMBER" as
    "BTATEFILENUMBER", so the word is not in the index at all. The retrieval was never
    the problem, and the extra query bought nothing. Recorded because the AND version is
    a plausible-looking change someone will otherwise re-propose.
    """
    match = " OR ".join(f'"{t}"' for t in terms)
    return _fts_query(conn, match, limit, account, source)


def _meta_hits(conn, terms, limit, account=None, source=None):
    """Documents whose NAME matches and whose contents we never read.

    Bounded like mail's metadata tier, and for the same reason: a LIKE scan over ~2,700
    rows is fine, but a query matching something very common should not walk all of them.
    """
    like = " OR ".join("lower(COALESCE(f.name,'')) LIKE ?" for _ in terms)
    sql = (f"SELECT {_COLS}, '' AS body, '' AS snip, 0.0 AS score"
           " FROM drive_files f"
           " WHERE f.trashed = 0 AND f.status = 'active'"
           "   AND NOT EXISTS (SELECT 1 FROM document_text d WHERE d.file_id = f.id)"
           f"   AND ({like})")
    args: list = [f"%{t}%" for t in terms]
    if account:
        sql += " AND f.account = ?"
        args.append(account)
    if source:
        sql += " AND f.source = ?"
        args.append(source)
    sql += " ORDER BY f.modified_time DESC LIMIT ?"
    args.append(min(limit * 4, META_SCAN_LIMIT))
    return conn.execute(sql, args).fetchall()


def search_archive(conn, query: str, account=None, limit: int = 8, source=None
                   ) -> tuple[list[dict], str | None]:
    """Search the document index. Returns (hits, error).

    `error` names the tier that failed, and is never folded into an empty result — the
    rule this whole subsystem keeps relearning. A caller that cannot tell "nothing
    matched" from "the search broke" will report the second as the first.
    """
    terms = content_terms(query)
    if not terms:
        return [], None

    rows, errors = [], []
    failed_tiers = []
    try:
        rows += list(_fts_hits(conn, terms, limit, account, source))
    except sqlite3.Error as exc:
        # NOT swallowed. The reference implementation in brain/db.py returns [] on a bad
        # query, which is indistinguishable from "no mail" — and that is exactly how a
        # broken index comes to look like an empty one.
        failed_tiers.append(f"full-text tier: {exc}")

    try:
        rows += list(_meta_hits(conn, terms, limit, account, source))
    except sqlite3.Error as exc:
        failed_tiers.append(f"name tier: {exc}")

    if len(failed_tiers) == 2:
        return [], "; ".join(failed_tiers)
    errors.extend(failed_tiers)

    rules = load_rules(conn, "drive") + load_rules(conn, "records")
    by_id: dict[str, sqlite3.Row] = {}
    for r in rows:
        prev = by_id.get(r["id"])
        if prev is None or (len(r["body"] or "") > len(prev["body"] or "")):
            by_id[r["id"]] = r

    # `_excluded` looks for `subject` and `from_addr`, because it was written for mail.
    # A document's equivalent of a subject is its filename, and an owner's `contains`
    # rule must not miss a document just because the field is named differently here —
    # so the name is offered under both keys before the rules are applied.
    kept = []
    for r in by_id.values():
        probe = dict(r)
        probe.setdefault("subject", probe.get("name") or "")
        if not _excluded(rules, probe):
            kept.append(r)

    # Ranked WITHIN each source, then concatenated. `_rank` sorts by how well a row
    # matches the query, so running it across the merged set would interleave a scanned
    # receipt between two pages of the service record and destroy the ordering that makes
    # records primary. The tier is the first key; relevance only orders within it.
    out: list[dict] = []
    for want in SOURCE_ORDER + ("other",):
        group = [r for r in kept
                 if (r["source"] if r["source"] in SOURCE_ORDER else "other") == want]
        if not group:
            continue
        ranked = _rank(group, terms, limit, ("name", "body"))
        for r in ranked:
            read = bool((r["body"] or "").strip())
            out.append(_shape(r, read))
    return out[:limit], ("; ".join(errors) if errors else None)


def read_document(conn, file_id: str) -> dict | None:
    """One document in full, with a page count if the text carries page markers."""
    row = conn.execute("""
        SELECT f.id, f.account, f.name, f.mime_type, f.modified_time, f.source,
               f.text_state, f.size, COALESCE(t.body,'') AS body, t.source AS text_source
        FROM drive_files f LEFT JOIN document_text t ON t.file_id = f.id
        WHERE f.id = ?""", (file_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["pages"] = d["body"].count("[page ") or (1 if d["body"].strip() else 0)
    d["read"] = bool(d["body"].strip())
    d["excluded"] = _excluded(load_rules(conn, "drive") + load_rules(conn, "records"), d)
    return d


def stats(conn) -> dict:
    """Coverage, split by source — the numbers that belong on /health."""
    out = {}
    for source, in conn.execute("SELECT DISTINCT source FROM drive_files ORDER BY source"):
        row = conn.execute("""
            SELECT COUNT(*) n,
                   SUM(CASE WHEN d.file_id IS NOT NULL THEN 1 ELSE 0 END) with_text,
                   SUM(CASE WHEN f.text_state LIKE 'unavailable%' THEN 1 ELSE 0 END) bad,
                   SUM(CASE WHEN f.text_state IS NULL THEN 1 ELSE 0 END) untouched
            FROM drive_files f LEFT JOIN document_text d ON d.file_id = f.id
            WHERE f.source = ?""", (source,)).fetchone()
        key = source or "drive"
        out[key] = {k: (row[k] or 0) for k in row.keys()}
        # `unread` is "fetchable and not yet read" — NOT every row without text. Media,
        # archives and folders have no text to get, and counting them as unread made
        # /health report 6,471 on a Drive where the real figure was 6. A number that
        # cannot be acted on stops being read, which is how the original gap hid.
        out[key]["unread"] = conn.execute(
            "SELECT COUNT(*) FROM drive_files f"
            " LEFT JOIN document_text d ON d.file_id = f.id"
            " WHERE d.file_id IS NULL AND f.indexable = 1 AND f.source = ?",
            (key,)).fetchone()[0]
        out[key]["not_fetchable"] = row["n"] - row["with_text"] - out[key]["unread"]
    return out


# ---------------------------------------------------------------- cli
def _print(hits, error):
    if error:
        print(f"  !! {error}")
    print(f"  {len(hits)} hit(s)")
    for h in hits:
        mark = "" if h["read"] else "  [NOT READ]"
        print(f"    {(h['modified_time'] or '')[:10]}  {h['source']:<10} "
              f"{h['name'][:52]}{mark}")
        if h["snippet"]:
            print(f"        ...{h['snippet'][:120]}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    conn = connect()
    cmd = argv[1]

    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    if cmd == "search":
        if len(argv) < 3:
            raise SystemExit("usage: document_search.py search <query>")
        hits, error = search_archive(conn, argv[2], account=opt("--account"),
                                     limit=int(opt("--limit", 8)), source=opt("--source"))
        _print(hits, error)
        return 0

    if cmd == "read":
        if len(argv) < 3:
            raise SystemExit("usage: document_search.py read <file-id>")
        d = read_document(conn, argv[2])
        if not d:
            print("  not in the index")
            return 1
        print(f"  {d['name']}  ({d['source']}, {d['pages']} page(s), "
              f"{len(d['body']):,} chars, via {d['text_source']})")
        print(d["body"][:3000])
        return 0

    if cmd == "stats":
        for source, s in stats(conn).items():
            print(f"  {source:<11} {s['n']:>6} rows, {s['with_text']:>6} with text,"
                  f" {s['unread']:>6} unread ({s['bad']} unavailable)")
        return 0

    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
