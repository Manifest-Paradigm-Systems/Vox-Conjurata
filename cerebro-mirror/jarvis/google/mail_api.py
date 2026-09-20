"""mail-api — read access to the mail archive, and to Gmail itself.

The index has been write-only since it was built: `gmail_index.py` fills
`message_fts` and nothing reads it. This is the read end, as a small service so
that Jarvis — which runs on cerebro, not on this box — can ask questions of it.

ARCHIVE FIRST. `/mail/search` answers from the local index by default and only
falls through to Gmail when the index has nothing. The local answer is instant,
works offline, and covers 100k messages; the live answer is slower and paginated,
so it is the fallback rather than the default. The response always says which one
answered (`source`) and how fresh it is (`fresh_as_of`), because a search that
cannot distinguish "the archive has nothing" from "the archive is three days
behind" is a search that will be trusted wrongly.

NEVER SILENT. A database that will not open is a 503, not an empty result list.
The one thing this service must never do is make a failure look like an absence —
the whole fleet has paid for that confusion repeatedly. `/health` probes the
database rather than reporting a cheerful 200 for a process that can no longer
read anything.

BIND 0.0.0.0, BUT NOT OPEN. The other services in this fleet bind the LAN with no
auth, which is fine for TTS. This one reads medical records, a child's education
file and a brother's health correspondence, so it carries a bearer token — the
one service here where "who is on the network" is not an acceptable answer to
"who may read this".
"""

import os
import secrets
import sqlite3
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

import document_search
import mail_search

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))
DEFAULT_ACCOUNT = os.environ.get("JARVIS_MAIL_ACCOUNT", "mnmeyer@gmail.com")

# Fail closed, loudly. A service that starts without a token and serves anyway is
# worse than one that refuses to start, because nothing looks wrong.
TOKEN = os.getenv("JARVIS_MAIL_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError(
        "JARVIS_MAIL_TOKEN is not set — refusing to serve the mail archive "
        "unauthenticated. Set it in the unit's EnvironmentFile (see "
        "systemd/mail-api.service) and in the brain's brain.env.")

app = FastAPI(title="mail-api (Jarvis mail archive + live Gmail read)")


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Bearer token, compared in constant time."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    if not secrets.compare_digest(authorization[7:].strip(), TOKEN):
        raise HTTPException(status_code=403, detail="bad bearer token")


def _conn() -> sqlite3.Connection:
    return mail_search.connect(DB_PATH)


def _freshness(conn) -> float | None:
    """When the archive was last brought up to date, as an epoch second.

    Read from sync_state rather than guessed from file mtime: mtime moves when
    anything touches the file, and this number is what tells Jarvis whether to
    trust the answer or reach for the live account.
    """
    try:
        row = conn.execute(
            "SELECT MAX(last_ok) AS t FROM sync_state"
            " WHERE stream IN ('gmail','replygraph')").fetchone()
    except sqlite3.Error:
        return None
    return row["t"] if row and row["t"] else None


def _shape(row) -> dict:
    when = row["internal_date"] or 0
    return {
        "id": row["id"],
        "account": row["account"],
        "thread_id": row["thread_id"],
        "date": time.strftime("%Y-%m-%d", time.localtime(when / 1000)) if when else "",
        "from": row["from_addr"] or "",
        "subject": row["subject"] or "",
        "snippet": (row["snip"] or row["snippet"] or "").strip(),
        "has_attachment": bool(row["has_attachment"]),
    }


# --------------------------------------------------------------- introspection

@app.get("/")
async def root():
    return {"service": "mail-api", "db": DB_PATH, "default_account": DEFAULT_ACCOUNT}


@app.get("/health")
async def health():
    """Probe the database. A 200 here must mean it can actually be read."""
    def _probe():
        conn = _conn()
        try:
            n = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
            # Documents coverage rides on the health line deliberately. "2,713 PDFs
            # unread" is the single most useful number about this index, and a number
            # nobody looks at is how a coverage gap survived for weeks unnoticed.
            return n, _freshness(conn), document_search.stats(conn)
        finally:
            conn.close()

    try:
        n, fresh, docs = await run_in_threadpool(_probe)
    except sqlite3.Error as exc:
        return JSONResponse(
            {"status": "degraded", "db": DB_PATH,
             "error": f"{type(exc).__name__}: {exc}"}, status_code=503)
    return {"status": "ok", "db": DB_PATH, "messages": n, "fresh_as_of": fresh,
            "documents": docs}


# --------------------------------------------------------------- search

@app.get("/mail/search", dependencies=[Depends(require_token)])
async def mail_search_endpoint(
        q: str = Query(..., min_length=1, description="what to look for"),
        account: str | None = Query(default=None),
        limit: int = Query(default=8, ge=1, le=50),
        mode: str = Query(default="auto", pattern="^(auto|archive|live)$")):
    """Archive first; live only when the archive cannot answer (mode=auto)."""
    t0 = time.time()

    hits: list[dict] = []
    error: str | None = None
    freshness: float | None = None
    source = "none"

    if mode in ("auto", "archive"):
        def _archive():
            conn = _conn()
            try:
                h, e = mail_search.search_archive(conn, q, account, limit)
                return [_shape(r) for r in h], e, _freshness(conn)
            finally:
                conn.close()
        try:
            hits, error, freshness = await run_in_threadpool(_archive)
        except sqlite3.Error as exc:
            # An unreadable archive is NOT an empty archive.
            return JSONResponse(
                {"error": "archive_unavailable",
                 "detail": f"{type(exc).__name__}: {exc}",
                 "hint": "the index database could not be read; this does NOT mean "
                         "there is no matching mail"}, status_code=503)
        if hits:
            source = "archive"

    live_error: str | None = None
    if not hits and mode in ("auto", "live"):
        msgs, live_error = await run_in_threadpool(
            mail_search.search_live, account or DEFAULT_ACCOUNT, q, limit)
        if msgs:
            source = "live"
            hits = msgs
            freshness = time.time()

    return {
        "source": source,
        "query": q,
        "count": len(hits),
        "fresh_as_of": freshness,
        "results": hits,
        "error": error,
        "live_error": live_error,
        "elapsed_ms": int((time.time() - t0) * 1000),
    }


@app.get("/mail/read", dependencies=[Depends(require_token)])
async def mail_read(id: str = Query(..., min_length=1)):
    """One message, full body, from the archive."""
    def _read():
        conn = _conn()
        try:
            return mail_search.read_message(conn, id)
        finally:
            conn.close()

    try:
        d = await run_in_threadpool(_read)
    except sqlite3.Error as exc:
        return JSONResponse({"error": "archive_unavailable",
                             "detail": f"{type(exc).__name__}: {exc}"}, status_code=503)
    if d is None:
        raise HTTPException(status_code=404, detail="not in the archive")
    if d.get("excluded"):
        # The rule is the owner's, and it outranks a lookup. Say so rather than
        # returning the body or pretending the message does not exist.
        raise HTTPException(status_code=403,
                            detail="this message is covered by an exclusion rule")
    when = d.get("internal_date") or 0
    return {
        "id": d["id"], "account": d.get("account"),
        "date": time.strftime("%Y-%m-%d", time.localtime(when / 1000)) if when else "",
        "from": d.get("from_addr") or "", "to": d.get("to_addrs") or "",
        "subject": d.get("subject") or "", "body": d.get("body") or "",
        "has_attachment": bool(d.get("has_attachment")),
    }


@app.get("/mail/live", dependencies=[Depends(require_token)])
async def mail_live(q: str = Query(..., min_length=1),
                    account: str | None = Query(default=None),
                    limit: int = Query(default=8, ge=1, le=25)):
    """Gmail directly. Exclusions are applied by mail_search, same as the archive."""
    msgs, error = await run_in_threadpool(
        mail_search.search_live, account or DEFAULT_ACCOUNT, q, limit)
    if error:
        return JSONResponse({"source": "live", "error": "live_unavailable",
                             "detail": error, "results": [],
                             "hint": "this is a failure to reach Gmail, not an "
                                     "empty mailbox"}, status_code=502)
    return {"source": "live", "query": q, "count": len(msgs),
            "fresh_as_of": time.time(), "results": msgs, "error": None}


# --------------------------------------------------------------- calendar
# Same index, same database, same token as the mail routes above — the calendar
# is not a second service. The name `mail-api` is now narrower than the job it
# does; renaming it touches this unit, mail.env, brain.env and the brain, so it
# is a deliberate change rather than a side effect of adding these two routes.

_EVENT_COLS = ("e.id, e.account, e.summary, e.description, e.location, e.start_ts, e.end_ts,"
               " e.all_day, e.organizer, e.ev_status, e.attendee_count,"
               " (SELECT group_concat(a.email, ', ') FROM event_attendees a"
               "   WHERE a.event_id = e.id AND a.is_self = 0) AS attendees")


def _event_shape(row) -> dict:
    """One event, with its time rendered as words.

    Epoch millis are useless to a model asked "what is on Thursday" — it cannot do
    the arithmetic and, asked anyway, it guesses. Measured today: told the correct
    date it still said the 25th fell on a Saturday when it falls on a Friday. So
    the day name is worked out HERE, once, where the clock and the locale are,
    and handed over as text.

    Same rule as the mail lane returning raw excerpts rather than a summary: give
    the speaker facts, not material to infer from.
    """
    start = row["start_ts"] or 0
    end = row["end_ts"] or 0
    all_day = bool(row["all_day"])
    day = time.strftime("%A %-d %B %Y", time.localtime(start / 1000)) if start else "date unknown"
    if all_day:
        span = "all day"
    elif start and end:
        span = (time.strftime("%H:%M", time.localtime(start / 1000)) + "–"
                + time.strftime("%H:%M", time.localtime(end / 1000)))
    else:
        span = "time unknown"
    return {
        "id": row["id"],
        "summary": row["summary"] or "(no title)",
        "when": f"{day}, {span}",
        "day": day,
        "start_ts": start,
        "end_ts": end,
        "all_day": all_day,
        "location": row["location"] or "",
        "description": (row["description"] or "")[:400],
        "organizer": row["organizer"] or "",
        "status": row["ev_status"] or "",
        "attendees": row["attendees"] or "",
    }


@app.get("/calendar/upcoming", dependencies=[Depends(require_token)])
async def calendar_upcoming(days: int = Query(default=7, ge=1, le=365),
                            account: str | None = Query(default=None),
                            limit: int = Query(default=25, ge=1, le=100)):
    """Events still to come, soonest first. Cancelled ones are excluded."""
    now_ms = int(time.time() * 1000)
    until_ms = now_ms + days * 86_400_000

    def _query():
        conn = _conn()
        try:
            sql = (f"SELECT {_EVENT_COLS} FROM calendar_events e"
                   " WHERE e.ev_status != 'cancelled'"
                   "   AND e.end_ts >= ? AND e.start_ts <= ?")
            args: list = [now_ms, until_ms]
            if account:
                sql += " AND e.account = ?"
                args.append(account)
            sql += " ORDER BY e.start_ts LIMIT ?"
            args.append(limit)
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    try:
        rows = await run_in_threadpool(_query)
    except sqlite3.Error as exc:
        return JSONResponse(
            {"error": "archive_unavailable", "detail": f"{type(exc).__name__}: {exc}",
             "hint": "the index could not be read; this does NOT mean the calendar "
                     "is empty"}, status_code=503)
    return {"source": "archive", "days": days, "count": len(rows),
            "today": time.strftime("%A %-d %B %Y", time.localtime(now_ms / 1000)),
            "results": [_event_shape(r) for r in rows], "error": None}


@app.get("/calendar/search", dependencies=[Depends(require_token)])
async def calendar_search(q: str = Query(..., min_length=1),
                          account: str | None = Query(default=None),
                          limit: int = Query(default=15, ge=1, le=100)):
    """Find events by title, place or description, past or future.

    Ordering is by distance from now rather than by date, so "when is Mike's
    gastro appointment" answers with the nearest one rather than the oldest ever.
    """
    like = f"%{q.lower()}%"
    now_ms = int(time.time() * 1000)

    def _query():
        conn = _conn()
        try:
            sql = (f"SELECT {_EVENT_COLS} FROM calendar_events e"
                   " WHERE e.ev_status != 'cancelled'"
                   "   AND (lower(COALESCE(e.summary,'')) LIKE ?"
                   "     OR lower(COALESCE(e.location,'')) LIKE ?"
                   "     OR lower(COALESCE(e.description,'')) LIKE ?)")
            args: list = [like, like, like]
            if account:
                sql += " AND e.account = ?"
                args.append(account)
            sql += " ORDER BY abs(e.start_ts - ?) LIMIT ?"
            args += [now_ms, limit]
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    try:
        rows = await run_in_threadpool(_query)
    except sqlite3.Error as exc:
        return JSONResponse(
            {"error": "archive_unavailable", "detail": f"{type(exc).__name__}: {exc}",
             "hint": "the index could not be read; this does NOT mean nothing "
                     "matches"}, status_code=503)
    events = [_event_shape(r) for r in rows]
    return {"source": "archive", "query": q, "count": len(events),
            "today": time.strftime("%A %-d %B %Y", time.localtime(now_ms / 1000)),
            "results": events, "error": None}


# --------------------------------------------------------------- documents
# Served from THIS service rather than a second one. Same database, same token, same
# loopback bind, same venv — the precedent and its reasoning are already written down
# above for the calendar routes, and the name `mail-api` was already narrower than the
# job it does before documents were added.

@app.get("/documents/search", dependencies=[Depends(require_token)])
async def documents_search(q: str = Query(..., min_length=1),
                           account: str | None = Query(default=None),
                           limit: int = Query(default=8, ge=1, le=50),
                           source: str | None = Query(
                               default=None, pattern="^(drive|lifepacket)$")):
    """Search the Drive and the service record.

    `live` is always "unsupported": unlike mail there is no API that reads inside a PDF,
    so this can only be as good as the crawl. It is stated rather than omitted so a
    thinner-than-expected answer is not mistaken for a missing fallback.
    """
    def _query():
        conn = _conn()
        try:
            return document_search.search_archive(conn, q, account=account, limit=limit,
                                                  source=source)
        finally:
            conn.close()

    try:
        hits, error = await run_in_threadpool(_query)
    except sqlite3.Error as exc:
        return JSONResponse(
            {"error": "archive_unavailable", "detail": f"{type(exc).__name__}: {exc}",
             "hint": "the index could not be read; this does NOT mean nothing matches"},
            status_code=503)
    return {"source": "archive", "live": "unsupported", "query": q, "count": len(hits),
            "results": hits, "error": error}


@app.get("/documents/read", dependencies=[Depends(require_token)])
async def documents_read(id: str = Query(..., min_length=1)):
    """One document's full text, with its page count."""
    def _query():
        conn = _conn()
        try:
            return document_search.read_document(conn, id)
        finally:
            conn.close()

    try:
        d = await run_in_threadpool(_query)
    except sqlite3.Error as exc:
        return JSONResponse({"error": "archive_unavailable",
                             "detail": f"{type(exc).__name__}: {exc}"}, status_code=503)
    if not d:
        raise HTTPException(status_code=404, detail="no such document")
    if d.get("excluded"):
        raise HTTPException(status_code=403, detail="excluded by an owner rule")
    return d


@app.get("/documents/stats", dependencies=[Depends(require_token)])
async def documents_stats():
    """Coverage per source. The unread count is the honest one — "7,385 files" is not."""
    def _query():
        conn = _conn()
        try:
            return document_search.stats(conn)
        finally:
            conn.close()
    return {"sources": await run_in_threadpool(_query)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "7870")))
