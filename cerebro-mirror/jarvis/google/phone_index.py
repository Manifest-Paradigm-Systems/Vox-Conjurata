"""Pull call log and SMS from the phone over adb. No Google API, no quota, no model.

The phone's content providers are readable from adb, which means this needs no Android
app, no new permission grant, and no network round-trip to Google:

    adb shell content query --uri content://call_log/calls
    adb shell content query --uri content://sms

TWO PARSING TRAPS, both handled below:

1. **Commas.** The output is `Row: 0 col=value, col=value` and SMS bodies contain commas.
   The body is therefore projected LAST and the split is capped, so everything after the
   final expected separator stays with the body.

2. **Newlines.** A multi-line text breaks the one-row-per-line format, and the remainder
   arrives as a line that does not begin with "Row:". Those continuations are appended
   to the previous row's body rather than dropped — silently losing the second half of
   someone's message would be worse than failing loudly.

Usage:
    python3 phone_index.py calls          # call log
    python3 phone_index.py sms            # text messages
    python3 phone_index.py all
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import control  # noqa: E402

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))
ADB = os.environ.get("JARVIS_ADB", "adb")
SERIAL = os.environ.get("JARVIS_PHONE", "")
DEVICE = os.environ.get("JARVIS_PHONE_NAME", "pixel")
CALL_URI = "content://call_log/calls"
SMS_URI = "content://sms"

# Projected in this order; the body/name go LAST so a comma inside them is kept.
# The column is `name`, not `cached_name` — this Android build rejects `cached_name`
# outright, which fails the WHOLE query rather than that one column. See adb_query.
CALL_COLS = ["_id", "number", "type", "date", "duration", "name"]
SMS_COLS = ["_id", "thread_id", "address", "date", "type", "read", "body"]

# A 5-6 digit sender is a machine — bank, 2FA, delivery. Kept as a row; its content is
# not indexed and never searchable.
_SHORTCODE = re.compile(r"^\d{4,6}$")
# One-time passcodes should never reach a search index, whoever sent them. Case
# insensitivity is a compile flag rather than an inline (?i): since Python 3.11 an inline
# global flag must sit at the very start of the pattern, and in the middle of an
# alternation it is a syntax error at import time.
_OTP = re.compile(
    r"\b(code|pin|otp|passcode)\b.{0,30}\b\d{4,8}\b"
    r"|\b\d{4,8}\b.{0,20}(is your|as your)\b.{0,20}\b(code|pin|otp)\b"
    r"|do not share",
    re.IGNORECASE)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------- adb
def adb_query(uri: str, cols: list[str]) -> str:
    cmd = [ADB]
    if SERIAL:
        cmd += ["-s", SERIAL]
    cmd += ["shell", "content", "query", "--uri", uri,
            "--projection", ":".join(cols)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise SystemExit(f"adb query failed on {uri}:\n  {(r.stderr or r.stdout).strip()[:300]}")

    # A provider error must not look like an empty table. `content query` reports a bad
    # column on STDOUT with exit status 0, so a wrong projection produced zero rows and
    # the puller cheerfully reported "0 rows from the phone" — the same silent-empty
    # failure as the email reply graph, and just as convincing.
    out = r.stdout
    if "Error while accessing provider" in out or "IllegalArgumentException" in out:
        detail = out.strip().splitlines()
        raise SystemExit(
            "the phone's provider refused the query — most likely a column that does not\n"
            "exist on this Android version. A failed query returns NO rows, so this would\n"
            "otherwise be silently recorded as an empty call log.\n  "
            + "\n  ".join(detail[:4]))
    return out


def parse_rows(raw: str, cols: list[str]) -> list[dict]:
    """Parse `Row: 0 a=1, b=2, c=the, rest` into dicts.

    maxsplit is len(cols)-1 so the LAST column keeps any commas it contains, and a line
    that does not start with "Row:" is treated as a continuation of the previous row's
    last column (a multi-line body) rather than discarded.
    """
    rows: list[dict] = []
    last_field = cols[-1]
    for line in raw.splitlines():
        if not line.startswith("Row: "):
            if rows and line.strip():
                rows[-1][last_field] = (rows[-1][last_field] + "\n" + line).strip()
            continue
        body = line[len("Row: "):]
        parts = body.split(", ", len(cols) - 1)
        if len(parts) != len(cols):
            continue
        row = {}
        for col, chunk in zip(cols, parts):
            row[col] = chunk.split("=", 1)[1] if "=" in chunk else ""
        rows.append(row)
    return rows


def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- storage
def store_calls(conn, rows) -> int:
    exclusions = control.load_exclusions(conn, "calls")
    n = 0
    with conn:
        for r in rows:
            number = (r.get("number") or "").strip()
            if control.is_excluded(exclusions, number, "", ""):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO calls (id, device, number, call_type, ts,"
                " duration_s, cached_name, indexed_at) VALUES (?,?,?,?,?,?,?,?)",
                (f"{DEVICE}:{r['_id']}", DEVICE, number, _int(r.get("type")),
                 _int(r.get("date")), _int(r.get("duration")),
                 (r.get("cached_name") or "").strip(), time.time()))
            n += 1
    return n


def store_sms(conn, rows) -> int:
    exclusions = control.load_exclusions(conn, "sms")
    n = indexed = skipped_auto = 0
    with conn:
        for r in rows:
            address = (r.get("address") or "").strip()
            body = r.get("body") or ""
            if control.is_excluded(exclusions, address, "", body):
                continue

            automated = 1 if _SHORTCODE.match(address) else 0
            # An OTP is treated as automated regardless of sender: ephemeral, useless to
            # keep, and the worst possible thing to have sitting in a search index.
            if automated or _OTP.search(body):
                body = ""
                skipped_auto += 1

            conn.execute(
                "INSERT OR REPLACE INTO sms (id, device, thread_id, address, ts,"
                " direction, automated, body, indexed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"{DEVICE}:{r['_id']}", DEVICE, r.get("thread_id"), address,
                 _int(r.get("date")), "out" if _int(r.get("type")) == 2 else "in",
                 automated, body, time.time()))
            n += 1
            if body:
                conn.execute("DELETE FROM sms_fts WHERE sms_id=?", (f"{DEVICE}:{r['_id']}",))
                conn.execute("INSERT INTO sms_fts (sms_id, address, body) VALUES (?,?,?)",
                             (f"{DEVICE}:{r['_id']}", address, body))
                indexed += 1
    print(f"    {indexed} indexed with content, {skipped_auto} auto/OTP bodies withheld")
    return n


# ---------------------------------------------------------------- MMS
MMS_URI = "content://mms"
MMS_PART_URI = "content://mms/part"
MMS_COLS = ["_id", "thread_id", "date", "msg_box", "sub"]
MMS_PART_COLS = ["_id", "mid", "ct", "name"]


def thread_addresses(conn) -> dict:
    """thread_id -> the other party's number, taken from the SMS we already have.

    Android gives SMS and MMS the SAME thread ids, so the address can be read from the
    texts instead of querying `content://mms/<id>/addr` once per message. That is one
    query instead of seventeen thousand, and the answer is identical.
    """
    out = {}
    for r in conn.execute("SELECT thread_id, address, COUNT(*) n FROM sms"
                          " WHERE address IS NOT NULL AND address != ''"
                          " GROUP BY thread_id, address ORDER BY n DESC"):
        out.setdefault(str(r["thread_id"]), r["address"])
    return out


def store_mms(conn, msgs, parts, thread_addr) -> tuple[int, int]:
    exclusions = control.load_exclusions(conn, "sms")
    by_mid: dict[str, list] = {}
    for p in parts:
        by_mid.setdefault(str(p.get("mid")), []).append(p)

    n = images = 0
    with conn:
        for m in msgs:
            mid = str(m.get("_id"))
            addr = thread_addr.get(str(m.get("thread_id")), "")
            if control.is_excluded(exclusions, addr):
                continue
            # Upstream gives seconds here, milliseconds in the SMS table. Normalise so
            # the two can be sorted and joined together without a conversion at each use.
            ts = _int(m.get("date")) * 1000
            conn.execute(
                "INSERT OR REPLACE INTO mms (id, device, thread_id, address, ts,"
                " direction, subject, automated, indexed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"{DEVICE}:{mid}", DEVICE, m.get("thread_id"), addr, ts,
                 "out" if _int(m.get("msg_box")) == 2 else "in",
                 (m.get("sub") or "").strip(),
                 1 if _SHORTCODE.match(addr or "") else 0, time.time()))
            n += 1

            for p in by_mid.get(mid, []):
                ct = (p.get("ct") or "").strip()
                pid = str(p.get("_id"))
                is_image = 1 if ct.startswith("image/") else 0
                if is_image:
                    images += 1
                conn.execute(
                    "INSERT OR REPLACE INTO mms_parts (id, mms_id, device,"
                    " content_type, name, is_image, indexed_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (f"{DEVICE}:{pid}", f"{DEVICE}:{mid}", DEVICE, ct,
                     (p.get("name") or "").strip(), is_image, time.time()))
    return n, images


# ---------------------------------------------------------------- report
def summary():
    conn = db()
    for label, sql in (
        ("calls", "SELECT COUNT(*) FROM calls"),
        ("  incoming", "SELECT COUNT(*) FROM calls WHERE call_type=1"),
        ("  outgoing", "SELECT COUNT(*) FROM calls WHERE call_type=2"),
        ("  missed", "SELECT COUNT(*) FROM calls WHERE call_type=3"),
        ("sms", "SELECT COUNT(*) FROM sms"),
        ("  from people", "SELECT COUNT(*) FROM sms WHERE automated=0"),
        ("  automated", "SELECT COUNT(*) FROM sms WHERE automated=1"),
        ("  with content", "SELECT COUNT(*) FROM sms WHERE body != ''"),
        ("mms", "SELECT COUNT(*) FROM mms"),
        ("  image parts", "SELECT COUNT(*) FROM mms_parts WHERE is_image=1"),
        ("  other parts", "SELECT COUNT(*) FROM mms_parts WHERE is_image=0"),
    ):
        print(f"    {label:<15}: {conn.execute(sql).fetchone()[0]}")


def main(argv: list[str]) -> int:
    what = argv[1] if len(argv) > 1 else "all"
    conn = db()

    if what in ("calls", "all"):
        if control.is_paused(conn, "calls"):
            print("  calls indexing is PAUSED — skipped")
        else:
            print("  reading the call log...")
            rows = parse_rows(adb_query(CALL_URI, CALL_COLS), CALL_COLS)
            print(f"    {len(rows)} rows from the phone")
            n = store_calls(conn, rows)
            print(f"    {n} stored")

    if what in ("sms", "all"):
        if control.is_paused(conn, "sms"):
            print("  sms indexing is PAUSED — skipped")
        else:
            print("  reading text messages...")
            rows = parse_rows(adb_query(SMS_URI, SMS_COLS), SMS_COLS)
            print(f"    {len(rows)} rows from the phone")
            n = store_sms(conn, rows)
            print(f"    {n} stored")

    if what in ("mms", "all"):
        if control.is_paused(conn, "sms"):
            print("  mms indexing is PAUSED — skipped")
        else:
            print("  reading picture messages...")
            msgs = parse_rows(adb_query(MMS_URI, MMS_COLS), MMS_COLS)
            print(f"    {len(msgs)} mms headers from the phone")
            parts = parse_rows(adb_query(MMS_PART_URI, MMS_PART_COLS), MMS_PART_COLS)
            print(f"    {len(parts)} parts")
            n, images = store_mms(conn, msgs, parts, thread_addresses(conn))
            print(f"    {n} stored, {images} image parts recorded (bytes not fetched)")

    print("\n  now in the database:")
    summary()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
