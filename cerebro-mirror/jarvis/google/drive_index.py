"""Index a Drive account into the local database. READ-ONLY against Google.

The owner's Drives are "many GB" each. That is fine, because we do not mirror them:
stream a file, extract its text, keep the text, drop the binary. A multi-GB Drive becomes
a few hundred MB of searchable words, and the originals stay in Google where they
already live.

Two things do most of the work of turning "many GB" into something readable:

  * MIME FILTERING. Video, audio and archives are the overwhelming majority of the
    bytes and none of the meaning. They get a metadata row and no download.
  * NEWEST FIRST. If the crawl is interrupted — it will be — the part that exists is the
    part he is most likely to ask about.

Google-native files (Docs/Sheets/Slides) are never "downloaded" at all; they are
exported as text. They are usually the most valuable things in a Drive and they cost
almost nothing to index.

Usage:
    python3 drive_index.py run mnmeyer@gmail.com [max_files]
    python3 drive_index.py stats mnmeyer@gmail.com
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth         # noqa: E402
import ocr_ladder   # noqa: E402

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))
API = "https://www.googleapis.com/drive/v3"
PAGE = 200

# What we can actually turn into words. Everything else gets a metadata row and is never
# fetched — which is where the terabytes would otherwise go.
EXPORTS = {
    "application/vnd.google-apps.document": ("text/plain", "export:text"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", "export:csv"),
    "application/vnd.google-apps.presentation": ("text/plain", "export:text"),
    "application/vnd.google-apps.script": ("application/vnd.google-apps.script+json",
                                           "export:json"),
}
DOWNLOADABLE = {
    "application/pdf": "download:pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        "download:docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        "download:xlsx",
    "text/plain": "download:text",
    "text/markdown": "download:text",
    "text/csv": "download:text",
    # HTML and EPUB were being skipped as "unknown MIME", which is a different thing from
    # "media with no text in it". There are 117 HTML and 309 EPUB files in this Drive —
    # both are pure text and both cost nothing to read.
    "text/html": "download:html",
    "application/epub+zip": "download:epub",
}

# SCANS ARE RECORDS. `image/` used to sit in the exclusion list below, which meant 1,037
# JPEG and PNG files were given a metadata row and never looked inside — on a Drive whose
# service records include a scanned DA photo and whole packets photographed page by page.
# A photograph of a page of a document is a page of a document.
#
# The cost is real and worth naming: OCR is the slowest thing here by an order of
# magnitude, and the Drive has more images than PDFs. That is why the catch-up pass is
# its own resumable command (`documents`) rather than part of the incremental listing
# walk — see the module docstring.
IMAGE_PREFIX = "image/"

# Deliberately excluded, and this list is the whole reason a "many GB" Drive is tractable.
# 743 audio files, and the archives, are the bulk of the bytes and none of the meaning.
SKIP_PREFIXES = ("video/", "audio/", "application/zip", "application/x-tar",
                 "application/x-7z", "application/vnd.rar", "application/x-rar")


def db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=180)
    conn.row_factory = sqlite3.Row
    # Patience: the mail crawler is writing to this database at the same time and holds
    # the write lock for a whole page. A 30s timeout loses that race repeatedly and reads
    # like a bug in this crawler rather than a busy neighbour.
    conn.execute("PRAGMA busy_timeout = 180000")
    return conn


# ---------------------------------------------------------------- api
def api_get(token, path: str, params: dict | None = None, raw: bool = False,
            tries: int = 6):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    delay = 1.0
    for attempt in range(tries):
        value = token.get() if hasattr(token, "get") else token
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {value}"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read() if raw else json.loads(r.read())
        except urllib.error.HTTPError as exc:
            # 403 here is per-user rate limiting far more often than a permission
            # problem; a real scope failure would have failed on the first call.
            if exc.code in (403, 429, 500, 502, 503) and attempt < tries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            detail = exc.read(300).decode("utf-8", "replace")
            if raw:
                raise SystemExit(f"{exc.code} on {path}: {detail}")
            return None
    return None


# ---------------------------------------------------------------- text extraction
# The ladder lives in ocr_ladder.py, not here. This module used to carry its own copy of
# it, and attachment_index.py carried a third — which is how the same bug (a missing
# binary collapsing into "this document has no text") came to exist in two places with
# two different explanations. One ladder, many callers.
#
# It returns THREE values now, and the third is the point:
#
#     (text, source, error)   error is None  -> we looked, nothing is there
#                             error is set   -> we could not look, and this is why
#
# The caller stores that third value on the row (see `text_state` in schema.sql), so a
# Drive full of unreadable scans can never again be mistaken for a Drive with nothing in
# it. That mistake is the reason this module was rewritten.


def extract(token: str, file_id: str, mime: str,
            max_pages: int | None = None) -> tuple[str, str, str | None]:
    """(text, source, error) for one Drive file.

    Empty text with `error is None` means the file was read and holds no text. Empty text
    with an error means we could not read it — and the caller records which, because the
    two used to be indistinguishable and that is how a Drive came to look empty.
    """
    if mime in EXPORTS:
        export_as, source = EXPORTS[mime]
        data = api_get(token, f"/files/{file_id}/export",
                       {"mimeType": export_as}, raw=True)
        if not data:
            # Google refused or the file is not exportable. Not "empty": unread.
            return "", "", "unavailable:export"
        return data.decode("utf-8", "replace").strip(), source, None

    if mime in DOWNLOADABLE or mime.startswith(IMAGE_PREFIX):
        data = api_get(token, f"/files/{file_id}", {"alt": "media"}, raw=True)
        if not data:
            return "", "", "unavailable:download"
        # The ladder decides: text layer, else rasterise and OCR, else frames for a
        # multi-frame TIFF. This module no longer owns any of that.
        return ocr_ladder.extract_bytes(data, mime, file_id, max_pages=max_pages)

    # A type we never fetch. The caller stores "skipped" rather than "empty" — the row
    # is honest about being a directory entry and nothing more.
    return "", "", None


def indexable(mime: str) -> int:
    """Can this MIME be turned into words? `SKIP_PREFIXES` is checked first so a
    surprising subtype inside a skipped family (image/svg inside nothing, say) cannot
    slip past on the image branch below."""
    if mime.startswith(SKIP_PREFIXES):
        return 0
    if mime in EXPORTS or mime in DOWNLOADABLE:
        return 1
    if mime.startswith(IMAGE_PREFIX):
        return 1
    return 0


# ---------------------------------------------------------------- crawl
def index_account(email: str, max_files: int | None = None, page_limit: int = 200):
    conn = db()
    token = auth.TokenSource(email)

    if not conn.execute("SELECT 1 FROM accounts WHERE email=?", (email,)).fetchone():
        raise SystemExit(f"{email} is not in the accounts table — add it first")

    state = conn.execute("SELECT * FROM sync_state WHERE account=? AND stream='drive'",
                         (email,)).fetchone()
    page = state["cursor"] if state and state["cursor"] else None

    seen = indexed = skipped = 0
    pages = 0
    while pages < page_limit:
        params = {
            # Newest first, and only what he owns or has had shared INTO the account —
            # the general "shared with me" firehose is mostly other people's noise.
            "q": "trashed=false",
            "orderBy": "modifiedTime desc",
            "pageSize": PAGE,
            "fields": ("nextPageToken,files(id,name,mimeType,modifiedTime,createdTime,"
                       "size,owners(emailAddress),trashed)"),
        }
        if page:
            params["pageToken"] = page

        data = api_get(token, "/files", params)
        if not data:
            print("  (no response — stopping here, progress is checkpointed)")
            break

        for f in data.get("files", []):
            mime = f.get("mimeType") or ""
            can = bool(indexable(mime))
            # EXTRACT FIRST, WITH NO TRANSACTION OPEN.
            #
            # This used to insert the file row and then extract inside the same
            # transaction — so a download plus a PDF OCR pass ran while HOLDING the
            # database's write lock. Some documents take a minute to read, and no amount of
            # `busy_timeout` on the other side survives a writer that holds the lock for
            # minutes at a time. That is what killed the mail crawler twice, at the same
            # step, and it looked like two unrelated causes both times.
            #
            # The slow work belongs outside the transaction. Two inserts then take
            # microseconds, and the lock is a shared resource held briefly rather than a
            # private one held for the length of a download.
            text, source, error = ("", "", None)
            if can:
                text, source, error = extract(token, f["id"], mime)

            # Three outcomes, and they are recorded differently on purpose:
            #   ok                    we read it and there was text
            #   empty                 we read it and there genuinely was none
            #   unavailable:<why>     we could not read it — the tool was missing
            # Only the middle one is an answer. Reporting the third as the second is the
            # bug this whole change exists to kill.
            if not can:
                state = "skipped"
            elif error:
                state = error
            elif text:
                state = "ok"
            else:
                state = "empty"

            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO drive_files (id, account, name, mime_type,"
                    " modified_time, created_time, size, owners, trashed, indexable,"
                    " source, text_state, indexed_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f["id"], email, f.get("name"), mime, f.get("modifiedTime"),
                     f.get("createdTime"), int(f.get("size") or 0),
                     json.dumps([o.get("emailAddress") for o in (f.get("owners") or [])]),
                     0, 1 if can else 0, "drive", state, time.time()))
                if can and text:
                    conn.execute(
                        "INSERT OR REPLACE INTO document_text (file_id, account, body,"
                        " source) VALUES (?,?,?,?)", (f["id"], email, text, source))
                    conn.execute("DELETE FROM document_fts WHERE file_id=?", (f["id"],))
                    conn.execute(
                        "INSERT INTO document_fts (file_id, account, name, body)"
                        " VALUES (?,?,?,?)",
                        (f["id"], email, f.get("name"), text[:400000]))

            seen += 1
            if can and text:
                indexed += 1
            else:
                skipped += 1

            if max_files and seen >= max_files:
                break
            if seen % 100 == 0:
                print(f"    {seen} files, {indexed} with text...", flush=True)

        page = data.get("nextPageToken")
        conn.commit()
        with conn:
            conn.execute(
                "INSERT INTO sync_state (account, stream, cursor, last_run, last_ok, note)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(account, stream) DO UPDATE SET cursor=excluded.cursor,"
                " last_run=excluded.last_run, last_ok=excluded.last_ok,"
                " note=excluded.note",
                (email, "drive", page, time.time(), time.time(),
                 f"{indexed} indexed / {seen} seen this run"))
        pages += 1
        print(f"  page {pages}: {seen} files seen, {indexed} with text"
              + ("" if page else "  — reached the end of the Drive"))
        if not page or (max_files and seen >= max_files):
            break

    print(f"\ndone: {seen} files seen, {indexed} indexed, {skipped} without text")
    return seen


def coverage(conn, email: str | None = None, source: str | None = None) -> dict:
    """How much of what we hold can actually be READ. Shared by `stats` and /health.

    This is the number that was invisible for weeks. "7,385 files" reads like coverage;
    the honest figure is the `unread` and `unavailable` lines below it, and the whole
    point of `text_state` is that they can be printed at all.
    """
    where, args = [], []
    if email:
        where.append("account = ?")
        args.append(email)
    if source:
        where.append("source = ?")
        args.append(source)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    row = conn.execute(f"""
        SELECT COUNT(*) n,
               SUM(CASE WHEN text_state = 'ok'      THEN 1 ELSE 0 END) read,
               SUM(CASE WHEN text_state = 'empty'   THEN 1 ELSE 0 END) empty,
               SUM(CASE WHEN text_state IS NULL     THEN 1 ELSE 0 END) untouched,
               SUM(CASE WHEN text_state = 'skipped' THEN 1 ELSE 0 END) skipped,
               SUM(CASE WHEN text_state LIKE 'unavailable%' OR text_state LIKE '%:%'
                        THEN 1 ELSE 0 END) unavailable
        FROM drive_files{clause}
    """, args).fetchone()
    d = {k: (row[k] or 0) for k in row.keys()}
    # `ok` counts rows we read text from; document_text is the authority on that, since a
    # row can be marked ok and then have its text pruned. Both are reported.
    d["with_text"] = conn.execute(
        "SELECT COUNT(*) FROM document_text d JOIN drive_files f ON f.id = d.file_id"
        + (clause.replace("account", "f.account").replace("source", "f.source")
           if clause else ""), args).fetchone()[0]
    d["unread"] = d["n"] - d["with_text"]
    return d


def stats(email: str):
    conn = db()
    d = coverage(conn, email)
    if not d["n"]:
        print(f"  {email}: nothing indexed yet")
        return
    rng = conn.execute("SELECT MIN(modified_time), MAX(modified_time) FROM drive_files"
                       " WHERE account=?", (email,)).fetchone()
    print(f"  {email}")
    print(f"    rows           : {d['n']}")
    print(f"    with text      : {d['with_text']}")
    print(f"    unread         : {d['unread']}")
    print(f"      not attempted: {d['untouched']}")
    print(f"      no text      : {d['empty']}")
    print(f"      unavailable  : {d['unavailable']}   <- a missing tool, not an empty file")
    print(f"      not fetched  : {d['skipped']}   (media, archives)")
    print(f"    modified range : {(rng[0] or '')[:10]} .. {(rng[1] or '')[:10]}")
    # Which MIME types make up the unread pile — the actionable half. "2,715 unread" is a
    # worry; "2,713 of them PDFs, and poppler is missing" is a fix.
    rows = conn.execute("""
        SELECT f.mime_type, COUNT(*) n FROM drive_files f
        LEFT JOIN document_text d ON d.file_id = f.id
        WHERE f.account = ? AND d.file_id IS NULL AND f.text_state != 'skipped'
        GROUP BY f.mime_type ORDER BY n DESC LIMIT 8
    """, (email,)).fetchall()
    for r in rows:
        print(f"      {r['n']:>6}  {r['mime_type']}")
    src = conn.execute("""SELECT source, COUNT(*) n FROM drive_files WHERE account=?
                          GROUP BY source ORDER BY n DESC""", (email,)).fetchall()
    if len(src) > 1:
        print("    by source      : " + ", ".join(f"{r['source']}={r['n']}" for r in src))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "run":
        if len(argv) < 3:
            raise SystemExit("usage: drive_index.py run <email> [max_files]")
        cap = int(argv[3]) if len(argv) > 3 else None
        index_account(argv[2], max_files=cap)
        return 0
    if cmd == "stats":
        if len(argv) < 3:
            raise SystemExit("usage: drive_index.py stats <email>")
        stats(argv[2])
        return 0
    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
