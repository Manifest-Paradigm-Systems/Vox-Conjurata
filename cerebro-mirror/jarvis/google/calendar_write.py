"""Writing to the calendar — the first thing in this module that changes anything.

Read-only was a design choice rather than a stage we were passing through, so
this file is the deliberate crossing of a line that `auth.py` describes exactly:
escalating to writes is "a scope change plus a re-consent, not a rewrite".

FOUR RULES, each of which is load-bearing:

1. WRITE FROM INTENT, NEVER FROM AN INDEXED ROW. The index cannot round-trip an
   event: `_ts()` converts times to epoch and discards the zone, `recurring` is a
   boolean with the RRULE thrown away, reminders and visibility are not stored at
   all, `calendar_id` is hardcoded "primary", and an all-day event's end is kept
   raw even though Google's is exclusive. Reconstructing an event from it would
   be a lossy guess dressed as a fact. The index is for identity and read-back;
   the resource sent to Google is built from what the human actually asked for.

2. SHOW FIRST, THEN ACT, AND SEND THE SAME OBJECT. `propose_*` returns the exact
   resource; `send()` transmits that identical object after a confirmation. The
   preview cannot disagree with the write, which is the property `control.py`
   documents for `forget` and the reason its preview and its purge share a query.

3. WRITES ARE AUDITED, ALWAYS. Reads are not logged — there are too many — but
   `actions` is explicit that writes are. Every send, successful or not.

4. NOTHING HERE RUNS ON A TIMER. The indexers are unattended; this must never
   be. There is no `--yes` default and no scheduled entry point.

The one thing it cannot do for you: `calendar.events` is a *sensitive* scope, and
the consent screen is External and Published. Google may require a verification
review before granting it. If `auth.py check` reports the scope as ungranted,
that is a console problem, not a code one.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth          # noqa: E402
import calendar_index as ci   # noqa: E402
import control       # noqa: E402

DEFAULT_ACCOUNT = os.environ.get("JARVIS_MAIL_ACCOUNT", "unconfigured@invalid")


def _iso(when: str) -> str:
    """Accept a few human shapes and return RFC3339 in local time.

    Deliberately strict about the time being PRESENT. Google will happily accept
    a date-only value and make the event all-day, which is a silent change to
    what someone meant when they said "2pm".
    """
    when = (when or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(when, fmt).astimezone().isoformat(timespec="seconds")
        except ValueError:
            continue
    try:
        return datetime.datetime.fromisoformat(when).astimezone().isoformat(timespec="seconds")
    except ValueError:
        raise SystemExit(f"could not read the time {when!r} — use 'YYYY-MM-DD HH:MM'")


def build_event(summary: str, start: str, end: str, *, description: str = "",
                location: str = "", attendees: list[str] | None = None,
                calendar_id: str = "primary") -> dict:
    """The exact resource that will be sent. No index data, no inferred fields.

    Times carry an explicit offset, and every field here is one the human asked
    for. Anything not asked for is omitted rather than defaulted — a default is a
    decision nobody made.
    """
    if not (summary or "").strip():
        raise SystemExit("an event needs a summary")
    body: dict = {
        "summary": summary.strip(),
        "start": {"dateTime": _iso(start)},
        "end": {"dateTime": _iso(end)},
    }
    if description.strip():
        body["description"] = description.strip()
    if location.strip():
        body["location"] = location.strip()
    if attendees:
        body["attendees"] = [{"email": a.strip()} for a in attendees if a.strip()]
    return {"kind": "create", "calendar_id": calendar_id, "body": body}


def build_move(event_id: str, start: str, end: str,
               calendar_id: str = "primary", summary: str | None = None) -> dict:
    """A PATCH of just the times — plus the summary only if it is changing.

    PATCH removes the fields it is given, so sending only what changed is what
    keeps the description, attendees and reminders intact.
    """
    raw_id = event_id.split(":", 1)[1] if ":" in event_id else event_id
    body: dict = {"start": {"dateTime": _iso(start)}, "end": {"dateTime": _iso(end)}}
    if summary is not None:
        body["summary"] = summary
    return {"kind": "move", "calendar_id": calendar_id, "event_id": raw_id, "body": body}


def describe(proposal: dict) -> str:
    """The human-readable preview, built from the SAME object that gets sent."""
    b = proposal["body"]
    lines = [f"  action   : {proposal['kind']}"]
    if proposal["kind"] == "move":
        lines.append(f"  event    : {proposal['event_id']}")
        lines.append("  (only the times below are changed; everything else on the "
                     "event is left alone)")
    if b.get("summary"):
        lines.append(f"  summary  : {b['summary']}")
    lines.append(f"  starts   : {b['start']['dateTime']}")
    lines.append(f"  ends     : {b['end']['dateTime']}")
    if b.get("location"):
        lines.append(f"  where    : {b['location']}")
    if b.get("attendees"):
        who = ", ".join(a["email"] for a in b["attendees"])
        lines.append(f"  invites  : {who}   <- these people will be emailed by Google")
    if b.get("description"):
        lines.append(f"  notes    : {b['description'][:120]}")
    return "\n".join(lines)


def _audit(kind: str, detail: str, ok: bool) -> None:
    """Writes always leave a row. The schema is explicit: reads are not logged,
    writes are."""
    try:
        conn = control.db()
        with conn:
            conn.execute("INSERT INTO actions (ts, kind, detail, ok) VALUES (?,?,?,?)",
                         (time.time(), kind, detail[:400], 1 if ok else 0))
    except sqlite3.Error as exc:
        # A failed audit must not undo a write that already happened at Google.
        print(f"  !! could not write the audit row: {exc}", file=sys.stderr)


def send(proposal: dict, yes: bool, account: str = DEFAULT_ACCOUNT) -> dict | None:
    """Transmit a proposal. Does nothing without `yes`, and prints it first.

    Returns Google's event resource on success, or None if nothing was sent.
    """
    print("\n  This would be sent to your calendar:")
    print(describe(proposal))
    if not yes:
        print("\n  Nothing was sent. Re-run with --yes to do it.")
        return None

    token = auth.TokenSource(account)
    cal = proposal["calendar_id"]
    try:
        if proposal["kind"] == "create":
            result = ci._request(token, "POST", f"/calendars/{cal}/events",
                                 body=proposal["body"])
        else:
            result = ci._request(token, "PATCH",
                                 f"/calendars/{cal}/events/{proposal['event_id']}",
                                 body=proposal["body"])
    except SystemExit as exc:
        # A write that failed ambiguously is reported, never retried here — see
        # the note in calendar_index._request. Whether it landed must be checked
        # by reading the calendar back, not by assuming.
        _audit(f"calendar_{proposal['kind']}", f"FAILED: {exc}", False)
        print(f"\n  FAILED: {exc}", file=sys.stderr)
        print("  Check the calendar before retrying — the write may have landed.",
              file=sys.stderr)
        return None

    _audit(f"calendar_{proposal['kind']}",
           f"{proposal['body'].get('summary', proposal.get('event_id', ''))} "
           f"@ {proposal['body']['start']['dateTime']}", True)
    print(f"\n  done — event {result.get('id', '(no id returned)')} "
          f"({result.get('htmlLink', '')})")
    return result


# ------------------------------------------------------------------ cli

def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        print("usage: calendar_write.py create --summary S --start 'YYYY-MM-DD HH:MM' "
              "--end '...' [--location L] [--description D] [--attendee EMAIL]... [--yes]")
        print("       calendar_write.py move   --event <id> --start '...' --end '...' [--yes]")
        return 1

    def opt(name: str, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    def multi(name: str) -> list[str]:
        out = []
        for i, a in enumerate(argv):
            if a == name and i + 1 < len(argv):
                out.append(argv[i + 1])
        return out

    yes = "--yes" in argv
    cmd = argv[1]

    if cmd == "create":
        proposal = build_event(
            opt("--summary", ""), opt("--start", ""), opt("--end", ""),
            description=opt("--description", "") or "",
            location=opt("--location", "") or "",
            attendees=multi("--attendee"))
    elif cmd == "move":
        proposal = build_move(opt("--event", ""), opt("--start", ""), opt("--end", ""),
                              summary=opt("--summary"))
    else:
        raise SystemExit(f"unknown command {cmd!r}")

    return 0 if send(proposal, yes) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
