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

# The fleet watchdog flag. Reading 2,713 PDFs is many hours of CPU, and this fleet's rule
# is that heavy jobs yield between batches rather than competing with whatever else the
# machine has been asked to do.
SUSPEND = os.getenv("JARVIS_SUSPEND_FILE", "/tmp/cinematome-suspend")

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

            # THIS WALK NO LONGER READS CONTENTS, and that is a correction rather than a
            # simplification. Reading a file here meant re-downloading every indexable
            # file on every pass — there is no early exit on known ids — which is the
            # whole reason `documents` exists as a separate, database-driven pass. Doing
            # both would download all 7,385 files twice and, worse, this walk would
            # OVERWRITE a good `text_state` with `unavailable:pdftotext` on a host with no
            # poppler. The listing walk had no way to know it was downgrading a file that
            # had already been read.
            #
            # So: metadata here, contents in `documents`. A new row gets text_state NULL
            # (meaning "not attempted yet") if it is readable and 'skipped' if it is not,
            # and `text_state = COALESCE(drive_files.text_state, excluded.text_state)`
            # below means an existing state — ok, empty, or a reason — is never replaced.
            state = None if can else "skipped"

            with conn:
                conn.execute(
                    "INSERT INTO drive_files (id, account, name, mime_type,"
                    " modified_time, created_time, size, owners, trashed, indexable,"
                    " source, text_state, indexed_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET"
                    "   account=excluded.account, name=excluded.name,"
                    "   mime_type=excluded.mime_type,"
                    "   modified_time=excluded.modified_time,"
                    "   created_time=excluded.created_time, size=excluded.size,"
                    "   owners=excluded.owners, trashed=excluded.trashed,"
                    "   indexable=excluded.indexable,"
                    "   text_state=COALESCE(drive_files.text_state, excluded.text_state),"
                    "   indexed_at=excluded.indexed_at",
                    (f["id"], email, f.get("name"), mime, f.get("modifiedTime"),
                     f.get("createdTime"), int(f.get("size") or 0),
                     json.dumps([o.get("emailAddress") for o in (f.get("owners") or [])]),
                     0, 1 if can else 0, "drive", state, time.time()))

            seen += 1
            if can:
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


def documents(limit: int | None = None, batch: int = 40, account: str | None = None,
              max_pages: int | None = None):
    """Read the CONTENTS of files whose text we never got.

    A SEPARATE PASS FROM index_account(), deliberately. That walks the Drive listing
    newest-first and re-downloads every file it already holds — there is no early exit on
    known ids — so folding OCR into it would re-fetch 7,385 files in order to read 2,713.
    This selects from the DATABASE instead: resumable by construction, and it never
    downloads a file whose text it already has.

    It picks up rows in `text_state IS NULL` (never attempted) and any row recorded as
    `unavailable:*` (attempted, but the tool was missing). It does NOT re-read rows marked
    `ok` or `empty` — those two are answers, and re-reading them every run is how a
    catch-up pass becomes a nightly re-crawl.

    Extraction happens BEFORE the transaction opens, in batches. The two crawler deaths
    documented in index_account() were both a download-and-OCR running while holding the
    write lock; `busy_timeout` on the other side cannot survive a writer that holds it for
    minutes at a time.
    """
    conn = db()
    # ANYTHING THAT IS NOT A FINISHED ANSWER IS WORTH ANOTHER TRY. This selected only
    # `text_state IS NULL OR LIKE 'unavailable%'`, which silently excluded every failure
    # it had recorded as `timeout:<tool>` or `failed:<tool>` — so a file that timed out
    # once was never retried, while the message printed at the end of the run promised
    # that "unreadable files ... will be retried". It was true for some of them.
    #
    # 'ok' and 'empty' are answers and are left alone; 'skipped' is a decision about the
    # file type. Everything else is an unfinished attempt.
    where = ("indexable = 1 AND trashed = 0 AND status = 'active'"
             " AND (text_state IS NULL OR text_state NOT IN ('ok', 'empty', 'skipped'))")
    args: list = []
    if account:
        where += " AND account = ?"
        args.append(account)

    total = conn.execute(f"SELECT COUNT(*) c FROM drive_files WHERE {where}",
                         args).fetchone()["c"]
    if not total:
        print("  nothing unread — every indexable file has been attempted")
        return 0
    # Flushed immediately. Without it the first line of an hours-long job sits in the
    # stdout buffer behind a batch of scanned PDFs, and an empty log is indistinguishable
    # from a crawl that died on start — which is exactly what it looked like.
    print(f"  {total} file(s) to read", flush=True)

    tokens: dict[str, auth.TokenSource] = {}
    read = empty = unavailable = 0
    done = 0
    import shutil as _shutil
    import tempfile as _tempfile

    while True:
        if os.path.exists(SUSPEND):
            print(f"  SUSPENDED — {SUSPEND} exists. Re-run to continue;"
                  " finished files are already written.")
            break
        want = min(batch, limit - done) if limit else batch
        if want <= 0:
            break
        rows = conn.execute(
            f"SELECT id, account, name, mime_type, size FROM drive_files WHERE {where}"
            f" ORDER BY modified_time DESC LIMIT ?", args + [want]).fetchall()
        if not rows:
            break

        stage = _tempfile.mkdtemp(prefix="drvdoc-")
        items = []
        try:
            for i, r in enumerate(rows):
                acct = r["account"]
                if acct not in tokens:
                    tokens[acct] = auth.TokenSource(acct)
                mime = r["mime_type"] or ""
                # GOOGLE-NATIVE FILES ARE EXPORTED, NOT DOWNLOADED. A Doc, Sheet or Slide
                # has no bytes to fetch; asking for them returns 403 "Only files with
                # binary content can be downloaded. Use Export with Docs Editors files."
                # The listing walk used to go through extract(), which knew this, and this
                # pass originally went straight to the download endpoint — so the three
                # files it could not read were exactly the Google-native ones. Those are,
                # in this module's own words, "usually the most valuable things in a
                # Drive", and they cost nothing to index.
                try:
                    if mime in EXPORTS:
                        export_as, _ = EXPORTS[mime]
                        data = api_get(tokens[acct], f"/files/{r['id']}/export",
                                       {"mimeType": export_as}, raw=True)
                    else:
                        data = api_get(tokens[acct], f"/files/{r['id']}",
                                       {"alt": "media"}, raw=True)
                except SystemExit as exc:                 # a dead token, not a bad file
                    print(f"  ! {acct}: {exc}")
                    data = None
                if not data:
                    # A ZERO-BYTE FILE IS EMPTY, NOT UNREADABLE. Two of the entries here
                    # are Army documents — DA_1380_CPT_MEYER and a USCENTCOM order — with
                    # `size: 0` in Drive's own metadata and `canDownload: true`. They are
                    # stubs somebody's sync left behind: the entry exists, the content does
                    # not. Recording that as a failed download made a permanent, retried-
                    # forever error out of what is simply an empty file, and it put two of
                    # the owner's military documents on a list of things that "will be
                    # retried" for no reason. Drive told us the size; believe it.
                    zero = (r["size"] or 0) == 0
                    state = "empty" if zero else "unavailable:download"
                    with conn:
                        conn.execute("UPDATE drive_files SET text_state=? WHERE id=?",
                                     (state, r["id"]))
                    if zero:
                        empty += 1
                    else:
                        unavailable += 1
                    continue
                # The staged name carries the real extension: the ladder picks its rung
                # from the extension when the MIME is generic, and some Drive PDFs are
                # served as application/octet-stream. An exported Doc arrives as text, so
                # the extension follows the EXPORT type rather than the Drive MIME.
                if mime in EXPORTS:
                    export_as = EXPORTS[mime][0]
                    ext = {"text/plain": ".txt", "text/csv": ".csv",
                           "application/vnd.google-apps.script+json": ".json"}.get(
                               export_as, ".txt")
                else:
                    ext = os.path.splitext(r["name"] or "")[1] or ".bin"
                path = os.path.join(stage, f"{i:05d}{ext}")
                with open(path, "wb") as fh:
                    fh.write(data)
                # The export's MIME, so the ladder reads it as the text it now is.
                items.append((r["id"], path,
                              EXPORTS[mime][0] if mime in EXPORTS else mime))

            results = ocr_ladder.extract_many(items, max_pages=max_pages)
        finally:
            _shutil.rmtree(stage, ignore_errors=True)

        for r in rows:
            if r["id"] not in results:
                continue
            text, source, error = results[r["id"]]
            if error:
                state = error
                unavailable += 1
            elif text:
                state = "ok"
                read += 1
            else:
                state = "empty"
                empty += 1
            with conn:
                conn.execute("UPDATE drive_files SET text_state=?, indexed_at=?"
                             " WHERE id=?", (state, time.time(), r["id"]))
                if text:
                    conn.execute(
                        "INSERT OR REPLACE INTO document_text (file_id, account, body,"
                        " source) VALUES (?,?,?,?)", (r["id"], r["account"], text, source))
                    conn.execute("DELETE FROM document_fts WHERE file_id=?", (r["id"],))
                    conn.execute(
                        "INSERT INTO document_fts (file_id, account, name, body)"
                        " VALUES (?,?,?,?)",
                        (r["id"], r["account"], r["name"], text[:400000]))

        done += len(rows)
        print(f"    {done}/{total}: {read} read, {empty} empty, {unavailable} unavailable",
              flush=True)
        if len(rows) < want:
            break

    print(f"\ndone: {read} read, {empty} with no text, {unavailable} unreadable")
    if unavailable:
        print("  unreadable files keep their reason and will be retried — they are not"
              " recorded as empty")
    coverage_line(conn, account)
    return read


def coverage_line(conn, email: str | None = None):
    """One line an operator can act on: how much is left, and what it is made of."""
    d = coverage(conn, email)
    print(f"  coverage: {d['with_text']} read · {d['unread']} unread ·"
          f" {d['not_fetchable']} not fetchable (media, archives, folders)"
          f" — {d['n']} rows")
    if d["unread"]:
        top = conn.execute("""
            SELECT mime_type, COUNT(*) n FROM drive_files f
            LEFT JOIN document_text t ON t.file_id = f.id
            WHERE t.file_id IS NULL AND f.indexable = 1
            GROUP BY mime_type ORDER BY n DESC LIMIT 4""").fetchall()
        for r in top:
            print(f"      {r['n']:>6}  {r['mime_type']}")


def coverage(conn, email: str | None = None, source: str | None = None) -> dict:
    """How much of what we hold can actually be READ. Shared by `stats` and /health.

    This is the number that was invisible for weeks. "7,385 files" reads like coverage;
    the honest figure is the `unread` and `unavailable` lines below it, and the whole
    point of `text_state` is that they can be printed at all.
    """
    where, args = [], []
    if email:
        where.append("f.account = ?")
        args.append(email)
    if source:
        where.append("f.source = ?")
        args.append(source)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    row = conn.execute(f"""
        SELECT COUNT(*) n,
               SUM(CASE WHEN f.text_state = 'ok'      THEN 1 ELSE 0 END) read,
               SUM(CASE WHEN f.text_state = 'empty'   THEN 1 ELSE 0 END) empty,
               SUM(CASE WHEN f.text_state IS NULL     THEN 1 ELSE 0 END) untouched,
               SUM(CASE WHEN f.text_state = 'skipped' THEN 1 ELSE 0 END) skipped,
               SUM(CASE WHEN f.text_state LIKE 'unavailable%' OR f.text_state LIKE '%:%'
                        THEN 1 ELSE 0 END) unavailable
        FROM drive_files f{clause}
    """, args).fetchone()
    d = {k: (row[k] or 0) for k in row.keys()}

    # UNREAD MEANS "WE COULD READ IT AND HAVE NOT", AND NOTHING ELSE.
    #
    # This was `n - with_text`, which counted the 3,756 audio files, folders and archives
    # as unread — so /health reported "6,471 unread" on a Drive where the real number was
    # 6, and the crawl looked permanently unfinished. A number that lumps "we chose not to"
    # in with "we could not" is the same failure this column was invented to prevent, one
    # level up: it makes an honest gap look like an ongoing problem, and an ongoing problem
    # stops being read.
    #
    # `text_state` distinguishes them for rows we have touched; this counts what is left.
    d["with_text"] = conn.execute(
        "SELECT COUNT(*) FROM document_text d JOIN drive_files f ON f.id = d.file_id"
        + clause, args).fetchone()[0]
    d["unread"] = conn.execute(
        "SELECT COUNT(*) FROM drive_files f"
        " LEFT JOIN document_text d ON d.file_id = f.id"
        " WHERE d.file_id IS NULL AND f.indexable = 1"
        + (" AND " + " AND ".join(where) if where else ""), args).fetchone()[0]
    d["not_fetchable"] = d["n"] - d["with_text"] - d["unread"]
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
    if cmd == "documents":
        # The catch-up pass. No account or file count required: it reads the database for
        # what is missing, so re-running it after an interruption continues rather than
        # starting over.
        cap = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else None
        acct = next((a for a in argv[2:] if "@" in a), None)
        pages = 0 if "--all-pages" in argv else None
        documents(limit=cap, account=acct, max_pages=pages)
        return 0
    if cmd == "stats":
        if len(argv) < 3:
            raise SystemExit("usage: drive_index.py stats <email>")
        stats(argv[2])
        return 0
    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
