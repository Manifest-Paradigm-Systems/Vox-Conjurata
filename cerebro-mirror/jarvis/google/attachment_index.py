"""Read what is INSIDE the attachments — documents and screenshots.

For most accounts this is optional. For Michael's it is the point: the IEPs, the school
forms, the specialist letters and the scanned medical paperwork arrive as attachments
rather than as Drive files, so without this the account's actual content is invisible.

Everything here is LOCAL except the download itself. Extraction and OCR never leave the
machine — no Google Vision, no Document AI, no external model. That is not a preference:
these are a child's medical and educational records, and the rule for this subsystem is
that they do not go anywhere to be processed.

The ladder, cheapest first:

    PDF with a text layer   ->  pdftotext            (instant, exact)
    PDF with no text layer  ->  pdftoppm + tesseract (a scan; OCR it)
    DOCX / XLSX / PPTX      ->  unzip + strip XML    (no dependency, no OCR)
    images / screenshots    ->  tesseract            (or the eyes, see OCR_ENGINE)

tesseract is the default for images because it is fast and these are mostly screenshots
of forms and documents rather than photographs. Set OCR_ENGINE=eyes to route them through
MiniCPM-V on cerebro instead, which is better on messy images and much slower.

Usage:
    python3 attachment_index.py plan          # what would be read, without reading it
    python3 attachment_index.py run [limit]   # extract, newest first
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth        # noqa: E402
import control     # noqa: E402
import ocr_ladder  # noqa: E402

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))
API = "https://gmail.googleapis.com/gmail/v1/users/me"
REQUEST_DELAY = float(os.getenv("JARVIS_REQUEST_DELAY", "0.08"))

# The OCR settings themselves live in ocr_ladder.py now — including the engine choice
# and the page cap. Only the display copy is kept, so `stats` can report what the ladder
# will actually use without importing a private constant out of it.
OCR_ENGINE = ocr_ladder.OCR_ENGINE                     # tesseract | eyes | off
MAX_BYTES = int(os.getenv("JARVIS_ATTACHMENT_MAX_MB", "25")) * 1024 * 1024

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ZIP_OFFICE = ("application/vnd.openxmlformats-officedocument", "application/vnd.ms-")

# What is worth the work. Everything else gets a metadata row and is left alone —
# a 40 MB video in an email is not content, it is weight.
READABLE = ("application/pdf", "application/msword", "text/", "image/")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=180)
    conn.row_factory = sqlite3.Row
    # Patience, because the mail crawl is writing to this database at the same time. It
    # holds the write lock for a whole page (~60s at 12 req/s), so anything that wants to
    # write has to wait that long — which is survivable ONCE per batch and fatal if it
    # happens per image.
    conn.execute("PRAGMA busy_timeout = 180000")
    return conn


def readable(mime: str) -> bool:
    if not mime:
        return False
    if mime == DOCX or mime.startswith(ZIP_OFFICE) or mime.startswith(READABLE):
        return True
    return False


# ---------------------------------------------------------------- download
def download(token: str, message_id: str, remote_id: str) -> bytes:
    time.sleep(REQUEST_DELAY)
    url = f"{API}/messages/{message_id}/attachments/{urllib.parse.quote(remote_id, safe='')}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        body = exc.read(300).decode("utf-8", "replace")
        raise SystemExit(f"{exc.code} fetching attachment: {body}")
    raw = data.get("data")
    if not raw:
        return b""
    return base64.urlsafe_b64decode(raw + "===")


# ---------------------------------------------------------------- extraction
# The ladder lives in ocr_ladder.py. This module used to carry its own copy — a third
# copy, after drive_index.py — and each grew its own version of the same bug: a missing
# binary became an empty string, and an empty string meant "nothing readable here". A
# host without poppler therefore recorded every scanned IEP and specialist letter as a
# document with no text in it, and said nothing. One ladder now, and it returns a reason.

def extract(mime: str, data: bytes) -> tuple[str, str, str | None]:
    """(text, source, error) for one attachment.

    Empty text with `error is None` means the attachment was read and holds no text.
    Empty text with an error means we could not read it — and that is the difference
    between a row that says "done" and a row that says "try me again when the tool is
    installed". Reporting the second as the first is what this module used to do.
    """
    if not data:
        return "", "", None
    return ocr_ladder.extract_bytes(data, mime)


# ---------------------------------------------------------------- passes
def pick(conn, limit: int | None = None):
    """Attachments worth reading: readable type, downloadable, not yet done.

    INLINE IMAGES ARE SKIPPED. Most images in email are signature logos, tracking pixels
    and social icons — not content. Downloading and OCR'ing them would be most of the work
    for none of the meaning, and the cost is not just time: an OCR pass over a bank's logo
    produces confident nonsense that then sits in the index.
    """
    q = """
        SELECT a.id, a.message_id, a.account, a.filename, a.mime_type, a.size,
               a.remote_id, a.inline, m.internal_date
        FROM attachments a JOIN messages m ON m.id = a.message_id
        WHERE a.extracted = 0 AND a.remote_id IS NOT NULL
          AND (a.inline IS NULL OR a.inline = 0)
        ORDER BY m.internal_date DESC
    """
    if limit:
        q += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(q) if readable(r["mime_type"])]


def plan():
    conn = db()
    n = conn.execute("SELECT COUNT(*) c FROM attachments WHERE extracted=0").fetchone()["c"]
    have = [dict(r) for r in conn.execute(
        "SELECT mime_type, COUNT(*) c FROM attachments WHERE extracted=0"
        " GROUP BY mime_type ORDER BY c DESC LIMIT 12")]
    todo = pick(conn)
    print(f"\n  {n} attachments recorded, {len(todo)} are readable and not yet extracted")
    print(f"  OCR engine: {OCR_ENGINE}")
    print("\n  by type:")
    for r in have:
        mark = "read" if readable(r["mime_type"]) else "skip"
        print(f"    {r['c']:>5}  [{mark}]  {r['mime_type'][:56]}")


def run(limit: int | None = None):
    conn = db()
    if control.is_paused(conn, "attachments"):
        print("  attachment extraction is PAUSED — nothing written.")
        return 0

    todo = pick(conn, limit)
    if not todo:
        print("  nothing to do — no readable, un-extracted attachments with a remote id.")
        print("  (Rows fetched before the remote_id column existed have none; a re-crawl"
              " fills them in.)")
        return 0

    exclusions = control.load_exclusions(conn, "gmail")
    tokens: dict[str, str] = {}
    done = skipped = failed = 0
    chars = 0

    for i, a in enumerate(todo, 1):
        if i % 25 == 0:
            conn.commit()
            print(f"    {i}/{len(todo)}: {done} extracted, {skipped} empty, {failed} failed",
                  flush=True)
        if control.is_paused(conn, "attachments"):
            print("  PAUSED mid-run — stopping here.")
            break
        if control.is_excluded(exclusions, a["filename"] or ""):
            skipped += 1
            continue
        if (a["size"] or 0) > MAX_BYTES:
            skipped += 1
            continue

        acct = a["account"]
        if acct not in tokens:
            tokens[acct] = auth.access_token(acct)
        try:
            data = download(tokens[acct], a["message_id"], a["remote_id"])
        except SystemExit as exc:
            failed += 1
            print(f"    ! {a['filename'][:40]}: {exc}")
            continue

        text, source, error = extract(a["mime_type"], data)
        if error:
            # WE COULD NOT READ IT — so it is not done. `extracted` stays 0 and the next
            # pass picks it up again, while the reason is written where an operator will
            # find it. Marking this row extracted=1 would be the original bug wearing a
            # progress bar: a missing tool recorded permanently as "nothing readable".
            with conn:
                conn.execute("UPDATE attachments SET text_source=? WHERE id=?",
                             (error, a["id"]))
            failed += 1
            print(f"    ! {a['filename'][:40]}: {error}")
            continue

        with conn:
            conn.execute("UPDATE attachments SET extracted=1, text_source=?,"
                         " indexed_at=? WHERE id=?", (source, time.time(), a["id"]))
            if text:
                conn.execute("INSERT OR REPLACE INTO attachment_text (attachment_id,"
                             " body) VALUES (?,?)", (a["id"], text))
                done += 1
                chars += len(text)
            else:
                skipped += 1

    conn.commit()
    print(f"\ndone: {done} extracted ({chars:,} chars), {skipped} nothing readable,"
          f" {failed} failed")
    return done


# ---------------------------------------------------------------- MMS parts
# Pictures in texts. The bytes come from the phone rather than from Google, so the fetch
# is an adb read instead of an API call — but the rest of the ladder is identical, and so
# are the reasons for it.
#
# The ordering matters more here than for email. A contractor texts quotes, receipts and
# progress photos, and those are the images with something in them; the rest of a
# person's photo texts are mostly incidental. `--account` scopes the pass to a domain,
# so the property images can be read first and the incidental ones deferred.
MMS_PART_URI = "content://mms/part"


def adb_read_part(part_id: str) -> bytes:
    """Read one MMS part's bytes.

    `exec-out` rather than `shell`: `adb shell` translates line endings on the way
    through, which corrupts binary data in ways that look like a decode failure rather
    than a transport one.
    """
    serial = os.environ.get("JARVIS_PHONE", "")
    cmd = [os.environ.get("JARVIS_ADB", "adb")]
    if serial:
        cmd += ["-s", serial]
    cmd += ["exec-out", "content", "read", "--uri", f"content://mms/part/{part_id.split(':')[-1]}"]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    return r.stdout or b""


def mms_pick(conn, limit=None, account=None):
    """Image parts worth OCR'ing, newest first.

    Skips the part type that is almost never content: image/gif. And skips parts whose
    message landed in no domain when a domain was asked for.
    """
    # The domain comes from the MESSAGE, not the part. `mms_parts` has no address column
    # to match a contact against, so the apply pass leaves it NULL — which read as
    # "2141 images, no domain" and would have made --account match nothing.
    q = """
        SELECT p.id, p.content_type, m.account, m.address, m.ts, m.thread_id
        FROM mms_parts p JOIN mms m ON m.id = p.mms_id
        WHERE p.is_image = 1 AND p.extracted = 0
          AND lower(p.content_type) NOT LIKE '%gif%'
    """
    args = []
    if account:
        q += " AND m.account = ?"
        args.append(account)
    q += " ORDER BY m.ts DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    return [dict(r) for r in conn.execute(q, args)]


def mms_plan(account=None):
    conn = db()
    todo = mms_pick(conn, account=account)
    total = conn.execute("SELECT COUNT(*) c FROM mms_parts WHERE is_image=1").fetchone()["c"]
    gif = conn.execute("SELECT COUNT(*) c FROM mms_parts WHERE is_image=1"
                       " AND lower(content_type) LIKE '%gif%'").fetchone()["c"]
    print(f"\n  {total} image parts total, {gif} are GIFs (skipped)")
    print(f"  {len(todo)} would be read" + (f" in {account}" if account else ""))
    by = {}
    for t in todo:
        by[t["account"] or "(no domain)"] = by.get(t["account"] or "(no domain)", 0) + 1
    for k, v in sorted(by.items(), key=lambda x: -x[1]):
        print(f"    {v:>5}  {k}")


def mms_run(limit=None, account=None):
    conn = db()
    if control.is_paused(conn, "attachments"):
        print("  attachment extraction is PAUSED — nothing written.")
        return 0

    todo = mms_pick(conn, limit=limit, account=account)
    if not todo:
        print("  nothing to do — no un-extracted image parts match.")
        return 0

    exclusions = control.load_exclusions(conn, "sms")
    done = empty = failed = 0
    chars = 0
    pending = []          # (part_id, account, address, source, text)

    def flush():
        """One transaction per batch.

        The slow part here is the read and the OCR, neither of which touches the
        database. Writing each result as it arrived meant competing for the write lock
        once per image against a crawler that holds it for a minute at a time — 988
        chances to lose a race, and it lost the first one. Batching means it waits once.
        """
        nonlocal done, empty, chars
        if not pending:
            return
        with conn:
            for pid, acct, addr, source, text in pending:
                conn.execute("UPDATE mms_parts SET extracted=1, text_source=?,"
                             " indexed_at=? WHERE id=?", (source, time.time(), pid))
                if text:
                    conn.execute("INSERT OR REPLACE INTO mms_text (part_id, account,"
                                 " body) VALUES (?,?,?)", (pid, acct, text))
                    conn.execute("DELETE FROM mms_fts WHERE part_id=?", (pid,))
                    conn.execute("INSERT INTO mms_fts (part_id, address, body)"
                                 " VALUES (?,?,?)", (pid, addr, text[:100000]))
        pending.clear()

    for i, p in enumerate(todo, 1):
        if control.is_paused(conn, "attachments"):
            print("  PAUSED mid-run — stopping here.")
            break
        if len(pending) >= 40:
            flush()
            print(f"    {i}/{len(todo)}: {done} with text, {empty} nothing,"
                  f" {failed} failed", flush=True)
        if control.is_excluded(exclusions, p["address"] or ""):
            empty += 1
            continue

        data = adb_read_part(p["id"])
        if not data:
            pending.append((p["id"], p["account"], p["address"], "empty", ""))
            empty += 1
            continue

        text, source, error = extract(p["content_type"], data)
        if error:
            # Same rule as the attachment pass: a failed read is not an empty one, and it
            # is counted as failed so it is retried rather than written off as nothing.
            failed += 1
            if failed <= 5:
                print(f"    ! {p['address']}: {error}")
            continue
        pending.append((p["id"], p["account"], p["address"], source, text))
        if text:
            done += 1
            chars += len(text)
        else:
            empty += 1

    flush()
    print(f"\ndone: {done} images with text ({chars:,} chars), {empty} with none,"
          f" {failed} failed")
    return done


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    account = argv[argv.index("--account") + 1] if "--account" in argv else None
    limit = None
    for a in argv[2:]:
        if a.isdigit():
            limit = int(a)
            break

    if cmd == "plan":
        plan()
        return 0
    if cmd == "run":
        run(limit)
        return 0
    if cmd == "mms-plan":
        mms_plan(account)
        return 0
    if cmd == "mms":
        mms_run(limit, account)
        return 0
    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
