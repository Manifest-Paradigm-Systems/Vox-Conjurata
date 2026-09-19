"""Index one Gmail account into the local database. READ-ONLY against Google.

About 100,000 messages in the pilot account, which is roughly 300 MB of extracted text.
Small enough to store every body, which is why there is no triage step here: keep a light
row for everything, full text for everything that is not obviously bulk, and decide what
matters at QUERY time where a mistake costs a low-ranked result instead of a lost record.

FOUR THINGS THIS FILE IS CAREFUL ABOUT, each one paid for:

1. PACING. Gmail's per-user quota is generous per second, and a crawl has nothing else to
   do, so unpaced it fires as fast as the network allows until it is refused. 0.08s
   between requests costs about two hours across 100k messages and removes the problem.

2. KEEPING THE ERROR BODY. This used to raise "giving up after 6 tries: 403" and discard
   the response — indistinguishable from a scope failure, a revoked token, or a quota
   wall. The body says which.

3. RESUMABILITY. A 100k crawl runs for hours and WILL be interrupted. Progress is
   checkpointed per page, so a re-run continues instead of starting over.

4. ENGAGEMENT IS THE OWNER'S OWN BEHAVIOUR. "Never replied to in five years" is not a
   heuristic someone invented. It would also delete his bank, so it is recorded, never
   acted on: `replied` feeds ranking and the junk report, and the owner approves anything
   destructive.

Usage:
    python3 gmail_index.py init
    python3 gmail_index.py add-account <email> <label> [purpose]
    python3 gmail_index.py run <email> [max_messages]
    python3 gmail_index.py junk <email>
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth     # noqa: E402
import control  # noqa: E402
from control import load_exclusions, is_excluded  # noqa: E402,F401

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))
SCHEMA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
API = "https://gmail.googleapis.com/gmail/v1/users/me"

# Gmail has been classifying this mail for years, tuned on this owner's own inbox. Read
# the labels; do not rebuild a spam filter.
LIGHT_ONLY = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS", "SPAM", "TRASH"}
# CATEGORY_UPDATES looks like noise and is the opposite: receipts, confirmations,
# shipping, statements. It gets full text.
PAGE = 500

# Seconds between API requests. 0.08 is ~12/s, comfortably inside Gmail's per-user quota
# and still fast enough that 100k messages takes a couple of hours rather than a week.
REQUEST_DELAY = float(os.getenv("JARVIS_REQUEST_DELAY", "0.08"))

# Concurrent message fetches. The network round-trip is ~0.23s of the ~0.63s a message
# costs, so overlapping it is the only real speed lever. Gmail's per-user quota is ~50
# requests/second and 4 workers at ~0.31s each is about 13/s — a quarter of the budget,
# which is deliberately conservative: the one unexplained failure in this project was a
# 403 under unpaced load, and I would rather find that limit slowly than rediscover it.
WORKERS = int(os.getenv("JARVIS_GMAIL_WORKERS", "4"))
FAILURE_LOG = os.getenv("JARVIS_GMAIL_FAILURE_LOG", "/tmp/gmail-fetch-failures.log")


# ---------------------------------------------------------------- db
def db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=180)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Patience. More than one crawler writes to this database — the Drive scan and this
    # one run against separate API quotas but share one file, and each holds the write
    # lock for a whole page. At 30 seconds this crawler spent two hours building the reply
    # graph and then died writing the cached copy of it, because the other one happened
    # to be committing.
    conn.execute("PRAGMA busy_timeout = 180000")
    return conn


def migrate(conn):
    """Columns added after the first release.

    CREATE TABLE IF NOT EXISTS will not add a column to a table that already exists, so
    anything new has to be applied explicitly. `remote_id` is Gmail's own attachment id —
    without it an attachment row is a description of a file we have no way to fetch.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(attachments)")}
    if "remote_id" not in cols:
        conn.execute("ALTER TABLE attachments ADD COLUMN remote_id TEXT")
        print("  migrated: attachments.remote_id")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(attachments)")}
    if "text_source" not in cols:
        conn.execute("ALTER TABLE attachments ADD COLUMN text_source TEXT")
        print("  migrated: attachments.text_source")
    if "inline" not in cols:
        conn.execute("ALTER TABLE attachments ADD COLUMN inline INTEGER DEFAULT 0")
        print("  migrated: attachments.inline")

    # Phone data carries a DOMAIN, like everything else. Calls and texts arrive from the
    # handset rather than from a Google account, but a contractor is still a property
    # matter and Michael's school is still family — and the domain is what lets a rule
    # question search the business side without wading through medical records.
    #
    # Left NULL until assigned: an unassigned row is honest, a wrongly-defaulted one is
    # a lie that looks like data.
    for table in ("sms", "mms", "mms_parts", "calls"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "account" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN account TEXT")
            print(f"  migrated: {table}.account")


def init():
    conn = db()
    with conn:
        conn.executescript(open(SCHEMA).read())
        migrate(conn)
    print(f"schema applied to {DB_PATH}")


def add_account(email: str, label: str, purpose: str = "", shared_with: str = ""):
    """Register an account before it can be indexed.

    The label is the point. It is what lets an answer say "that's from your property
    account" instead of presenting medical records as though they belonged to the
    rental business — the whole reason `account` is on every row.
    """
    conn = db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO accounts (email, label, purpose, shared_with,"
            " added_at) VALUES (?,?,?,?,?)",
            (email, label, purpose, shared_with, time.time()))
    print(f"  {email}  label={label!r}  purpose={purpose!r}")


def list_accounts():
    conn = db()
    rows = list(conn.execute("SELECT * FROM accounts ORDER BY email"))
    if not rows:
        print("  no accounts registered")
        return
    for r in rows:
        d = dict(r)
        n = conn.execute("SELECT COUNT(*) c FROM messages WHERE account=?",
                         (d["email"],)).fetchone()["c"]
        print(f"  {d['email']:<32} {d['label']:<10} {d['purpose'][:40]:<40} {n:>7} messages")


# ---------------------------------------------------------------- api
class Throttle(Exception):
    pass


class TokenSource:
    """An access token that renews itself.

    Access tokens last about an hour. A crawl of 100,000 messages lasts nine. Fetching one
    token at the start and using it throughout worked perfectly for the first fifty
    minutes and then every single request failed — which is exactly how the first full run
    died, at 3,600 messages, with one `UNAUTHENTICATED` in the log and no other symptom.

    Renewing proactively (well inside the hour) and again on any 401 means a long crawl
    cannot be outlived by its own credentials.
    """

    REFRESH_AFTER = 2700          # 45 minutes, comfortably inside the ~60-minute lifetime

    def __init__(self, email: str):
        self.email = email
        self._token = ""
        self._fetched = 0.0
        self.renewals = 0

    def get(self, force: bool = False) -> str:
        if force or not self._token or (time.time() - self._fetched) > self.REFRESH_AFTER:
            self._token = auth.access_token(self.email)
            self._fetched = time.time()
            self.renewals += 1
        return self._token


def api_get(token, path: str, params: dict | None = None, tries: int = 8):
    """GET, paced and with honest backoff. `token` is a TokenSource, or a plain string for
    the short-lived callers that do not need renewal."""
    time.sleep(REQUEST_DELAY)
    url = API + path
    if params:
        # doseq matters: without it urlencode renders a LIST as its repr, so
        # metadataHeaders=["To","Cc"] became the literal string "['To', 'Cc']", Gmail
        # ignored it, no headers came back, and the reply graph came out empty. An empty
        # reply graph is not an error — it reads as "you have never replied to anyone",
        # which would have marked every sender as never-replied, including the bank.
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    delay = 1.0
    for attempt in range(tries):
        value = token.get() if hasattr(token, "get") else token
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {value}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            body = exc.read(400).decode("utf-8", "replace")
            # An expired token is not a failure, it is maintenance. Renew and retry
            # immediately rather than counting it against the attempt budget.
            if exc.code == 401 or "UNAUTHENTICATED" in body:
                if hasattr(token, "get"):
                    print("    token expired — renewing", flush=True)
                    token.get(force=True)
                    continue
            if exc.code in (403, 429, 500, 502, 503):
                # 403 from Gmail is usually per-user rate limiting, not a permission
                # problem — a scope error would have failed at the very first call.
                if attempt == tries - 1:
                    raise Throttle(
                        f"giving up after {tries} tries: HTTP {exc.code}\n"
                        f"      response: {body[:300]}")
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            detail = body
            raise SystemExit(f"{exc.code} on {path}: {detail}")
    raise Throttle("exhausted retries")


# ---------------------------------------------------------------- body extraction
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n{3,}")


def strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return _WS.sub("\n\n", s).strip()


def body_of(payload: dict) -> tuple[str, bool]:
    """(text, has_attachment). Prefers text/plain; falls back to stripped HTML.

    Many business mail shots are HTML-only, and a message with no readable text is
    invisible to search — so the HTML fallback is not a nicety.
    """
    plain, rich, has_att = [], [], False
    stack = [payload]
    while stack:
        part = stack.pop()
        if not part:
            continue
        if part.get("parts"):
            stack.extend(part["parts"])
        mime = part.get("mimeType") or ""
        filename = part.get("filename") or ""
        if filename:
            has_att = True
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        try:
            import base64
            text = base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
        except Exception:                                   # noqa: BLE001
            continue
        if mime == "text/plain":
            plain.append(text)
        elif mime == "text/html":
            rich.append(strip_html(text))
    if plain:
        return "\n".join(plain).strip(), has_att
    return "\n".join(rich).strip(), has_att


def header(msg: dict, name: str) -> str:
    for h in (msg.get("payload", {}).get("headers") or []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


# ---------------------------------------------------------------- engagement
def emailed_addresses(token: str, limit: int = 20000, resume_token: str | None = None,
                      seed: set | None = None, checkpoint=None) -> set[str]:
    """Every address the owner has ever SENT to.

    One pass over the Sent folder, and it is what makes `replied` real rather than
    guessed. A sender he has never written to in five years is a different kind of
    sender from one he corresponds with — and only he can say which of those matter.
    """
    seen: set[str] = set(seed or ())
    page = resume_token
    fetched = 0
    while fetched < limit:
        # `in:sent` is the whole point of this pass and was once missing — without it the
        # query walks the ENTIRE mailbox newest-first and harvests To/Cc from mail the
        # owner RECEIVED, so every sender he had ever been emailed BY would be marked as
        # someone he had replied to. That inverts the single most useful junk signal we
        # have, and it would look perfectly plausible in the report.
        params = {"maxResults": PAGE, "q": "in:sent -in:chats"}
        if page:
            params["pageToken"] = page
        data = api_get(token, "/messages", params)
        if not data:
            break
        for m in data.get("messages", []):
            fetched += 1
            if fetched >= limit:
                break
            full = api_get(token, f"/messages/{m['id']}",
                           {"format": "metadata", "metadataHeaders": ["To", "Cc"]})
            if not full:
                continue
            for hname in ("To", "Cc"):
                for addr in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", header(full, hname)):
                    seen.add(addr.lower())
            # Progress, because this pass is the slow half and runs BEFORE any message is
            # indexed. Without it a multi-hour run looks indistinguishable from a hung
            # one, and the natural reaction is to kill and restart it — which is the worst
            # possible response, since this phase has no checkpoint to resume from.
            if fetched % 500 == 0:
                print(f"    {fetched} sent messages scanned, {len(seen)} addresses so far",
                      flush=True)
                # Checkpoint: losing two hours of scanning to a crash at the save step is
                # a mistake worth only making once.
                if checkpoint:
                    checkpoint(seen, page)
        page = data.get("nextPageToken")
        if not page:
            break
    return seen


# ---------------------------------------------------------------- reply graph cache
def load_reply_graph(conn, email: str, max_age_days: int = 30):
    """The COMPLETE cached set of addresses the owner has sent to, or None.

    Only a finished scan counts as a cache. A partial one is resume material, not an
    answer — treating it as complete would under-mark `replied`, which is the signal the
    whole junk report rests on, and it would look like a finding rather than a truncation.
    """
    # A COMPLETE scan has NO cursor — that is what complete means, there is nothing left
    # to resume. This guard used to require one, so a finished cache was rejected as
    # invalid and the whole 20,000-message scan ran again on every restart. `last_ok` is
    # the completion marker; the cursor marks an interrupted scan, which is resume
    # material rather than a cache (see load_partial_graph).
    row = conn.execute("SELECT cursor, last_ok FROM sync_state WHERE account=? AND"
                       " stream='replygraph'", (email,)).fetchone()
    if not row or not row["last_ok"]:
        return None
    if (time.time() - row["last_ok"]) > max_age_days * 86400:
        return None
    return {r["address"] for r in conn.execute(
        "SELECT address FROM replied_addresses WHERE account=?", (email,))}


def load_partial_graph(conn, email: str):
    """(page_token, addresses_so_far) from an interrupted scan, for resuming it.

    The scan takes ~2.5 hours and has been lost twice — once to an expired token, once to
    a lock at the final save. Both times it started from nothing. A scan that can be
    continued is worth more than one that is merely fast.
    """
    row = conn.execute("SELECT cursor FROM sync_state WHERE account=? AND"
                       " stream='replygraph'", (email,)).fetchone()
    token = row["cursor"] if row else None
    if not token:
        return None, set()
    addrs = {r["address"] for r in conn.execute(
        "SELECT address FROM replied_addresses WHERE account=?", (email,))}
    return token, addrs


def save_reply_graph(conn, email: str, addresses, next_token: str | None,
                     complete: bool = False):
    """Persist progress. `complete` is what distinguishes a cache from a checkpoint."""
    with conn:
        conn.execute("DELETE FROM replied_addresses WHERE account=?", (email,))
        conn.executemany(
            "INSERT OR REPLACE INTO replied_addresses (account, address, scanned_at)"
            " VALUES (?,?,?)", [(email, a, 0.0) for a in addresses])
        conn.execute(
            "INSERT INTO sync_state (account, stream, cursor, last_run, last_ok, note)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(account, stream) DO UPDATE SET cursor=excluded.cursor,"
            " last_run=excluded.last_run, last_ok=excluded.last_ok, note=excluded.note",
            (email, "replygraph", None if complete else next_token, time.time(),
             time.time() if complete else None,
             f"{len(addresses)} addresses" + ("" if complete else " (partial)")))


# ---------------------------------------------------------------- crawl
def index_account(email: str, max_messages: int | None = None, page_limit: int = 2000):
    conn = db()

    if control.is_paused(conn, "gmail"):
        print("  gmail indexing is PAUSED — nothing will be written.")
        print("  resume with:  python3 control.py resume gmail")
        return 0

    token = TokenSource(email)

    row = conn.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
    if not row:
        raise SystemExit(f"{email} is not in the accounts table — add it first")

    # One pass over Sent, and it is the expensive half of a run — so it is capped, and
    # the cap is adjustable. On a validation run you want a few hundred, not twenty
    # thousand; on the real crawl it is worth paying once because it never changes.
    # The reply graph takes ~50 minutes at 20k sent messages and used to be thrown away on
    # every restart — so a crawl that died at hour three paid that cost again. It barely
    # changes, so it is now cached and only rebuilt when it is stale.
    replied_to = load_reply_graph(conn, email, max_age_days=30)
    if replied_to is None:
        sent_limit = int(os.getenv("JARVIS_SENT_SCAN_LIMIT", "20000"))
        resume_at, already = load_partial_graph(conn, email)
        if resume_at:
            print(f"reply graph: RESUMING an interrupted scan "
                  f"({len(already)} addresses already found)")
        else:
            print(f"building the reply graph for {email} (up to {sent_limit} sent"
                  f" messages — checkpointed as it goes, cached when done)...")
        replied_to = emailed_addresses(
            token, limit=sent_limit, resume_token=resume_at, seed=already,
            checkpoint=lambda s, p: save_reply_graph(conn, email, s, p, complete=False))
        save_reply_graph(conn, email, replied_to, None, complete=True)
        print(f"  owner has written to {len(replied_to)} addresses (cached)")
    else:
        print(f"reply graph: {len(replied_to)} addresses (cached — skipping the scan)")

    # Newest first: if this is interrupted — and it will be — the part that exists is
    # the part he is most likely to ask about.
    state = conn.execute("SELECT * FROM sync_state WHERE account=? AND stream='gmail'",
                         (email,)).fetchone()
    page = state["cursor"] if state and state["cursor"] else None

    seen = 0
    skipped = 0
    pages = 0
    failures: list = []                 # (message_id, reason) — never silently dropped
    while pages < page_limit:
        # Re-checked every page, not once at the start: a crawl runs for hours, and both
        # a pause and a new exclusion are things you reach for when you have just decided
        # something should not be indexed. They have to take effect now, not tomorrow.
        if control.is_paused(conn, "gmail"):
            print("  PAUSED mid-crawl — stopping here. The cursor is saved, so resuming"
                  " continues from this page rather than starting over.")
            break
        exclusions = load_exclusions(conn, "gmail")

        params = {"maxResults": PAGE}
        if page:
            params["pageToken"] = page
        data = api_get(token, "/messages", params)
        ids = (data or {}).get("messages", [])
        if not ids:
            break

        # FETCH IN PARALLEL, STORE IN ONE THREAD.
        #
        # The network wait is about a third of the per-message cost, so overlapping it is
        # where the speed comes from. Writing stays single-threaded because SQLite takes
        # one writer and a connection cannot be shared across threads.
        #
        # The page token is still the checkpoint, and it only advances once the whole page
        # is stored — so a crash loses at most one page, and re-fetching a page is safe
        # (every write is INSERT OR REPLACE). A message that fails to fetch is recorded
        # by id rather than silently skipped, which is the thing that went wrong every
        # other time today.
        fetched: dict = {}
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(api_get, token, f"/messages/{m['id']}",
                                   {"format": "full"}): m["id"] for m in ids}
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    got = fut.result()
                except Exception as exc:                     # noqa: BLE001
                    got = None
                    failures.append((mid, f"{type(exc).__name__}: {exc}"[:200]))
                if got is None:
                    if not any(mid == f[0] for f in failures):
                        failures.append((mid, "empty response"))
                else:
                    fetched[mid] = got

        for m in ids:                       # store in page order, not completion order
            full = fetched.get(m["id"])
            if not full:
                continue
            if store_message(conn, email, full, replied_to, exclusions):
                skipped += 1
            seen += 1
            if max_messages and seen >= max_messages:
                break
            if seen % 25 == 0:
                conn.commit()
            if seen % 200 == 0:
                print(f"    {seen} messages...")
        conn.commit()

        page = (data or {}).get("nextPageToken")
        conn.commit()
        with conn:
            conn.execute(
                "INSERT INTO sync_state (account, stream, cursor, last_run, last_ok, note)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(account, stream) DO UPDATE SET"
                " cursor=excluded.cursor, last_run=excluded.last_run,"
                " last_ok=excluded.last_ok, note=excluded.note",
                (email, "gmail", page, time.time(), time.time(),
                 f"{seen} indexed this run"))
        pages += 1
        print(f"  page {pages}: {seen} messages indexed"
              + ("" if page else "  — reached the end of the mailbox"))
        if not page or (max_messages and seen >= max_messages):
            break

    # Failures are written down, not swallowed. Every silent-empty bug today came from a
    # failure that looked like a legitimate answer; a message that could not be fetched is
    # a message we know by id and can retry, which is a different and much smaller problem.
    if failures:
        try:
            with open(FAILURE_LOG, "a") as fh:
                for mid, why in failures:
                    fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{mid}\t{why}\n")
        except OSError as exc:
            print(f"  (could not write {FAILURE_LOG}: {exc})")
        print(f"  {len(failures)} messages could NOT be fetched — listed in {FAILURE_LOG}")
        for mid, why in failures[:5]:
            print(f"    {mid}: {why}")

    print(f"done: {seen} messages this run"
          + (f", {skipped} skipped by your exclusions" if skipped else "")
          + (f", {len(failures)} fetch failures (re-run retries them)" if failures else ""))
    return seen


def store_message(conn, email: str, msg: dict, replied_to: set[str],
                  exclusions=()) -> bool:
    """Store one message. Returns True if it was skipped by an owner exclusion."""
    labels = list(msg.get("labelIds") or [])
    from_addr = header(msg, "From")
    addr = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", from_addr)
    addr = addr.group(0).lower() if addr else from_addr.lower()

    text, has_att = body_of(msg.get("payload") or {})

    # The owner's rules are checked BEFORE anything is written, so an excluded message is
    # never in the database even briefly. Purging afterwards would leave it in the FTS
    # index for as long as the purge took, and would need doing again on every re-crawl.
    if is_excluded(exclusions, addr, header(msg, "Subject"), text):
        return True

    light = bool(set(labels) & LIGHT_ONLY)

    conn.execute(
        "INSERT OR REPLACE INTO messages (id, account, thread_id, internal_date,"
        " from_addr, to_addrs, subject, labels, snippet, has_attachment, size_estimate,"
        " replied, unread, status, indexed_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (msg["id"], email, msg.get("threadId"),
         int(msg.get("internalDate") or 0),
         addr, json.dumps([header(msg, "To")]),
         header(msg, "Subject"), json.dumps(labels),
         msg.get("snippet", ""), 1 if has_att else 0,
         int(msg.get("sizeEstimate") or 0),
         1 if addr in replied_to else 0,
         1 if "UNREAD" in labels else 0,
         "active", time.time()))

    if not light and text:
        conn.execute("INSERT OR REPLACE INTO message_text (message_id, body) VALUES (?,?)",
                     (msg["id"], text))
        conn.execute("DELETE FROM message_fts WHERE message_id=?", (msg["id"],))
        conn.execute(
            "INSERT INTO message_fts (message_id, account, subject, sender, body)"
            " VALUES (?,?,?,?,?)",
            (msg["id"], email, header(msg, "Subject"), from_addr, text[:200000]))

    for part in walk_parts(msg.get("payload") or {}):
        if not part.get("filename"):
            continue
        import hashlib
        key = hashlib.sha256(
            f"{msg['id']}:{part['filename']}:{part.get('body', {}).get('size', 0)}"
            .encode()).hexdigest()[:32]
        # remote_id is Gmail's attachment id. Without it the row is a description of a
        # file with no way to fetch it — the metadata that made this pass possible later
        # would have been metadata we could not act on.
        #
        # `inline` matters as much as the size: most images in email are signature logos,
        # tracking pixels and social icons. They are not content, and without this the
        # attachment pass would download and OCR them by the thousand.
        disposition = ""
        for h in (part.get("headers") or []):
            if h.get("name", "").lower() == "content-disposition":
                disposition = h.get("value", "")
        conn.execute(
            "INSERT OR REPLACE INTO attachments (id, message_id, account, filename,"
            " mime_type, size, remote_id, inline) VALUES (?,?,?,?,?,?,?,?)",
            (key, msg["id"], email, part["filename"], part.get("mimeType"),
             int(part.get("body", {}).get("size") or 0),
             (part.get("body") or {}).get("attachmentId"),
             1 if disposition.lower().startswith("inline") else 0))
    return False


def walk_parts(payload: dict):
    stack = [payload]
    while stack:
        p = stack.pop()
        if not p:
            continue
        yield p
        stack.extend(p.get("parts") or [])


# ---------------------------------------------------------------- the junk report
def junk_report(email: str, min_messages: int = 20, limit: int = 25):
    """Senders that look like junk — as a LIST for the owner to judge, never an action.

    "Never replied in five years" alone would delete his bank, his children's school and
    his doctor, because those are exactly the senders nobody replies to. So this is a
    ranking with the evidence shown, and he decides.
    """
    conn = db()
    rows = conn.execute("""
        SELECT from_addr,
               COUNT(*)                                   AS n,
               SUM(CASE WHEN replied=1 THEN 1 ELSE 0 END)  AS replied_n,
               SUM(CASE WHEN unread=1  THEN 1 ELSE 0 END)  AS unread_n,
               MAX(internal_date)                          AS newest,
               MIN(internal_date)                          AS oldest,
               SUM(has_attachment)                         AS with_files
        FROM messages
        WHERE account=? AND status='active'
        GROUP BY from_addr
        HAVING n >= ?
        ORDER BY n DESC
        LIMIT ?
    """, (email, min_messages, limit)).fetchall()

    print(f"\nsenders by volume — {email}")
    print("(candidates for demotion; nothing is changed by this command)\n")
    print(f"  {'messages':>8} {'replied':>7} {'unread':>6} {'files':>5}  sender")
    print("  " + "-" * 74)
    for r in rows:
        d = dict(r)
        newest = time.strftime("%Y-%m", time.localtime((d["newest"] or 0) / 1000))
        print(f"  {d['n']:>8} {d['replied_n'] or 0:>7} {d['unread_n'] or 0:>6} "
              f"{d['with_files'] or 0:>5}  {d['from_addr'][:44]:<44} last {newest}")
    print("\n  A sender with many messages, zero replies and zero attachments is the "
          "shape of junk.\n  One with zero replies and attachments may be a bank, a "
          "school or a doctor — check before demoting.")
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- cli
def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "init":
        init()
        return 0
    if cmd == "add-account":
        if len(argv) < 4:
            raise SystemExit("usage: gmail_index.py add-account <email> <label> [purpose]")
        add_account(argv[2], argv[3], argv[4] if len(argv) > 4 else "")
        return 0
    if cmd == "accounts":
        list_accounts()
        return 0
    if cmd == "run":
        if len(argv) < 3:
            raise SystemExit("usage: gmail_index.py run <email> [max_messages]")
        cap = int(argv[3]) if len(argv) > 3 else None
        index_account(argv[2], max_messages=cap)
        return 0
    if cmd == "junk":
        if len(argv) < 3:
            raise SystemExit("usage: gmail_index.py junk <email>")
        junk_report(argv[2])
        return 0
    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
