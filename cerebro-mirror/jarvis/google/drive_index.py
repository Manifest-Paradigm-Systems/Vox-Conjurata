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

import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))
API = "https://www.googleapis.com/drive/v3"
PAGE = 200

# PDFs and scans go through poppler and tesseract rather than a Python library, because
# those are what this host actually has. See text_from_pdf.
OCR_ENGINE = os.getenv("JARVIS_OCR", "tesseract")
OCR_MAX_PAGES = int(os.getenv("JARVIS_OCR_MAX_PAGES", "20"))

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
    "text/plain": "download:text",
    "text/markdown": "download:text",
    "text/csv": "download:text",
}
# Deliberately excluded, and this list is the whole reason a "many GB" Drive is tractable.
SKIP_PREFIXES = ("video/", "audio/", "image/", "application/zip", "application/x-tar",
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
def text_from_pdf(data: bytes) -> str:
    """Text from a PDF, via poppler, falling back to OCR for scans.

    THIS USED TO USE pypdf, WHICH IS NOT INSTALLED ON THIS HOST. The import failed, the
    except branch returned an empty string, and every PDF in the Drive was recorded as
    having no text — 326 of them: invoices, plumbing estimates, an MRI report. No error
    appeared anywhere, and it looked exactly like a Drive full of scanned images.

    `pdftotext` is installed, is what attachment_index.py already uses, and needs no Python
    dependency. An empty string must mean "this document has no text", never "I could not
    look" — so a missing tool now surfaces instead of being swallowed.
    """
    if not data:
        return ""
    tmp = tempfile.mkdtemp(prefix="drvpdf-")
    try:
        path = os.path.join(tmp, "f.pdf")
        with open(path, "wb") as fh:
            fh.write(data)
        text = _run(["pdftotext", "-layout", "-q", path, "-"])
        if len(text.strip()) > 40:
            return text.strip()

        # No text layer — a scan. Same fallback the attachment pass uses, for the same
        # reason: the documents that matter most arrive off a scanner.
        if OCR_ENGINE == "off":
            return ""
        _run(["pdftoppm", "-r", "200", "-png", "-l", str(OCR_MAX_PAGES), path,
              os.path.join(tmp, "page")])
        pages = sorted(f for f in os.listdir(tmp) if f.endswith(".png"))
        out = []
        for p in pages[:OCR_MAX_PAGES]:
            t = _run(["tesseract", os.path.join(tmp, p), "stdout", "-l", "eng",
                      "--psm", "3"])
            if t.strip():
                out.append(t.strip())
        return "\n\n".join(out).strip()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(cmd: list[str], timeout: int = 300) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout or ""
    except (subprocess.SubprocessError, OSError):
        return ""


def text_from_docx(data: bytes) -> str:
    """A .docx is a zip of XML. No dependency needed."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
    except Exception:                                       # noqa: BLE001
        return ""
    xml = re.sub(r"(?i)</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    import html as _html
    return _html.unescape(text).strip()


def extract(token: str, file_id: str, mime: str) -> tuple[str, str]:
    """(text, source). Empty text means 'not indexable', which is normal and not an error."""
    if mime in EXPORTS:
        export_as, source = EXPORTS[mime]
        data = api_get(token, f"/files/{file_id}/export",
                       {"mimeType": export_as}, raw=True)
        if not data:
            return "", ""
        return data.decode("utf-8", "replace").strip(), source

    if mime in DOWNLOADABLE:
        source = DOWNLOADABLE[mime]
        data = api_get(token, f"/files/{file_id}", {"alt": "media"}, raw=True)
        if not data:
            return "", ""
        if mime == "application/pdf":
            return text_from_pdf(data), source
        if mime.endswith("wordprocessingml.document"):
            return text_from_docx(data), source
        return data.decode("utf-8", "replace").strip(), source

    return "", ""


def indexable(mime: str) -> int:
    if mime in EXPORTS or mime in DOWNLOADABLE:
        return 1
    if mime.startswith(SKIP_PREFIXES):
        return 0
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
            text, source = ("", "")
            if can:
                text, source = extract(token, f["id"], mime)

            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO drive_files (id, account, name, mime_type,"
                    " modified_time, created_time, size, owners, trashed, indexable,"
                    " indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f["id"], email, f.get("name"), mime, f.get("modifiedTime"),
                     f.get("createdTime"), int(f.get("size") or 0),
                     json.dumps([o.get("emailAddress") for o in (f.get("owners") or [])]),
                     0, 1 if can else 0, time.time()))
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

    print(f"\ndone: {seen} files seen, {indexed} indexed, {skipped} skipped "
          f"(no text, or a type we do not fetch)")
    return seen


def stats(email: str):
    conn = db()
    row = conn.execute("""
        SELECT COUNT(*) n,
               SUM(indexable) idx,
               SUM(CASE WHEN indexable=0 THEN 1 ELSE 0 END) skipped,
               MIN(modified_time) oldest, MAX(modified_time) newest
        FROM drive_files WHERE account=?
    """, (email,)).fetchone()
    have = conn.execute("SELECT COUNT(*) c FROM document_text WHERE account=?",
                        (email,)).fetchone()["c"]
    if not row or not row["n"]:
        print(f"  {email}: nothing indexed yet")
        return
    d = dict(row)
    print(f"  {email}")
    print(f"    files seen     : {d['n']}")
    print(f"    with text      : {have}")
    print(f"    skipped (no text / media / archive): {d['skipped'] or 0}")
    print(f"    modified range : {(d['oldest'] or '')[:10]} .. {(d['newest'] or '')[:10]}")


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
