"""Index a Google Calendar account. READ-ONLY against Google.

Small and fast — a few thousand events at most — but it answers a question nothing else
in this database can: **who do I actually spend time with?**

The attendee list is the join that turns a pile of addresses into relationships. Email
tells you who writes to you; a calendar tells you who you sit in a room with, and those
are different populations. Somebody who never emails you but is in your weekly standup is
a real colleague; somebody who emails you 500 times is often a newsletter.

Two details that matter more than they look:

* **singleEvents=true.** A recurring meeting is expanded into individual occurrences, so
  "we meet every Monday" becomes forty data points rather than one. Counting recurrence
  as one meeting would understate exactly the relationships that matter most.

* **attendee_count is stored.** Your spouse in a 2-person dinner and row 31 of a
  forty-person all-hands are not the same signal, and only the count can tell them apart.

Usage:
    python3 calendar_index.py run <account-email> [--years 3]
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
import auth     # noqa: E402
import control  # noqa: E402

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))
API = "https://www.googleapis.com/calendar/v3"
PAGE = 250
REQUEST_DELAY = float(os.getenv("JARVIS_REQUEST_DELAY", "0.08"))

EVENT_FIELDS = ("nextPageToken,items(id,summary,description,location,start,end,"
                "organizer,attendees(email,displayName,responseStatus,organizer,self),"
                "status,recurringEventId)")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=180)
    conn.row_factory = sqlite3.Row
    # Patience: the mail crawler is writing to this database at the same time and holds
    # the write lock for a whole page. A 30s timeout loses that race repeatedly and reads
    # like a bug in this crawler rather than a busy neighbour.
    conn.execute("PRAGMA busy_timeout = 180000")
    return conn


def _request(token, method: str, path: str, params: dict | None = None,
             body: dict | None = None, tries: int = 8):
    """One authenticated Calendar call: GET, POST, PATCH or DELETE.

    GET was the only verb here until writes arrived. Two things change when a
    method and a body appear, and the second is the one that matters:

    RETRIES ARE FOR IDEMPOTENT CALLS ONLY. A GET that times out did not happen.
    A POST that times out may well have succeeded — retrying it books the
    appointment twice. So a write gets exactly one attempt, and an ambiguous
    failure is raised for a human rather than swallowed by a retry loop that
    cannot know whether it already worked.
    """
    time.sleep(REQUEST_DELAY)
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    idempotent = method in ("GET", "PUT", "PATCH", "DELETE")

    delay = 1.0
    for attempt in range(tries if idempotent else 1):
        value = token.get() if hasattr(token, "get") else token
        send = {"Authorization": f"Bearer {value}"}
        if data is not None:
            send["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=send)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read(400).decode("utf-8", "replace")
            if exc.code == 401 or "UNAUTHENTICATED" in detail:
                if hasattr(token, "get") and idempotent:
                    token.get(force=True)
                    continue
            if exc.code in (403, 429, 500, 502, 503) and idempotent and attempt < tries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            raise SystemExit(f"{exc.code} on {method} {path}: {detail[:300]}")
    raise SystemExit(f"calendar: exhausted retries on {method} {path}")


def api_get(token, path: str, params: dict | None = None, tries: int = 8):
    """Paced, with the error body kept — see gmail_index.py for why both matter."""
    return _request(token, "GET", path, params, None, tries)


def _ts(block: dict | None) -> tuple[int, int]:
    """(epoch millis, is_all_day). Google gives either dateTime or a bare date."""
    if not block:
        return 0, 0
    if block.get("dateTime"):
        raw = block["dateTime"].replace("Z", "+00:00")
        try:
            import datetime
            dt = datetime.datetime.fromisoformat(raw)
            return int(dt.timestamp() * 1000), 0
        except ValueError:
            return 0, 0
    if block.get("date"):
        try:
            import datetime
            dt = datetime.datetime.strptime(block["date"], "%Y-%m-%d")
            return int(dt.timestamp() * 1000), 1
        except ValueError:
            return 0, 0
    return 0, 0


def index_account(email: str, years: int = 3):
    conn = db()
    if control.is_paused(conn, "calendar"):
        print("  calendar indexing is PAUSED — nothing written.")
        return 0
    if not conn.execute("SELECT 1 FROM accounts WHERE email=?", (email,)).fetchone():
        raise SystemExit(f"{email} is not in the accounts table — add it first")

    token = auth.TokenSource(email)
    exclusions = control.load_exclusions(conn, "calendar")

    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    time_min = (now - datetime.timedelta(days=365 * years)).isoformat()
    time_max = (now + datetime.timedelta(days=365)).isoformat()

    page, events, attendees = None, 0, 0
    while True:
        params = {"timeMin": time_min, "timeMax": time_max, "singleEvents": "true",
                  "orderBy": "startTime", "maxResults": PAGE, "fields": EVENT_FIELDS}
        if page:
            params["pageToken"] = page
        data = api_get(token, "/calendars/primary/events", params)
        items = (data or {}).get("items", [])
        if not items and not page:
            print("  no events returned — is the calendar empty, or the range wrong?")
            break

        for ev in items:
            eid = f"{email}:{ev.get('id')}"
            start_ts, all_day = _ts(ev.get("start"))
            end_ts, _ = _ts(ev.get("end"))
            people = [a for a in (ev.get("attendees") or []) if a.get("email")]
            summary = ev.get("summary") or ""

            if control.is_excluded(exclusions, summary, ev.get("location") or ""):
                continue

            conn.execute(
                "INSERT OR REPLACE INTO calendar_events (id, account, calendar_id,"
                " summary, description, location, start_ts, end_ts, all_day, organizer,"
                " ev_status, recurring, attendee_count, indexed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (eid, email, "primary", summary,
                 (ev.get("description") or "")[:20000], ev.get("location") or "",
                 start_ts, end_ts, all_day,
                 ((ev.get("organizer") or {}).get("email") or ""),
                 ev.get("status") or "", 1 if ev.get("recurringEventId") else 0,
                 len(people), time.time()))
            events += 1

            for a in people:
                addr = (a.get("email") or "").lower()
                if not addr or control.is_excluded(exclusions, addr):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO event_attendees (event_id, account, email,"
                    " display_name, response_status, is_organizer, is_self)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (eid, email, addr, a.get("displayName") or "",
                     a.get("responseStatus") or "",
                     1 if a.get("organizer") else 0, 1 if a.get("self") else 0))
                attendees += 1

        conn.commit()
        page = (data or {}).get("nextPageToken")
        print(f"  {events} events, {attendees} attendee rows...")
        if not page:
            break

    print(f"\ndone: {events} events, {attendees} attendee rows")
    return events


def stats(email: str):
    conn = db()
    e = conn.execute("SELECT COUNT(*) c FROM calendar_events WHERE account=?",
                     (email,)).fetchone()["c"]
    a = conn.execute("SELECT COUNT(*) c FROM event_attendees WHERE account=?",
                     (email,)).fetchone()["c"]
    people = conn.execute("SELECT COUNT(DISTINCT email) c FROM event_attendees"
                          " WHERE account=? AND is_self=0", (email,)).fetchone()["c"]
    print(f"  {email}: {e} events, {a} attendee rows, {people} distinct people")
    print("\n  people you have shared the most meetings with:")
    for r in conn.execute("""
        SELECT email, display_name, COUNT(*) n,
               SUM(CASE WHEN (SELECT attendee_count FROM calendar_events
                              WHERE id = event_attendees.event_id) <= 3
                        THEN 1 ELSE 0 END) AS small_meetings
        FROM event_attendees WHERE account=? AND is_self=0
        GROUP BY email ORDER BY n DESC LIMIT 10""", (email,)):
        d = dict(r)
        print(f"    {d['n']:>4} meetings ({d['small_meetings']:>3} small)  "
              f"{(d['display_name'] or d['email'])[:44]}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "run":
        if len(argv) < 3:
            raise SystemExit("usage: calendar_index.py run <email> [--years N]")
        years = int(argv[argv.index("--years") + 1]) if "--years" in argv else 3
        index_account(argv[2], years=years)
        return 0
    if cmd == "stats":
        stats(argv[2])
        return 0
    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
