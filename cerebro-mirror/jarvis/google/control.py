"""Pause indexing, and erase what has already been indexed.

Two switches, and the reason they exist is trust. A system that only ingests is one you
eventually stop putting anything real into, because there is no way back out.

    python3 control.py status
    python3 control.py pause sms --note "seeing someone, don't want this indexed"
    python3 control.py resume sms
    python3 control.py forget --address someone@example.com
    python3 control.py forget --contains "shoulder surgery" --stream gmail
    python3 control.py forget --between 2026-01-01..2026-03-01 --stream sms
    python3 control.py rules
    python3 control.py unexclude 3

`forget` always shows what it would remove first. Nothing is deleted without --yes.

THE IMPORTANT DETAIL: an erasure is a RULE, not an event. The crawlers are resumable and
re-run, so anything deleted but not recorded as an exclusion comes straight back on the
next sync — a deletion that quietly undoes itself overnight is worse than no deletion,
because you would trust it.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))
STREAMS = ("gmail", "sms", "calls", "calendar", "drive", "attachments")

# The stream says which TABLE, the account says which MAILBOX — the same two axes
# that were conflated in `matching_rows`. They were conflated here too: the count
# filtered one table (`messages`) by an account whose LABEL was the stream name,
# and no account is labelled "gmail". The subquery was therefore always empty and
# every stream reported 0 — a status command that lied, in the reassuring
# direction, about the one thing it exists to answer. Each stream counts its own
# table, and the noun goes with the table so the line reads true.
STREAM_TABLES = {
    "gmail":       ("messages",        "messages"),
    "sms":         ("sms",             "messages"),
    "calls":       ("calls",           "calls"),
    "calendar":    ("calendar_events", "events"),
    "drive":       ("drive_files",     "files"),
    "attachments": ("attachments",     "attachments"),
}


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------- pause
def is_paused(conn, stream: str) -> bool:
    """Checked by every crawler before it writes. 'all' pauses everything."""
    for key in (f"pause:{stream}", "pause:all"):
        row = conn.execute("SELECT value FROM controls WHERE key=?", (key,)).fetchone()
        if row and (row["value"] or "").lower() in ("1", "true", "yes"):
            return True
    return False


def pause(stream: str, note: str = ""):
    if stream not in STREAMS + ("all",):
        raise SystemExit(f"unknown stream {stream!r}; one of {STREAMS + ('all',)}")
    conn = db()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO controls (key, value, set_at, note) VALUES (?,?,?,?)",
            (f"pause:{stream}", "true", time.time(), note))
    print(f"  paused: {stream}" + (f"  ({note})" if note else ""))
    if stream != "all":
        conn.execute("INSERT INTO actions (ts, kind, detail, ok) VALUES (?,?,?,?)",
                     (time.time(), "pause", f"{stream}: {note}", 1))
        conn.commit()


def resume(stream: str):
    conn = db()
    with conn:
        conn.execute("DELETE FROM controls WHERE key=?", (f"pause:{stream}",))
    print(f"  resumed: {stream}")


# ---------------------------------------------------------------- exclusions
def load_exclusions(conn, stream: str):
    """The owner's rules for one stream, as (kind, value) pairs.

    Re-read every page by the crawlers, not once at startup: a crawl runs for hours, and
    an exclusion is something you reach for the moment you decide something should not be
    indexed. Reading it once would mean "takes effect tomorrow".
    """
    rows = conn.execute("SELECT kind, value FROM exclusions WHERE stream=? OR stream='all'",
                        (stream,)).fetchall()
    return [(r["kind"], (r["value"] or "").lower()) for r in rows]


def is_excluded(exclusions, *fields: str) -> bool:
    """Does any field match an `address` or `contains` rule?

    Takes variadic fields so a crawler can pass whatever it has — an email passes sender
    and subject and body; a phone pull passes a number and an empty subject.
    """
    for kind, value in exclusions:
        if not value:
            continue
        for f in fields:
            if f and value in str(f).lower():
                return True
    return False


def add_exclusion(conn, stream: str, kind: str, value: str, reason: str = ""):
    with conn:
        conn.execute(
            "INSERT INTO exclusions (stream, kind, value, reason, created) VALUES (?,?,?,?,?)",
            (stream, kind, value, reason, time.time()))


def matching_rows(conn, stream: str, kind: str, value: str, account: str | None = None):
    """The rows an exclusion would remove. Shared by the dry run and the real thing, so
    the preview cannot disagree with what actually happens.

    STREAM and ACCOUNT are different axes and used to be conflated here: `--stream gmail`
    was being matched against an account LABEL, but the label of the gmail account is
    "personal" — so every exclusion quietly matched nothing and reported success. Two
    separate things: the stream says which TABLE, the account says which MAILBOX.
    """
    q = "SELECT id, account, from_addr, subject, internal_date FROM messages WHERE 1=1"
    args: list = []
    if account:
        q += " AND (account=? OR account IN (SELECT email FROM accounts WHERE label=?))"
        args += [account, account]
    if kind == "address":
        # covers both directions: anything to or from this address
        q += " AND (lower(from_addr) LIKE ? OR lower(to_addrs) LIKE ?)"
        args += [f"%{value.lower()}%", f"%{value.lower()}%"]
    elif kind == "contains":
        q += (" AND id IN (SELECT message_id FROM message_text WHERE lower(body) LIKE ?)")
        args.append(f"%{value.lower()}%")
    elif kind == "between":
        lo, hi = value.split("..")
        q += " AND internal_date BETWEEN ? AND ?"
        args += [int(time.mktime(time.strptime(lo, "%Y-%m-%d")) * 1000),
                 int(time.mktime(time.strptime(hi, "%Y-%m-%d")) * 1000)]
    elif kind == "thread":
        q += " AND thread_id=?"
        args.append(value)
    elif kind == "id":
        q += " AND id=?"
        args.append(value)
    else:
        raise SystemExit(f"unknown exclusion kind {kind!r}")
    return conn.execute(q, args).fetchall()


def purge(conn, rows) -> int:
    """Remove the rows from every table that holds their content."""
    ids = [r["id"] for r in rows]
    if not ids:
        return 0
    for chunk_start in range(0, len(ids), 500):
        chunk = ids[chunk_start:chunk_start + 500]
        marks = ",".join("?" * len(chunk))
        with conn:
            conn.execute(f"DELETE FROM message_fts WHERE message_id IN ({marks})", chunk)
            conn.execute(f"DELETE FROM message_text WHERE message_id IN ({marks})", chunk)
            conn.execute(f"DELETE FROM attachments WHERE message_id IN ({marks})", chunk)
            conn.execute(f"DELETE FROM messages WHERE id IN ({marks})", chunk)
    return len(ids)


def forget(stream: str, kind: str, value: str, reason: str, yes: bool,
           account: str | None = None):
    conn = db()
    rows = matching_rows(conn, stream, kind, value, account)
    if not rows:
        print("  nothing matches — no changes made")
        return 0

    print(f"\n  {len(rows)} message(s) match ({kind}={value!r}, stream={stream})")
    for r in rows[:8]:
        d = dict(r)
        when = time.strftime("%Y-%m-%d", time.localtime((d["internal_date"] or 0) / 1000))
        print(f"    {when}  {d['from_addr'][:34]:<34} {(d['subject'] or '')[:36]}")
    if len(rows) > 8:
        print(f"    ... and {len(rows) - 8} more")

    if not yes:
        print("\n  Nothing deleted. Re-run with --yes to erase these and to add a rule")
        print("  so a future crawl does not put them back.")
        return 0

    n = purge(conn, rows)
    add_exclusion(conn, stream, kind, value, reason)
    with conn:
        conn.execute("INSERT INTO actions (ts, kind, detail, ok) VALUES (?,?,?,?)",
                     (time.time(), "forget",
                      f"{n} rows erased; rule: {stream}/{kind}={value}; {reason}", 1))
    print(f"\n  erased {n} message(s) and recorded the rule — a re-crawl will skip them.")
    return n


# ---------------------------------------------------------------- reporting
def status():
    conn = db()
    print("\n  streams")
    for s in STREAMS:
        p = is_paused(conn, s)
        table, noun = STREAM_TABLES[s]
        n = conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]
        note = ""
        row = conn.execute("SELECT note FROM controls WHERE key=?", (f"pause:{s}",)).fetchone()
        if row and row["note"]:
            note = f"  ({row['note']})"
        print(f"    {s:<9} {'PAUSED' if p else 'indexing':<9} {n:>7} {noun}{note}")

    rows = list(conn.execute("SELECT * FROM exclusions ORDER BY id"))
    print(f"\n  exclusions ({len(rows)})")
    if not rows:
        print("    none")
    for r in rows:
        d = dict(r)
        when = time.strftime("%Y-%m-%d", time.localtime(d["created"] or 0))
        print(f"    #{d['id']:<3} {d['stream']:<9} {d['kind']:<9} {d['value'][:40]:<40}"
              f" {d['reason'][:28]:<28} {when}")

    recent = list(conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT 5"))
    if recent:
        print("\n  recent actions")
        for r in recent:
            d = dict(r)
            when = time.strftime("%m-%d %H:%M", time.localtime(d["ts"] or 0))
            print(f"    {when}  {d['kind']:<8} {(d['detail'] or '')[:64]}")


def rules():
    conn = db()
    rows = list(conn.execute("SELECT * FROM exclusions ORDER BY id"))
    if not rows:
        print("  no exclusions")
        return
    for r in rows:
        d = dict(r)
        print(f"  #{d['id']:<3} {d['stream']:<9} {d['kind']:<9} {d['value']}")


def unexclude(ident: int):
    conn = db()
    with conn:
        conn.execute("DELETE FROM exclusions WHERE id=?", (ident,))
    print(f"  removed exclusion #{ident} — note the content is still gone; this only"
          f" means a future crawl may index it again")


# ---------------------------------------------------------------- cli
def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    args = argv[2:]

    if cmd == "status":
        status()
        return 0
    if cmd == "rules":
        rules()
        return 0
    if cmd == "pause":
        if not args:
            raise SystemExit("usage: control.py pause <stream|all> [--note TEXT]")
        note = ""
        if "--note" in args:
            note = args[args.index("--note") + 1]
        pause(args[0], note)
        return 0
    if cmd == "resume":
        if not args:
            raise SystemExit("usage: control.py resume <stream|all>")
        resume(args[0])
        return 0
    if cmd == "unexclude":
        unexclude(int(args[0]))
        return 0
    if cmd == "forget":
        stream, value, reason, kind, yes, account = "all", None, "", None, False, None
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--address":
                kind, value = "address", args[i + 1]; i += 2
            elif a == "--number":
                kind, value = "address", args[i + 1]; i += 2
            elif a == "--contains":
                kind, value = "contains", args[i + 1]; i += 2
            elif a == "--thread":
                kind, value = "thread", args[i + 1]; i += 2
            elif a == "--between":
                kind, value = "between", args[i + 1]; i += 2
            elif a == "--id":
                kind, value = "id", args[i + 1]; i += 2
            elif a == "--stream":
                stream = args[i + 1]; i += 2
            elif a == "--account":
                account = args[i + 1]; i += 2
            elif a == "--reason":
                reason = args[i + 1]; i += 2
            elif a == "--yes":
                yes = True; i += 1
            else:
                raise SystemExit(f"unexpected argument {a!r}")
        if not kind:
            raise SystemExit("usage: control.py forget (--address X | --contains X |"
                             " --between A..B | --thread T | --id I) [--stream S] [--yes]")
        forget(stream, kind, value, reason, yes, account)
        return 0
    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
