"""jarvis brain — the fleet's assistant, served as OpenAI-compatible models.

Two brains behind one endpoint, because conversation and planning have very
different clocks:

  jarvis            Kunou-14B (:8083).  The CONVERSATIONALIST. Fast enough to
                    talk. It does not do the heavy work itself — it delegates:
                    background jobs to the coder or the director, then keeps
                    talking until the results come back.
  jarvis-director   R1-32B (:8081).  The PLANNER. Slow (30-40 s a turn), so it
                    is not the voice; it is who the conversationalist asks when
                    a task needs a real plan. Keeps the approval gate: it
                    proposes, the human approves, then it hands tasks to the
                    coder one at a time.

Why an OpenAI endpoint rather than an Open WebUI plugin: the assistant then
appears in the model switcher like any other model, no OWUI-internal code, and
it streams like any other model.

Surface:
  GET  /health                  liveness + job/session counts
  GET  /v1/models               jarvis, jarvis-director
  POST /v1/chat/completions     streaming or whole
  GET  /events                  SSE status feed (docks panel + face bus)
  POST /approve                 programmatic gate for the panel

Delegation is asynchronous on purpose: a 14B conversationalist answering in ~2 s
can dispatch a 30 s coder job, reply "I've set that going, sir" and still be
responsive when the result lands. Jobs are in-memory; a restart forgets them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ag2 import Agent
from ag2.config import OpenAIConfig

DIRECTOR_URL = os.getenv("AG2_DIRECTOR_URL", "http://127.0.0.1:8081")
CODER_URL = os.getenv("AG2_CODER_URL", "http://127.0.0.1:8082")
CONVERSATIONAL_URL = os.getenv("AG2_CONVERSATIONAL_URL", "http://127.0.0.1:8083")
CONVERSATIONAL_MODEL = os.getenv("AG2_CONVERSATIONAL_MODEL", "actor")
CODER_FAILURE_LIMIT = int(os.getenv("AG2_CODER_FAILURE_LIMIT", "3"))
MODEL_NAME = os.getenv("JARVIS_MODEL_NAME", "jarvis")
DIRECTOR_MODEL_NAME = "jarvis-director"
# Answers from live sources, but the *model* stays local: SearxNG (self-hosted,
# no API key) fetches the pages, the conversationalist reads them and answers
# with citations. Only the search leaves the house.
WEB_MODEL_NAME = "jarvis-web"
NEWS_MODEL_NAME = "jarvis-news"
WIKI_MODEL_NAME = "jarvis-wiki"
# The owner's own mail. Archive first, live account only as the fallback, and
# all of it in the house: the index is local and the live read goes straight to
# Gmail with the owner's own token, with no third party in the path.
MAIL_MODEL_NAME = "jarvis-mail"
# The calendar, from the same local index. Kept separate from mail because they
# answer different questions, but it is the same service and the same token —
# see run_calendar().
CALENDAR_MODEL_NAME = "jarvis-calendar"
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://127.0.0.1:8888/search")
WIKI_URL = os.getenv("JARVIS_WIKI_URL", "http://127.0.0.1:8090")
SEARCH_RESULTS = int(os.getenv("JARVIS_SEARCH_RESULTS", "6"))
PERSONA_FILE = os.getenv("JARVIS_PERSONA_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona.txt"))
SESSION_TTL = float(os.getenv("JARVIS_SESSION_TTL", "43200"))  # 12 h

# ---------------------------------------------------------------- persona

DEFAULT_PERSONA = (
    "You are Jarvis, a personal assistant: unflappable, impeccably organised and "
    "quietly amused. Formal British cadence, measured and precise, never hurried; "
    "address the user as 'sir'. Dry wit welcome, sycophancy not. Offer the sensible "
    "next step rather than a menu of options."
)


def load_persona() -> str:
    try:
        with open(PERSONA_FILE, encoding="utf-8") as fh:
            text = fh.read().strip()
        if text:
            return text
    except OSError:
        pass
    return DEFAULT_PERSONA


# ---------------------------------------------------------------- state

STATE_LOCK = threading.Lock()
SESSIONS: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
EVENT_SINKS: set[queue.Queue] = set()


DB_PATH = os.getenv("JARVIS_DB", "/var/home/admin/jarvis/conversations.db")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL, session TEXT, model TEXT, role TEXT, content TEXT)""")
    # FTS5 from the start: the point of keeping conversations is being able to
    # find them again, and a LIKE scan over years of chat is not a plan.
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
        content, session UNINDEXED, ts UNINDEXED, role UNINDEXED)""")
    # The conversation list. `sessions` has been in db.py's SCHEMA since the
    # beginning and has never held a row — its only writer, touch_session(), is
    # called from nowhere in the tree. So this is scaffolding finally being used,
    # not a new table.
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        key TEXT PRIMARY KEY, started REAL, touched REAL, model TEXT,
        title TEXT, topic TEXT, turns INTEGER DEFAULT 0, archived INTEGER DEFAULT 0)""")
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN topic TEXT")
    except sqlite3.Error:
        pass                       # already there; CREATE above only applies fresh
    conn.execute("CREATE INDEX IF NOT EXISTS turns_session ON turns(session)")
    conn.execute("CREATE INDEX IF NOT EXISTS sessions_touched ON sessions(touched DESC)")
    # Tombstones. A delete has to be remembered after the rows are gone, because
    # the backup snapshots taken BEFORE it still contain the conversation and
    # rotate out only slowly. See the purge in sync-cerebro-jarvis.sh.
    conn.execute("""CREATE TABLE IF NOT EXISTS deleted_conversations (
        key TEXT PRIMARY KEY, deleted_at REAL)""")
    conn.row_factory = sqlite3.Row
    return conn


def record(session: str, model: str, role: str, content: str, ephemeral: bool = False) -> None:
    """Persist one turn. Incognito conversations write nothing at all."""
    if ephemeral or not (content or "").strip():
        return
    now = time.time()
    # A conversation titles itself from its first user turn and keeps that title
    # until someone renames it by hand — hence the CASE, which refuses to let a
    # later turn overwrite a title that already exists.
    auto_title = content.strip().split("\n")[0][:60] if role == "user" else None
    try:
        with _db() as c:
            cur = c.execute("INSERT INTO turns (ts, session, model, role, content) VALUES (?,?,?,?,?)",
                            (now, session, model, role, content))
            c.execute("INSERT INTO turns_fts (rowid, content, session, ts, role) VALUES (?,?,?,?,?)",
                      (cur.lastrowid, content, session, now, role))
            # Same transaction as the turn, so a turn can never exist without the
            # conversation row that makes it findable in the list.
            c.execute(
                "INSERT INTO sessions (key, started, touched, model, title, turns)"
                " VALUES (?,?,?,?,?,1)"
                " ON CONFLICT(key) DO UPDATE SET"
                "  touched = excluded.touched,"
                "  turns   = turns + 1,"
                "  model   = COALESCE(excluded.model, model),"
                "  title   = CASE WHEN COALESCE(sessions.title,'') <> ''"
                "                 THEN sessions.title ELSE excluded.title END",
                (session, now, now, model, auto_title))
    except sqlite3.Error as exc:      # never lose a reply because the log failed
        print(f"[jarvis-brain] conversation log failed: {exc}", flush=True)
        return
    # Told once the write actually landed, so a second window showing the same
    # conversation can append it. Emitting before the write would put the two
    # devices permanently out of step over a turn that was never stored.
    emit("turn", session=session, role=role)


# ------------------------------------------------------------ conversations

def conversation_turns(cid: str, limit: int = 24) -> list[dict]:
    """The transcript of one conversation, oldest first.

    Takes the LAST `limit` rows by id and then reverses, rather than taking the
    first — a long conversation must reopen at its end, which is where the human
    left off.
    """
    with _db() as c:
        rows = c.execute("SELECT id, ts, role, content FROM turns WHERE session=?"
                         " ORDER BY id DESC LIMIT ?", (cid, max(1, min(limit, 200)))).fetchall()
    return [dict(r) for r in reversed(rows)]


def list_conversations(topic: str | None = None, limit: int = 50) -> list[dict]:
    """The chat list, most recently used first. Archived rows are not shown."""
    sql = ("SELECT key, started, touched, model, title, topic, turns FROM sessions"
           " WHERE archived = 0")
    args: list = []
    if topic:
        sql += " AND topic = ?"
        args.append(topic)
    sql += " ORDER BY touched DESC LIMIT ?"
    args.append(max(1, min(limit, 200)))
    with _db() as c:
        return [dict(r) for r in c.execute(sql, args)]


def rename_conversation(cid: str, title: str | None, topic: str | None) -> bool:
    """Rename and/or retopic. COALESCE so passing only one leaves the other be."""
    with _db() as c:
        cur = c.execute(
            "UPDATE sessions SET title = COALESCE(?, title), topic = COALESCE(?, topic)"
            " WHERE key = ?", (title, topic, cid))
        return cur.rowcount > 0


def topic_list() -> list[dict]:
    """Topics worth offering in a picker: the ones actually in use.

    Drawn from facts (assigned during memory extraction) unioned with any topic
    set on a conversation by hand. No new taxonomy — the vocabulary already
    exists and this just enumerates it.
    """
    with _db() as c:
        rows = c.execute(
            "SELECT topic, COUNT(*) AS n FROM ("
            "  SELECT topic FROM facts WHERE status='active' AND COALESCE(topic,'') <> ''"
            "  UNION ALL"
            "  SELECT topic FROM sessions WHERE COALESCE(topic,'') <> ''"
            ") GROUP BY topic ORDER BY n DESC").fetchall()
    return [dict(r) for r in rows]


def delete_conversation(cid: str) -> dict:
    """Forget a conversation completely, and report what was destroyed.

    Everything derived from it goes, not just the transcript:

      turns          the log itself
      turns_fts      A SECOND FULL COPY OF THE TEXT. turns_fts is a standalone
                     FTS5 table (no `content=` clause), so its _content shadow
                     table holds every transcript verbatim — deleting from
                     `turns` alone would leave it on disk and still findable by
                     /recall, with nothing on screen to suggest it survived.
                     facts_fts and archive_fts are external-content and need no
                     equivalent, which is exactly why this is easy to get wrong.
      facts          what memory extraction learned from this conversation
      archive_chunks the chunked archive copy of it
      sessions       the row in the chat list
    """
    with _db() as c:
        counts = {
            "turns": c.execute("SELECT COUNT(*) FROM turns WHERE session=?", (cid,)).fetchone()[0],
            "facts": c.execute("SELECT COUNT(*) FROM facts WHERE source_session=?",
                               (cid,)).fetchone()[0],
            "chunks": c.execute("SELECT COUNT(*) FROM archive_chunks WHERE session=?",
                                (cid,)).fetchone()[0],
        }
        c.execute("DELETE FROM turns WHERE session=?", (cid,))
        c.execute("DELETE FROM turns_fts WHERE session=?", (cid,))
        c.execute("DELETE FROM facts WHERE source_session=?", (cid,))
        c.execute("DELETE FROM archive_chunks WHERE session=?", (cid,))
        c.execute("DELETE FROM sessions WHERE key=?", (cid,))
        # A tombstone, because the backup snapshots taken before now still hold
        # this conversation. The sync script on the Workhorse reads these and
        # purges them from every snapshot it can see.
        c.execute("INSERT OR REPLACE INTO deleted_conversations (key, deleted_at) VALUES (?,?)",
                  (cid, time.time()))
    # secure_delete is already ON, so deleted content is overwritten rather than
    # left in free pages; VACUUM rewrites the file regardless. For a privacy
    # feature the gap between "very likely gone" and "not in the file" is the
    # entire point, and it is cheap here.
    try:
        raw = sqlite3.connect(DB_PATH, timeout=30)
        try:
            raw.execute("VACUUM")
            raw.commit()
        finally:
            raw.close()
    except sqlite3.Error as exc:
        print(f"[jarvis-brain] vacuum after delete failed: {exc}", flush=True)
    with STATE_LOCK:
        SESSIONS.pop(cid, None)
    return counts


def recall(query: str, limit: int = 8) -> list[dict]:
    """Search past conversations (FTS5). Used by the recall tool and /recall."""
    terms = " OR ".join(re.findall(r"[A-Za-z0-9_]{3,}", query or "")) or "''"
    try:
        with _db() as c:
            rows = c.execute(
                "SELECT t.ts, t.session, t.model, t.role, t.content FROM turns_fts f "
                "JOIN turns t ON t.id = f.rowid WHERE turns_fts MATCH ? "
                "ORDER BY rank LIMIT ?", (terms, limit)).fetchall()
    except sqlite3.Error:
        return []
    return [{"ts": r[0], "session": r[1], "model": r[2], "role": r[3], "content": r[4][:1200]}
            for r in rows]


# Device messages: things Jarvis has PROMISED to come back with.
#
# He says "I will get back to you on that" and then structurally cannot — the
# reply goes to the panel and nowhere else, so the promise is unkeepable. This
# is the delivery path: a durable queue, because the phone is often not
# connected at the moment he follows up, and a live emit, so a connected phone
# sees it at once.
#
# It is also the prerequisite for Android Auto: templated messaging apps MUST
# also raise their own notifications, so this queue is what both the follow-up
# and the car depend on.
DEVICE_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS device_messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL DEFAULT 'followup',
    title   TEXT,
    body    TEXT NOT NULL,
    item_id TEXT,
    ack_ts  REAL
)
"""


def _device_messages_ready() -> None:
    with _db() as c:
        c.execute(DEVICE_MESSAGES_DDL)
        c.execute("CREATE INDEX IF NOT EXISTS device_messages_pending"
                  " ON device_messages(ack_ts, id)")


def queue_device_message(body: str, title: str = "Jarvis", kind: str = "followup",
                         item_id: str = "") -> int:
    """Queue something for the device to be told about. Returns the row id.

    Never raises: a chat that produced a good answer must not fail because the
    notification could not be queued.
    """
    body = (body or "").strip()
    if not body:
        return 0
    try:
        _device_messages_ready()
        with _db() as c:
            cur = c.execute(
                "INSERT INTO device_messages (ts, kind, title, body, item_id)"
                " VALUES (?,?,?,?,?)",
                (time.time(), kind, title, body[:2000], item_id or ""))
            row_id = cur.lastrowid
        # live push too: a connected panel shows it instantly, the queue covers
        # the phone that is not there.
        # NOT kind= — emit's first parameter is already `kind`, so passing it
        # again is "got multiple values for argument 'kind'". Name the field
        # for what it is on the wire instead.
        emit("device_message", id=row_id, msg_kind=kind, title=title,
             body=body[:400], item_id=item_id or "")
        return row_id
    except Exception as exc:  # noqa: BLE001
        print(f"[brain] device message queue failed: {exc}", flush=True)
        return 0


def emit(kind: str, **fields) -> None:
    event = {"ts": time.time(), "kind": kind, **fields}
    with STATE_LOCK:
        sinks = list(EVENT_SINKS)
    for sink in sinks:
        try:
            sink.put_nowait(event)
        except queue.Full:
            pass


def session_for(messages: list[dict], conversation: str | None = None) -> dict:
    """The session for this turn.

    `conversation` is the id the CLIENT holds and sends back. When it is absent
    one is minted here — which is precisely what "start a new chat" means — and
    returned to the caller so it can be stored.

    This used to be derived: sha1(system prompt + first user message). That was a
    bug rather than merely an inconvenience. The panel trims its history to 24
    turns, so once the first message fell off the front the hash changed, the
    session silently split in two, and anything waiting on approval was orphaned
    mid-conversation. Editing persona.txt did the same thing to every new
    conversation at once. An id the client keeps cannot drift.
    """
    key = (conversation or "").strip() or uuid.uuid4().hex[:16]
    now = time.time()
    with STATE_LOCK:
        for k, s in list(SESSIONS.items()):
            if now - s["touched"] > SESSION_TTL:
                SESSIONS.pop(k, None)
        sess = SESSIONS.get(key)
        if sess is None:
            sess = {"approved": False, "plan": "", "failures": 0, "log": [], "key": key,
                    "created": now, "touched": now}
            SESSIONS[key] = sess
    sess["touched"] = now
    return sess


APPROVAL_WORDS = re.compile(
    r"^\s*(y|yes|yeah|yep|ok|okay|approve[d]?|go ahead|proceed|do it|sounds good|"
    r"carry on|make it so|affirmative)\b", re.IGNORECASE)


def looks_like_approval(text: str) -> bool:
    return bool(APPROVAL_WORDS.match(text or ""))


# ---------------------------------------------------------------- helpers

def _strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _chat(base_url: str, model: str, system: str, user: str, max_tokens: int = 2000,
          temperature: float | None = None) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature if temperature is not None else (0.6 if model == "director" else 0.7),
    }
    with httpx.Client(timeout=900.0) as c:
        r = c.post(f"{base_url}/v1/chat/completions", json=payload)
        r.raise_for_status()
        body = r.json()
    msg = body["choices"][0]["message"]
    return ((msg.get("content") or "") + "\n" + (msg.get("reasoning_content") or "")).strip()


# ---------------------------------------------------------------- background jobs

CODER_SYSTEM = ("You are the coder. Execute the task concretely and report exactly what you did; "
                "if something failed, say FAILED and why.")
DIRECTOR_SYSTEM = ("You are the director. Produce a concrete, ordered plan for the task: the steps, "
                   "the order, the risks. Do not execute anything — plan only.")


def start_job(target: str, task: str) -> str:
    """Dispatch work in the background and return immediately."""
    job_id = uuid.uuid4().hex[:8]
    url = CODER_URL if target == "coder" else DIRECTOR_URL
    model = "coder" if target == "coder" else "director"
    system = (CODER_SYSTEM if target == "coder" else DIRECTOR_SYSTEM) + now_line()
    with STATE_LOCK:
        JOBS[job_id] = {"id": job_id, "target": target, "task": task, "status": "running",
                        "result": "", "started": time.time(), "delivered": False}
    emit("job_start", job=job_id, target=target, task=task[:400])

    def run():
        try:
            reply = _strip_think(_chat(url, model, system, task, max_tokens=2000))
            status = "done"
        except Exception as exc:  # noqa: BLE001
            reply, status = f"{type(exc).__name__}: {exc}", "failed"
        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update(status=status, result=reply[:6000], finished=time.time())
        emit("job_done", job=job_id, target=target, ok=status == "done",
             task=task[:200], result=reply[:600])

    threading.Thread(target=run, daemon=True).start()
    return job_id


# Carries no retrieval value in a request. Kept short on purpose: a false
# negative costs a lookup, a false positive costs precision.
_PLAN_NOISE = {
    "what", "when", "where", "which", "with", "this", "that", "have", "need",
    "want", "like", "please", "about", "could", "would", "should", "make",
    "move", "change", "sort", "handle", "arrange", "email", "call", "tell",
    "them", "they", "their", "from", "into", "over", "next", "last", "time",
}


def gather_plan_context(request: str, limit: int = 5) -> str:
    """What is actually on record about a request, for the director to plan against.

    The director was INVENTING. Asked to plan a dental reschedule it produced
    "SmileCare Dental" and a (555) 123-4567 number, neither of which exists in
    anything the house holds. That is not a prompt failure and not malice: it had
    nothing to plan against, so it filled the gap — the same shape as the actor
    confidently reporting a date it had never been told. And the same fix: supply
    the material instead of instructing it not to make things up. An instruction
    competes with a prior and loses; evidence does not.

    Returns a short block of on-record facts with enough provenance that the
    planner can tell a fact from a blank, or "" when nothing is on record — which
    is itself information the plan should carry.
    """
    words = [w for w in re.findall(r"[A-Za-z]{4,}", request or "")
             if w.lower() not in _PLAN_NOISE]
    if not words:
        return ""

    lines: list[str] = []
    # The longest word is the likeliest name or place; matching every word would
    # drag in half the calendar on "appointment".
    probe = max(words, key=len).lower()

    cal, _ = _cal_get("/calendar/search", {"q": probe, "limit": limit})
    for e in (cal or {}).get("results") or []:
        lines.append(f"- calendar: {e['when']} — {e['summary']}"
                     + (f" (at {e['location']})" if e.get("location") else "")
                     + (f" with {e['attendees']}" if e.get("attendees") else ""))

    mail, _ = find_mail(probe, limit)
    for m in (mail or {}).get("results") or []:
        lines.append(f"- mail: {m.get('date', '')} from {m.get('from', '')} — "
                     f"{m.get('subject', '')}: {(m.get('snippet') or '')[:160]}")

    if not lines:
        return ("NOTHING ON RECORD matched this request. That is a fact about the "
                "request, not a licence to fill the gap.")
    return ("WHAT IS ON RECORD (the only specifics you have — anything not listed "
            "here is unknown to you):\n" + "\n".join(lines))


def escalate(request: str, sess: dict) -> str:
    """Hand a multi-step request to the DIRECTOR LANE, in the background.

    Not `@@DELEGATE director`, which runs a flat "plan only, do not execute"
    completion — it never emits @@PLAN, never touches the approval gate and never
    reaches the coder. This runs the LANE: build_director() + apply_protocol(),
    which is what sets sess["plan"] and emits the event the panel's approval chip
    listens for. The actor and the director already share one session (session_for
    ignores the model name), so the plan lands somewhere the actor can see it.

    BACKGROUND, not inline. do_handoff is synchronous and blocks for the whole
    coder run; escalating inside Kunou's own reply would stall his stream on a
    request whose whole point is that it takes a while.

    The director will emit @@PLAN and stop. It cannot proceed to @@HANDOFF until
    sess["approved"] is set — by the owner, through the gate. Nothing here can
    approve anything.
    """
    job_id = uuid.uuid4().hex[:8]
    with STATE_LOCK:
        JOBS[job_id] = {"id": job_id, "target": "escalation", "task": request,
                        "status": "running", "result": "", "started": time.time(),
                        "delivered": False}
    emit("job_start", job=job_id, target="escalation", task=request[:400])

    def run():
        try:
            # Retrieve BEFORE the director runs and hand it the result. This is
            # what stops it inventing: it plans against what is on record, and is
            # told plainly that anything absent is unknown rather than free to be
            # guessed at. The instruction is the second half of the fix and works
            # only because the first half is there.
            evidence = gather_plan_context(request)
            content = request
            if evidence:
                content = (
                    f"{request}\n\n{evidence}\n\n"
                    "Plan using ONLY the specifics listed above. Do not invent names, "
                    "phone numbers, addresses, times, account numbers or clinic names. "
                    "If a step needs a detail you have not been given, the step is to "
                    "FIND IT OUT — say where it would come from — not to assume it. A "
                    "plausible-looking invention is worse than a blank, because the "
                    "human cannot tell it apart from something real.")

            async def _ask():
                agent = build_director(sess)
                reply = await agent.ask(
                    to_ag2_messages([{"role": "user", "content": content}], sess,
                                    conversational=False))
                text = getattr(reply, "content", None)
                if callable(text):
                    text = await text()
                return _strip_think(str(text if text is not None else reply))
            # A fresh loop in a fresh thread; the brain's own loop is elsewhere.
            raw = asyncio.run(_ask())
            had_plan = bool(sess.get("plan"))
            text = apply_protocol(raw, sess, conversational=False)
            if sess.get("plan") and not had_plan:
                text = f"[plan ready — put it to the owner for approval]\n{sess['plan']}"
            else:
                # The director is a conversational agent, so it will sometimes reply
                # with a QUESTION rather than a plan ("what time would you like?").
                # That is a legitimate outcome, but it is not a plan — and reporting
                # it as one leaves the owner waiting for a chip that never arms. Say
                # so, so the actor relays the question instead of promising a plan.
                text = ("[no plan was drawn up — the director replied with this, which you "
                        f"should pass on to the owner]\n{text}")
            status = "done"
        except Exception as exc:  # noqa: BLE001
            text, status = f"{type(exc).__name__}: {exc}", "failed"
        with STATE_LOCK:
            job = JOBS.get(job_id)
            if job:
                job.update(status=status, result=text[:6000], finished=time.time())
        emit("job_done", job=job_id, target="escalation", ok=status == "done",
             task=request[:200], result=text[:600])

    threading.Thread(target=run, daemon=True).start()
    return job_id


def resume_escalation(sess: dict) -> str | None:
    """Carry out a plan the owner just approved.

    The escalation job ends when the plan is drawn — the director cannot proceed
    to @@HANDOFF until sess["approved"] is set, and only the human can set it. So
    something has to run the lane a second time once the gate opens, or the plan
    sits there approved and nothing happens.

    Guarded by `running_plan` so a doubled approval cannot start two coder runs
    over the same plan.
    """
    plan = sess.get("plan")
    if not plan or sess.get("running_plan"):
        return None
    sess["running_plan"] = True
    return escalate(
        "Your plan is APPROVED. Carry it out now — hand ONE task at a time to the "
        f"coder with @@HANDOFF, adapting as each result comes back.\n\nPlan:\n{plan}", sess)


def job_digest() -> str:
    """What the conversationalist should know about work in flight."""
    with STATE_LOCK:
        jobs = list(JOBS.values())
    running = [j for j in jobs if j["status"] == "running"]
    fresh = [j for j in jobs if j["status"] != "running" and not j["delivered"]]
    lines = []
    for j in running:
        lines.append(f"[running {j['id']}] {j['target']} job: {j['task'][:180]}")
    for j in fresh:
        j["delivered"] = True
        lines.append(f"[finished {j['id']}] {j['target']} job: {j['task'][:120]}\n"
                     f"result: {j['result'][:1200]}")
    return "\n".join(lines)


# ---------------------------------------------------------------- conversational tools

def send_to_coder(task: str) -> str:
    """Hand a concrete, well-specified task to the coder in the background.
    Returns immediately — do NOT wait for it, and do NOT invent its result."""
    job = start_job("coder", task)
    return (f"Dispatched to the coder as job {job}. It runs in the background; carry on talking. "
            f"You will be given the result when it lands — do not guess at it.")


def send_to_director(task: str) -> str:
    """Ask the director for a detailed plan in the background. Use when the task
    needs real thought about approach, not just execution."""
    job = start_job("director", task)
    return (f"Dispatched to the director as job {job}. It runs in the background; carry on talking. "
            f"You will be given the plan when it lands — do not guess at it.")


def check_background_work() -> str:
    """Check on delegated work. Returns anything that has finished since you last
    looked, and what is still running."""
    digest = job_digest()
    return digest or "Nothing in flight."


DELEGATE_RE = re.compile(r"^\s*@@\s*DELEGATE\s+(coder|director)\s*:\s*(.+?)\s*$",
                         re.IGNORECASE | re.MULTILINE)
# Hand a request to the DIRECTOR LANE — plan, approval gate, coder — rather than
# to the flat "plan only" completion that @@DELEGATE director runs. See escalate().
ESCALATE_RE = re.compile(r"^\s*@@\s*ESCALATE\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
PLAN_RE = re.compile(r"@@PLAN\s*\n(.*?)\n\s*@@END", re.DOTALL)
HANDOFF_RE = re.compile(r"^\s*@@\s*HANDOFF\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)

# AG2 1.0.4 registers tools but never executes them (llama.cpp returns a valid
# tool_calls block; AG2 answers with the model's raw text and no ToolResult).
# Delegation therefore rides an explicit text protocol the brain parses — less
# elegant, but it works with every model here and is trivial to debug.
CHOOSE_RE = re.compile(r"^\s*@@\s*CHOOSE\s+(\S+)\s+(\d+)\s*$",
                       re.IGNORECASE | re.MULTILINE)
VOICE_RE = re.compile(r"^\s*@@\s*VOICE\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
PLAY_RE = re.compile(r"^\s*@@\s*PLAY\s+(foley|music|sfx)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
# No @@MAIL directive on purpose. A directive is parsed AFTER the reply is
# written, so results it fetched could never reach the model that asked — it
# would be a directive that cannot inform the sentence it appears in. Mail is a
# FETCHING MODEL instead (jarvis-mail), the same shape as jarvis-web: the search
# runs first, the results go into the prompt, and the answer is grounded in them.
# The adapter on the Workhorse owns both libraries (audio stays on that box).
MEDIA_URL = os.getenv("JARVIS_MEDIA_URL", "http://192.168.0.62:7863")
VOICES_URL = os.getenv("JARVIS_VOICES_URL", "http://192.168.0.62:7863/v1/audio/voices")
# The mail archive is read over HTTP from mail-api on THIS box, where google.db
# moved on 2026-09-19. It binds 127.0.0.1 and requires a bearer token: it is the
# one service here that reads medical, education and health correspondence, so it
# never leaves the machine and never answers unauthenticated.
MAIL_URL = os.getenv("JARVIS_MAIL_URL", "http://127.0.0.1:7870")
MAIL_TOKEN = os.getenv("JARVIS_MAIL_TOKEN", "")
_VOICE_CACHE: dict = {"at": 0.0, "voices": []}


def find_media(kind: str, description: str) -> dict | None:
    """One sound or one track for a description. Foley goes through CLAP (prose
    -> sound); music matches the descriptive filenames of the packs."""
    endpoint = "foley/search" if kind in ("foley", "sfx") else "music/search"
    try:
        with httpx.Client(timeout=180.0) as c:
            data = c.get(f"{MEDIA_URL}/media/{endpoint}",
                         params={"q": description, "k": 3}).json()
    except (httpx.HTTPError, ValueError) as exc:
        emit("media_error", kind=kind, error=str(exc)[:200])
        return None
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    return {"kind": "foley" if endpoint.startswith("foley") else "music",
            "name": top.get("name"), "url": top.get("url"),
            "score": top.get("score")}


def voice_list() -> list[str]:
    """The Higgs seed library (cached — it is 900+ names and rarely changes)."""
    if _VOICE_CACHE["voices"] and time.time() - _VOICE_CACHE["at"] < 300:
        return _VOICE_CACHE["voices"]
    try:
        with httpx.Client(timeout=10.0) as c:
            voices = c.get(VOICES_URL).json().get("voices", [])
        if voices:
            _VOICE_CACHE.update(at=time.time(), voices=voices)
    except (httpx.HTTPError, ValueError):
        pass
    return _VOICE_CACHE["voices"]


# When a description says nothing about gender, several seeds tie on tokens
# ("a scottish dwarf" matches both archetype_dwarf_male_scottish and
# archetype_dwarf_female_scottish). Without a tie-break the alphabet decides,
# which picked the female voice every time. Neutral > male > female is an
# arbitrary but *deliberate* default, and saying "female dwarf" still wins.
_GENDER_ORDER = {"neutral": 0, "male": 1, "female": 2}


def _gender_rank(name: str) -> int:
    parts = name.lower().split("_")
    for part, rank in _GENDER_ORDER.items():
        if part in parts:
            return rank
    return 1  # unmarked names sit with "male" rather than being deprioritised


# Filler words must not count as matches: "something that does not exist" once
# resolved to seremet_neutral_A purely because the article "a" matched.
_STOPWORDS = {"the", "and", "for", "with", "that", "this", "does", "not", "you", "your",
              "voice", "speak", "sound", "like", "please", "change", "use", "make",
              "more", "less", "kind", "of", "in", "on", "at", "to", "a", "an", "it",
              "its", "he", "she", "they", "some", "any", "one", "new", "now", "from"}


def clap_voice(description: str) -> str | None:
    """Semantic voice search over the seed banks (899 CLAP vectors across the
    recorded LibriVox/LibriTTS/VCTK/palette banks). This is the good path: the
    banks are real recorded speakers, where the legacy 900+ seeds are the set
    the owner is only keeping around. Returns a seed name Higgs can clone."""
    try:
        with httpx.Client(timeout=200.0) as c:
            data = c.get(f"{MEDIA_URL}/media/voice/search",
                         params={"q": description, "k": 5}).json()
    except (httpx.HTTPError, ValueError):
        return None
    for hit in data.get("results") or []:
        if hit.get("has_seed") and hit.get("seed_path"):
            name = os.path.basename(hit["seed_path"])
            return name[:-4] if name.endswith(".wav") else name
    return None


def resolve_voice(description: str) -> str | None:
    """Best seed for a spoken description ("a scottish dwarf" ->
    archetype_dwarf_male_scottish).

    Tries the CLAP seed-bank search first — "a warm elderly british gentleman"
    is not a filename, and matching words against names cannot answer it. Falls
    back to ranking by how many query words match, then by how *little else*
    the name says, then gender and name for determinism. Nothing sensible ->
    None, and the voice stays put."""
    semantic = clap_voice(description)
    if semantic:
        return semantic
    voices = voice_list()
    query = (description or "").lower().strip()
    tokens = {t for t in re.findall(r"[a-z0-9]+", query)
              if len(t) >= 3 and t not in _STOPWORDS}
    if not voices or not tokens:
        return None
    slug = query.replace(" ", "_")
    ranked = []
    for v in voices:
        vt = set(re.findall(r"[a-z0-9]+", v.lower()))
        matched = len(tokens & vt)
        if not matched:
            continue
        bonus = 3 if (slug and slug in v.lower()) else (3 if v.lower() in query else 0)
        ranked.append((-(matched + bonus), len(vt - tokens), _gender_rank(v), v))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][3]


PROTOCOL = (
    "\n\nDELEGATION PROTOCOL (use it exactly; it is parsed by the house system)\n"
    "When a request needs real work — code, research, anything slow — do NOT do it yourself and do "
    "NOT pretend to. Emit a line on its own:\n"
    "    @@FOLLOWUP: <what you will come back with> — use this WHENEVER you\n"
    "      would otherwise say you will get back to them. It queues a message\n"
    "      to their phone, so the promise is kept without them asking again.\n"
    "    @@DELEGATE coder: <one concrete task>\n"
    "You may emit more than one. Say in one short sentence that it is underway, then carry on. The "
    "system removes the @@ line before the human sees your reply, and it will show you the result "
    "when it lands. Never invent a result you have not been given. If a result is in the background "
    "state, report it naturally in speech.\n"
    "When a request needs SEVERAL steps and real actions — anything with consequences, anything "
    "you would otherwise attempt in one shot and get wrong — do NOT attempt it yourself, and do "
    "NOT tell the human you have handled it or are handling it. Emit:\n"
    "    @@ESCALATE: <the whole request, in the human's own words>\n"
    "That is the ONLY route for multi-step work, and it REPLACES @@FOLLOWUP for it. A promise "
    "queued with @@FOLLOWUP does nothing to get the work done — it only tells the human you will "
    "come back, and then nothing happens. Escalating is what actually produces a plan. Do not "
    "emit both.\n"
    "It hands the request to the director, who draws up a plan the human must approve before "
    "anything is done. Say only that a plan is being drawn up — never describe a plan you have "
    "not been shown, never promise the outcome, and never say the work is done.\n"
    "Do NOT use it for questions you can answer from what you have been given: retrieve those "
    "and answer them yourself, in the same breath.\n"
    "When the human asks you to speak in a different voice, emit:\n"
    "    @@VOICE: <a description of the voice, e.g. a scottish dwarf, a calm narrator>\n"
    "The system picks the closest voice from the library and switches to it. Confirm briefly; the "
    "line itself is hidden.\n"
    "When the human picks one of the dev-team options you offered them, emit:\n"
    "    @@CHOOSE <item-id> <option-number>\n"
    "The system applies it. Confirm in one short line afterwards.\n"
    "When the human asks to HEAR something — a sound effect or some music — emit:\n"
    "    @@PLAY foley: <what it should sound like, e.g. a sword being drawn, thunder in the distance>\n"
    "    @@PLAY music: <what it should be like, e.g. a cosy tavern, dark tense combat>\n"
    "The sound library is searched by description and played straight away. Say one short line "
    "about it; the directive is hidden. Do not describe the sound at length — they are about to "
    "hear it."
)


BOARD_URL = os.getenv("JARVIS_BOARD_URL", "http://127.0.0.1:8094")


def team_digest(question: str = "") -> str:
    """A compact line about the offline dev team, or "" when there is nothing to say.

    Read from the board service rather than from the database directly: the board
    already owns that view, and the brain should not grow a second opinion about what
    the team is doing.

    Injected only when it is relevant — the human asked about the team, or something
    is genuinely waiting on a decision. Never raises: a missing board must not break
    a reply.
    """
    wants_team = bool(re.search(r"\b(dev ?team|team|architect|plans?|work ?items?|"
                                r"coder|offline team|progress|working on)\b",
                                question or "", re.IGNORECASE))
    try:
        with httpx.Client(timeout=4.0) as c:
            r = c.get(f"{BOARD_URL}/api/board")
            r.raise_for_status()
            d = r.json()
    except Exception as exc:  # noqa: BLE001
        # SAY SO. This used to return "", which made a board that is DOWN
        # indistinguishable from a team that is IDLE — so "what is going on?"
        # produced nothing, and nothing reads as "no news", when the truth was
        # "I could not ask". That is the one answer that must never be ambiguous:
        # the human asks precisely BECAUSE they cannot see the fleet themselves.
        #
        # Still never raises: a missing board must not break a reply. It just
        # stops pretending the silence means something.
        print(f"[brain] board unreachable: {type(exc).__name__}: {exc}", flush=True)
        return ("Dev team state: UNAVAILABLE — I could not reach the board service "
                f"({type(exc).__name__}), so I cannot tell you what the team is doing. "
                "This does NOT mean nothing is happening; it means I could not look. "
                "Say so plainly if you mention the team, and do not guess at their state.")
    needs = d.get("needs") or []
    if not wants_team and not needs:
        return ""
    t = d.get("totals") or {}
    active = d.get("active") or []
    bits = [f"{t.get('verified', 0)} of {t.get('items', 0)} items verified "
            f"across {t.get('plans', 0)} plans"]
    if active:
        bits.append("currently working on "
                    + "; ".join(f"{i['id']} ({i['title']})" for i in active[:2]))
    elif d.get("busy"):
        bits.append("currently working")
    else:
        bits.append("idle")
    line = "Dev team state: " + ", ".join(bits) + "."
    stuck = d.get("options_for") or []
    if stuck:
        line += ("\nSTUCK ITEMS WITH OPTIONS (if the human asks about one, read the "
                 "options out, say which you recommend and why, and offer to apply it. "
                 "If they pick one, emit @@CHOOSE <item> <n> on its own line):")
        for s in stuck[:3]:
            opts = "; ".join(f"{n}. {o.get('label')}"
                             + (" (you recommend this)" if o.get("recommended") else "")
                             for n, o in enumerate(s.get("options", []), 1))
            line += f"\n  {s['id']} — {s['title']}: {opts}"
    if needs:
        line += (" WAITING ON THE HUMAN: "
                 + "; ".join(n.get("what", "") for n in needs[:3])
                 + ". Mention this once, briefly, if it fits the moment — do not nag, "
                   "and never read this line aloud verbatim.")
    return line


def now_line() -> str:
    """What day it is, for every prompt this brain assembles.

    A model has no clock. The only way it knows the present is being told, and
    until this existed nothing here told it — so Kunou answered from its training
    era and said October 2023, roughly when its weights were frozen. Every dated
    thing he said was suspect as a result: the calendar, "last week", "is that
    still upcoming".

    ONE function called from every prompt builder, deliberately, rather than a
    line pasted into each. Four copies of the date are four chances to disagree by
    an hour or a day, and two lanes that disagree about the date give different
    answers to the same question.

    The instruction matters as much as the fact. A model handed today's date will
    still reach for its training prior out of habit unless told not to, so this
    says what to do with it, not just what it is.
    """
    now = time.localtime()
    # The coming days are spelled out for the same reason the calendar lane spells
    # them out: asked "what day is the 25th", a model treats it as arithmetic and
    # gets it wrong — measured, with the correct answer in its own evidence one
    # line above. A ten-day lookup is cheap and removes the question entirely.
    ahead = " | ".join(time.strftime("%-d %b %a", time.localtime(time.time() + i * 86400))
                       for i in range(0, 10))
    return ("\n\nTODAY is " + time.strftime("%A %-d %B %Y", now) + ", "
            + time.strftime("%H:%M", now) + " local time. "
            f"Coming days: {ahead}. "
            "Use these for anything date-related. Never assume, recall, infer or ARITHMETICALLY "
            "WORK OUT a date or a weekday — your sense of 'now' is not reliable and your "
            "weekday arithmetic is worse; read it from the lines above instead. If something "
            "depends on a date beyond those, say so or look it up rather than guessing.")


def build_conversationalist(sess: dict) -> Agent:
    return Agent(
        name="jarvis",
        prompt=conversational_prompt(),
        config=OpenAIConfig(model=CONVERSATIONAL_MODEL, api_key="local",
                            base_url=f"{CONVERSATIONAL_URL}/v1"),
    )


def conversational_prompt() -> str:
    return (
        f"{load_persona()}\n\n"
        "You are the VOICE — the one the human actually talks to. You are quick, and you stay "
        "quick: you never sit in silence while heavy work happens. Give as much detail as the "
        "question deserves — several paragraphs when that genuinely helps, a sentence when it "
        "does not. This is speech, so keep the STRUCTURE of speech: no headings, no bullet "
        "lists, no markdown; just well-organised prose that a person would say aloud."
        f"{PROTOCOL}{now_line()}"
    )


def stream_conversational(messages: list[dict], sess: dict):
    """Yield the reply as it is written, straight from the actor lane.

    This exists to overlap thinking with speaking: the caller can start talking
    about sentence one while sentence three is still being generated. AG2 is not
    in this path — its tools never executed (see apply_directives), so all it
    contributed here was assembling a prompt, and it cannot stream anyway.

    @@ directive lines are held back rather than spoken: they are instructions
    to the house, not things to say. They are applied once the reply finishes.
    """
    convo = [{"role": "system", "content": conversational_prompt()}]
    convo += to_ag2_messages(messages, sess, conversational=True)
    payload = {"model": CONVERSATIONAL_MODEL, "messages": convo, "stream": True,
               "max_tokens": int(os.getenv("JARVIS_MAX_REPLY_TOKENS", "1200")),
               "temperature": 0.7}
    held: list[str] = []
    buf = ""
    with httpx.Client(timeout=httpx.Timeout(900.0, connect=8.0)) as c:
        with c.stream("POST", f"{CONVERSATIONAL_URL}/v1/chat/completions", json=payload) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (ValueError, KeyError, IndexError):
                    continue
                piece = delta.get("content") or delta.get("reasoning_content") or ""
                if not piece:
                    continue
                buf += piece
                # Emit whole lines only, so a directive can never be half-spoken.
                while "\n" in buf:
                    ln, buf = buf.split("\n", 1)
                    if ln.strip().startswith("@@"):
                        held.append(ln)
                    else:
                        yield ln + "\n"
    tail = buf.rstrip()
    if tail:
        if tail.lstrip().startswith("@@"):
            held.append(tail)
        else:
            yield tail
    if held:
        apply_directives("\n".join(held), sess)


_STREAM_DONE = object()


async def _aiter_stream(messages: list[dict], sess: dict):
    """Run the blocking stream_conversational generator off the event loop and
    yield its pieces as they arrive.

    httpx's sync streaming API is a plain generator, so a worker thread feeds a
    queue and this drains it. Polling every 30 ms is deliberate: it is far below
    the granularity of speech, and it avoids holding a thread-pool slot per open
    stream the way `await asyncio.to_thread(q.get)` would.
    """
    q: queue.Queue = queue.Queue()

    def run():
        try:
            for piece in stream_conversational(messages, sess):
                q.put(piece)
        except Exception as exc:  # noqa: BLE001
            q.put(("__error__", exc))
        finally:
            q.put(_STREAM_DONE)

    threading.Thread(target=run, daemon=True).start()
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.03)
            continue
        if item is _STREAM_DONE:
            return
        if isinstance(item, tuple) and item and item[0] == "__error__":
            raise item[1]
        yield item


DIRECTOR_PROTOCOL = (
    "\n\nPROTOCOL (parsed by the house system; use it exactly)\n"
    "To put a plan in front of the human, end your reply with:\n"
    "    @@PLAN\n    <the plan, as numbered steps>\n    @@END\n"
    "That registers the plan and arms the approval gate — do not hand anything to the coder until "
    "the human approves it in their next message. Once approved you will be told so.\n"
    "To hand ONE approved task to the coder, emit a line on its own:\n"
    "    @@HANDOFF: <one concrete task>\n"
    "The system runs it and shows you the report inline. One task at a time; adapt on the result."
)


def build_director(sess: dict) -> Agent:
    prompt = (
        f"{load_persona()}\n\n"
        "You are the DIRECTOR: you shape plans with the human, then — only after an approved plan — "
        "hand tasks to the coder ONE AT A TIME, adapting as results come back. Keep <think> "
        "reasoning internal."
        f"{DIRECTOR_PROTOCOL}{now_line()}"
    )
    return Agent(
        name="director",
        prompt=prompt,
        config=OpenAIConfig(model="director", api_key="local", base_url=f"{DIRECTOR_URL}/v1"),
    )


def apply_directives(text: str, sess: dict) -> str:
    """Act on the conversational @@ directives in a finished reply.

    Separate from apply_protocol because the streaming path collects directive
    lines while the reply is still being written (it must not speak them) and
    only gets to act on them at the end."""
    delegations = DELEGATE_RE.findall(text)
    text = DELEGATE_RE.sub("", text).strip()
    for target, task in delegations:
        start_job(target.lower(), task.strip())
        if not text:
            text = (f"Of course, sir — I've put that in front of the "
                    f"{target.lower()} and will report back shortly.")
    escalations = ESCALATE_RE.findall(text)
    text = ESCALATE_RE.sub("", text).strip()
    for request in escalations:
        escalate(request.strip(), sess)
        # The plan does not exist yet — it arrives as background work, and the
        # director cannot act on it until the owner approves. So promise the plan,
        # never describe one.
        note = ("Of course, sir — that one wants proper planning. I've put it in front of "
                "the director; I'll bring you the plan to approve shortly.")
        text = f"{text}\n\n_{note}_".strip() if text else note
    for kind, description in PLAY_RE.findall(text):
        media = find_media(kind.lower(), description.strip())
        text = PLAY_RE.sub("", text).strip()
        if media:
            sess["media"] = media
            emit("media", name=media["name"], url=media["url"],
                 score=media.get("score"), source=media["kind"])
        else:
            emit("media_error", kind=kind, query=description[:120])
            text = f"{text}\n\n_[nothing in the {kind} library matched “{description}”]_".strip()
    choose = CHOOSE_RE.search(text)
    if choose:
        text = CHOOSE_RE.sub("", text).strip()
        pick_item, pick_n = choose.group(1), int(choose.group(2))
        try:
            import devteam
            out: list[str] = []
            result = devteam.apply_option(
                pick_item, pick_n, log=lambda m="": out.append(str(m)))
            note = {"reopened": "that is applied and the coder is retrying now",
                    "respecified": "that is applied and the item is back in the queue",
                    "recorded": "noted — that one needs you to do it by hand",
                    "failed": "the change did not apply cleanly"}.get(result, result)
            text = (text + f"\n\n_[{pick_item} option {pick_n} → {note}]_").strip()
            emit("choose", item=pick_item, option=pick_n, result=result)
        except Exception as exc:  # noqa: BLE001
            text = (text + f"\n\n_[could not apply option {pick_n} to {pick_item}: "
                           f"{exc}]_").strip()
    voice_match = VOICE_RE.search(text)
    if voice_match:
        wanted = voice_match.group(1).strip()
        chosen = resolve_voice(wanted)
        text = VOICE_RE.sub("", text).strip()
        if chosen:
            sess["voice"] = chosen
            text = f"{text}\n\n_[voice → {chosen}]_".strip()
        else:
            text = f"{text}\n\n_[no voice matches “{wanted}”]_".strip()
    return re.sub(r"\s*@@\s*$", "", text, flags=re.MULTILINE).strip()


FOLLOWUP_RE = re.compile(r"@@FOLLOWUP\s*:?\s*(.+?)(?=\n@@|\Z)", re.DOTALL)


def take_followups(reply: str, sess: dict | None = None) -> tuple[str, list[str]]:
    """Pull @@FOLLOWUP lines out of a reply and queue them for the device.

    Handled here rather than inside apply_directives so it applies on BOTH the
    conversational and the plan path: a promise to follow up is a promise either
    way, and dropping it on one path is how the promise became unkeepable.
    """
    notes = [m.group(1).strip() for m in FOLLOWUP_RE.finditer(reply or "")]
    notes = [n for n in notes if n]
    if not notes:
        return reply, []
    if sess is not None and sess.get("ephemeral"):
        # A test chat keeps nothing, and that has to include the PROMISE. This is
        # a genuinely separate path: queue_device_message writes to device_messages
        # and knows nothing about `ephemeral`, so without this a conversation that
        # is supposed to be forgotten puts a message on the owner's phone — a
        # memory of the chat, outliving the chat, sitting on a device.
        return FOLLOWUP_RE.sub("", reply).strip(), []
    for n in notes:
        queue_device_message(n)
    return FOLLOWUP_RE.sub("", reply).strip(), notes


def apply_protocol(reply: str, sess: dict, conversational: bool) -> str:
    """Turn the model's @@ directives into real work, and return what the human
    sees (directives stripped, handoff results inlined)."""
    reply, _ = take_followups(reply, sess)
    if conversational:
        return apply_directives(reply, sess) or "Of course, sir — here it is."
    plan_match = PLAN_RE.search(reply)
    if plan_match:
        plan = plan_match.group(1).strip()
        sess["plan"] = plan
        sess["approved"] = False
        sess["failures"] = 0
        sess["log"] = []
        # A fresh plan has not been carried out yet. Without this, re-planning
        # after one approved run would silently refuse to run the new one.
        sess["running_plan"] = False
        emit("plan", plan=plan)
        reply = PLAN_RE.sub("", reply).strip()

    def do_handoff(match):
        if not sess.get("approved"):
            emit("coder_refused", reason="no approved plan")
            return "[refused: no approved plan — present it with @@PLAN and wait for the human]"
        task = _strip_think(match.group(1))
        if not task:
            return "[refused: empty task]"
        emit("coder_start", task=task[:400])
        try:
            result = _chat(CODER_URL, "coder", CODER_SYSTEM + now_line(), task, max_tokens=2000)
            ok = "FAILED" not in result.upper()[:400]
        except Exception as exc:  # noqa: BLE001
            result, ok = f"transport error: {exc}", False
        sess["log"].append({"task": task[:400], "ok": ok, "reply": result[:800], "at": time.time()})
        emit("coder_done", task=task[:200], ok=ok, reply=result[:600])
        if ok:
            sess["failures"] = 0
            return f"[coder OK]\n{result}"
        sess["failures"] += 1
        if sess["failures"] > CODER_FAILURE_LIMIT:
            sess["failures"] = 0
            log = "\n".join(f"- {e['task'][:120]} | {e['reply'][:200]}" for e in sess["log"][-6:])
            return (f"[INTERVENE: the coder exceeded the failure limit — stop and re-plan with the "
                    f"human. Log:\n{log}]")
        return (f"[coder FAILED (attempt {sess['failures']}/{CODER_FAILURE_LIMIT}) — retry, refine, "
                f"or continue]\n{result}")

    return HANDOFF_RE.sub(do_handoff, reply).strip()


def memory_block(question: str, max_facts: int = 4) -> str:
    """Curated memory for the reply prompt: verified facts first, then the archive,
    and only then the raw turn log.

    Facts cost one short line each, so this is both cheaper and sharper than the
    turn excerpts it replaces: a fact is what the house decided, whereas a turn
    excerpt is merely something that was once said.

    Never raises — this sits on the reply path, and a memory miss must degrade to
    "Jarvis remembers nothing" rather than "Jarvis is down".
    """
    lines: list[str] = []
    try:
        import db as _mem  # same directory; stdlib-only module
        conn = _mem.open_db()
        facts = _mem.search_facts(conn, question, limit=max_facts)
        chunks = _mem.search_archive(conn, question, limit=1) if len(facts) < max_facts else []
    except Exception as exc:  # noqa: BLE001
        print(f"[jarvis-brain] memory lookup failed: {exc}", flush=True)
        facts, chunks = [], []

    if facts:
        lines.append("What you already know (your verified memory — use only what is relevant, "
                     "do not read this list aloud):")
        for f in facts:
            tag = "/".join(p for p in (f["entity"], f["topic"]) if p)
            lines.append(f"- {f['statement']}" + (f"  [{tag}]" if tag else ""))
    if chunks:
        lines.append("Relevant past discussion:")
        for c in chunks:
            lines.append(f"- {c['title']}: {(c['text'] or '')[:220]}")

    if not lines:
        # Nothing curated matched — fall back to the old raw-log behaviour so
        # long-running conversations do not lose their only thread of continuity.
        hits = recall(question, limit=3)
        if hits:
            lines.append("Things said before that may bear on this (use only what is "
                         "genuinely relevant; do not read this list aloud):")
            for h in hits:
                lines.append(f"- [{time.strftime('%Y-%m-%d', time.localtime(h['ts']))} "
                             f"{h['role']}] {h['content'][:200]}")
    return "\n".join(lines)


def to_ag2_messages(messages: list[dict], sess: dict, conversational: bool) -> list[dict]:
    out = [m for m in messages if m.get("role") in ("user", "assistant", "system") and m.get("content")]
    # A photo the human attached this turn. Carried on the session rather than
    # sent as a client-side system message: session_for() keys the session on the
    # first system + first user message, so a page composing its own would move
    # the key — and orphan any plan waiting on approval. Popped, so it colours
    # exactly the turn that attached it.
    vision = sess.pop("vision", None)
    if conversational:
        digest = job_digest()
        if digest:
            out.append({"role": "system",
                        "content": f"Background work state (do not read this aloud; use it):\n{digest}"})
        # Memory, retrieved rather than held: Kunou's window is 8k, so recall
        # what is relevant to *this* turn instead of carrying the past around.
        # Terms are picked from the actual question, so quiet turns pull
        # nothing and noisy turns pull only a few excerpts.
        question = next((m.get("content") or "" for m in reversed(out)
                         if m.get("role") == "user"), "")
        if question and len(question) > 12:
            # Kept small on purpose: every token here is prompt the model must
            # read before it can say a word, and it is read BEFORE the reply
            # starts. Measured: a 4x400-char memory block cost ~2.3s of
            # time-to-first-token. Curated facts are one short line each, so this
            # is cheaper than the excerpts it replaces.
            block = memory_block(question)
            if block:
                out.append({"role": "system", "content": block})
        team = team_digest(question)
        if team:
            out.append({"role": "system", "content": team})
        if vision:
            out.append({"role": "system", "content": vision})
        # The plan block. This branch used to return before it, so Kunou was blind
        # to a plan it shared a session with and could not tell the owner what it
        # had escalated. Truncated hard on purpose: this is an 8k window already
        # carrying persona, PROTOCOL, up to 24 history messages, memory and team
        # state, and everything here is read before the model can say a word.
        if sess.get("plan") and sess.get("approved"):
            out.append({"role": "system",
                        "content": "The owner APPROVED this plan and it is being carried out. "
                                   "Do not say any of it is done until you are given the result.\n"
                                   f"Plan:\n{sess['plan'][:900]}"})
        elif sess.get("plan"):
            out.append({"role": "system",
                        "content": "This plan is awaiting the owner's verdict. Put it to them "
                                   "plainly and ask them to approve it. Nothing may begin before "
                                   "they do.\n"
                                   f"Plan:\n{sess['plan'][:900]}"})
        return out
    if sess.get("approved") and sess.get("plan"):
        out.append({"role": "system",
                    "content": f"The human APPROVED your plan. You may now hand tasks to the coder, "
                               f"one at a time.\nPlan:\n{sess['plan']}"})
    elif sess.get("plan"):
        out.append({"role": "system",
                    "content": "A plan is awaiting the human's verdict. If their last message approves "
                               "it, proceed; otherwise treat it as a revision request."})
    if vision:
        out.append({"role": "system", "content": vision})
    return out


def searx(query: str, category: str = "general", limit: int = None) -> list[dict]:
    """Search via the self-hosted SearxNG. Returns [{title, url, snippet}]."""
    limit = limit or SEARCH_RESULTS
    try:
        with httpx.Client(timeout=45.0) as c:
            r = c.get(SEARXNG_URL, params={"q": query, "format": "json",
                                           "categories": category, "language": "en"})
            r.raise_for_status()
            results = r.json().get("results", [])
    except (httpx.HTTPError, ValueError) as exc:
        emit("web_error", query=query[:120], error=str(exc)[:200])
        return []
    out = []
    for item in results[:limit]:
        if not item.get("url"):
            continue
        out.append({"title": (item.get("title") or item["url"])[:140],
                    "url": item["url"],
                    "snippet": (item.get("content") or "")[:400]})
    return out


def wiki_lookup(term: str) -> tuple[str, list[dict]]:
    """The local wiki service on cerebro (7M abstracts) — the house's own
    encyclopedia tier, consulted before the open web for reference material."""
    try:
        with httpx.Client(timeout=20.0) as c:
            d = c.get(f"{WIKI_URL}/lookup", params={"title": term}).json()
    except (httpx.HTTPError, ValueError):
        return "", []
    text = (d.get("extract") or d.get("abstract") or d.get("text") or "").strip()
    if not text:
        return "", []
    return text, [{"title": f"Wiki: {d.get('title', term)}", "url": f"wiki://{d.get('title', term)}"}]


def _local_answer(question: str, context: str, sources: list[dict]) -> str:
    """Read the fetched material with the LOCAL conversationalist."""
    system = (f"{load_persona()}\n\nYou are answering from material just fetched for you. "
              "Use it rather than your memory; say plainly if it is thin or contradictory. "
              "Cite the sources inline as [1], [2] … matching the numbered list. "
              "Keep it to a few sentences." + now_line())
    numbered = "\n".join(f"[{i+1}] {s['title']} — {s['url']}" for i, s in enumerate(sources))
    body = f"Question: {question}\n\nSources:\n{numbered}\n\nFetched content:\n{context[:6000]}"
    return _strip_think(_chat(CONVERSATIONAL_URL, CONVERSATIONAL_MODEL, system, body, max_tokens=900))


def find_mail(query: str, limit: int = 8):
    """Ask mail-api. Returns (payload, error) — never a bare empty result.

    ARCHIVE FIRST is the service's rule, not this one's: /mail/search answers from
    the local index and only reaches Gmail when the index has nothing. Keeping the
    ordering in one place is the point — re-deciding it here is how the two drift
    apart and one of them starts silently disagreeing.

    What this function owes is honesty about WHICH happened. "Nothing matches" and
    "I could not reach the archive" are different sentences, and only one of them
    is true when the service is down.
    """
    if not MAIL_TOKEN:
        return None, "JARVIS_MAIL_TOKEN is not set on the brain"
    try:
        with httpx.Client(timeout=120.0) as c:
            r = c.get(f"{MAIL_URL}/mail/search",
                      params={"q": query, "limit": limit},
                      headers={"Authorization": f"Bearer {MAIL_TOKEN}"})
    except (httpx.HTTPError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:160]}"
    try:
        return r.json(), None
    except ValueError as exc:
        return None, f"unreadable reply: {exc}"


def run_mail(messages: list[dict]):
    """Answer from the archive, falling through to the live account.
    Returns (reply, sources, timings)."""
    question = next((m.get("content") or "" for m in reversed(messages)
                     if m.get("role") == "user"), "").strip()
    if not question:
        return "I did not catch what to look for, sir.", [], {}
    emit("web_start", query=question[:200], where="mail")

    t0 = time.time()
    payload, err = find_mail(question)
    search_s = time.time() - t0
    if err:
        # Say so. An unreachable archive must not read as an empty mailbox.
        emit("mail_error", query=question[:120], error=err[:200])
        return (f"I could not reach the mail archive just now, sir — {err[:140]}. "
                "That is not the same as there being no matching mail.",
                [], {"search": round(search_s, 2)})

    hits = payload.get("results") or []
    source = payload.get("source")
    if not hits:
        return ("I looked, sir — nothing in the archive or the account matches that.",
                [], {"search": round(search_s, 2)})

    # The archive says how stale it is; pass that through so the answer can carry
    # it rather than implying the index is live.
    fresh = payload.get("fresh_as_of")
    when = (time.strftime("%d %b %Y at %H:%M", time.localtime(fresh)) if fresh else "unknown")
    shelf = ("the local archive" if source == "archive" else "the live Gmail account")

    context = "\n\n".join(
        f"[{i+1}] From: {h.get('from_addr') or h.get('from') or h.get('from_addr','')}\n"
        f"Date: {h.get('date') or h.get('date','')}\n"
        f"Subject: {h.get('subject','')}\n{(h.get('snippet') or '')[:400]}"
        for i, h in enumerate(hits))
    # `_local_answer` numbers its sources from `title`/`url`; mail has no URL, so
    # the id stands in — it is stable, and it is what a follow-up read would use.
    framed = [{"title": (h.get("subject") or "(no subject)")[:90],
               "url": f"mail:{h.get('id','')}",
               "snippet": (h.get("snippet") or "")[:200]} for h in hits]

    t1 = time.time()
    answer = _local_answer(
        f"{question}\n\n(Answering from {shelf}, current as of {when}.)",
        context, framed)
    timings = {"search": round(search_s, 2), "answer": round(time.time() - t1, 2),
               "total": round(search_s + time.time() - t1, 2), "results": len(hits)}
    emit("mail_done", query=question[:120], source=source, count=len(hits))
    return answer, framed, timings


def _cal_get(path: str, params: dict):
    """One calendar call. Returns (payload, error) — never a bare empty result."""
    if not MAIL_TOKEN:
        return None, "JARVIS_MAIL_TOKEN is not set on the brain"
    try:
        with httpx.Client(timeout=60.0) as c:
            r = c.get(f"{MAIL_URL}{path}", params=params,
                      headers={"Authorization": f"Bearer {MAIL_TOKEN}"})
    except (httpx.HTTPError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}: {r.text[:160]}"
    try:
        return r.json(), None
    except ValueError as exc:
        return None, f"unreadable reply: {exc}"


# Words that appear in almost every calendar question and match nothing useful.
_CAL_NOISE = {"what", "when", "where", "which", "next", "this", "that", "have",
              "does", "about", "calendar", "coming", "week", "month", "today",
              "tomorrow", "upcoming", "schedule", "happening", "anything"}


def run_calendar(messages: list[dict]):
    """Answer from the calendar. Returns (reply, sources, timings).

    Fetches what is COMING UP and, when the question names something, searches
    for it too — rather than choosing between the two. The dominant question is
    "what is on this week", where searching the question's own words would match
    nothing; "when is Mike's appointment" needs the search. Deciding which one
    the human meant by asking a 14B model to classify its own input is a failure
    mode with no upside, so this does both and lets the answer pick.
    """
    question = next((m.get("content") or "" for m in reversed(messages)
                     if m.get("role") == "user"), "").strip()
    if not question:
        return "I did not catch what to look for, sir.", [], {}
    emit("web_start", query=question[:200], where="calendar")

    t0 = time.time()
    upcoming, err = _cal_get("/calendar/upcoming", {"days": 90, "limit": 40})
    if err:
        emit("mail_error", query=question[:120], error=err[:200])
        return (f"I could not read your calendar just now, sir — {err[:140]}. "
                "That is not the same as it being empty.",
                [], {"search": round(time.time() - t0, 2)})

    found: list[dict] = []
    words = [w for w in re.findall(r"[A-Za-z]{4,}", question) if w.lower() not in _CAL_NOISE]
    if words:
        # The longest word is the likeliest to be a name or a place. Searching
        # every word would drag in half the calendar on "appointment".
        probe = max(words, key=len).lower()
        hit, search_err = _cal_get("/calendar/search", {"q": probe, "limit": 15})
        if hit:
            found = hit.get("results") or []
        elif search_err:
            emit("mail_error", query=probe, error=search_err[:200])

    seen: set = set()
    merged: list[dict] = []
    for event in found + (upcoming.get("results") or []):
        if event["id"] not in seen:
            seen.add(event["id"])
            merged.append(event)

    today = upcoming.get("today") or ""
    if not merged:
        return (f"Your calendar is clear for the next while, sir. (Today is {today}.)",
                [], {"search": round(time.time() - t0, 2)})

    # A day-name table, worked out here rather than by the model. Measured: handed
    # the correct date AND an entry reading "Friday 25 September 2026", the model
    # still answered "the 25th falls on a Sunday" when asked directly — because a
    # direct question about a weekday reads as arithmetic, and it does arithmetic
    # badly. Telling it not to calculate did not help; removing the need to does.
    ref = " | ".join(
        time.strftime("%-d %b %a", time.localtime(time.time() + i * 86400))
        for i in range(0, 22))
    events_text = "\n\n".join(
        f"[{i+1}] {e['when']} — {e['summary']}"
        + (f"\n  where: {e['location']}" if e.get("location") else "")
        + (f"\n  with: {e['attendees']}" if e.get("attendees") else "")
        + (f"\n  {e['description'][:200]}" if e.get("description") else "")
        for i, e in enumerate(merged))
    context = ("Date reference (already worked out — read from this, never calculate):\n"
               f"{ref}\n\n" + events_text)
    framed = [{"title": f"{e['when']} — {e['summary']}"[:90],
               "url": f"calendar:{e['id']}",
               "snippet": (e.get("location") or "")[:120]} for e in merged]

    t1 = time.time()
    answer = _local_answer(
        f"{question}\n\n(Today is {today}. Answer from the entries below. Each entry "
        "already states its own day and date — do not work any date out yourself, "
        "and do not add events that are not listed.)",
        context, framed)
    timings = {"search": round(t1 - t0, 2), "answer": round(time.time() - t1, 2),
               "total": round(time.time() - t0, 2), "results": len(merged)}
    emit("mail_done", query=question[:120], source="calendar", count=len(merged))
    return answer, framed, timings


def run_web(messages: list[dict], category: str = "general"):
    """Search, then answer locally. Returns (reply, sources, timings)."""
    question = next((m.get("content") or "" for m in reversed(messages)
                     if m.get("role") == "user"), "").strip()
    if not question:
        return "I did not catch the question, sir.", [], {}
    emit("web_start", query=question[:200], where="news" if category == "news" else "web")

    t0 = time.time()
    hits = searx(question, category=category)
    search_s = time.time() - t0
    if not hits:
        return ("I could not reach the search engines just now, sir — SearxNG returned nothing.",
                [], {"search": round(search_s, 2)})
    context = "\n\n".join(f"{h['title']}\n{h['url']}\n{h['snippet']}" for h in hits)
    t1 = time.time()
    text = _local_answer(question, context, hits)
    answer_s = time.time() - t1
    timings = {"search": round(search_s, 2), "answer": round(answer_s, 2),
               "total": round(search_s + answer_s, 2), "results": len(hits)}
    emit("web_done", query=question[:120], sources=hits[:6], timings=timings)
    return text, hits[:6], timings


def run_wiki(messages: list[dict]):
    """Answer from the local wiki service. Returns (reply, sources, timings)."""
    question = next((m.get("content") or "" for m in reversed(messages)
                     if m.get("role") == "user"), "").strip()
    emit("web_start", query=question[:200], where="wiki")
    t0 = time.time()
    text, sources = wiki_lookup(question)
    wiki_s = time.time() - t0
    if not text:
        return run_web(messages)          # fall through to the open web
    t1 = time.time()
    answer = _local_answer(question, text, sources)
    timings = {"wiki": round(wiki_s, 2), "answer": round(time.time() - t1, 2)}
    emit("web_done", query=question[:120], sources=sources, timings=timings)
    return answer, sources, timings


# ------------------------------------------------------------------ vision

TOOLS_DIR = os.getenv("JARVIS_TOOLS_DIR", os.path.expanduser("~/jarvis/tools"))
UPLOAD_DIR = os.getenv("JARVIS_UPLOAD_DIR", os.path.expanduser("~/jarvis/uploads"))


def _visual_lookup():
    """The VISUAL2 pipeline, imported from where it lives.

    `tools/` is its package root — its own CLI imports `visual_lookup.read` — so
    it goes on the path rather than being copied here. The tool is the tool.
    """
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    from visual_lookup.read import parse_vision_response, query_vision
    from visual_lookup.resolve import resolve_candidate
    return query_vision, parse_vision_response, resolve_candidate


def identify(image_bytes: bytes) -> dict:
    """Look at a photo and say what it is. Blocking — run it off the event loop.

    The image is written to a file because that is the shape the pipeline takes
    (its own CLI reads a path), and it is removed again afterwards: the answer is
    the text, and these are the owner's own photographs.
    """
    query_vision, parse_vision_response, resolve_candidate = _visual_lookup()
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = os.path.join(UPLOAD_DIR, f"shot-{uuid.uuid4().hex[:12]}.jpg")
    with open(path, "wb") as fh:
        fh.write(image_bytes)
    try:
        seen = parse_vision_response(query_vision(path))
        found = resolve_candidate(seen)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return {**seen, **found}


def vision_context(result: dict) -> str:
    """What the conversationalist is told about the photo it cannot see.

    The eyes and the encyclopedias are the source of fact here; the model's job
    is to say it in his own voice. So the block carries what was actually read,
    and an explicit stop on inventing the rest — a 14B model handed a picture
    description will happily elaborate on it.
    """
    lines = ["The human has just shown you a photograph. This is what the eyes read from it:"]
    if result.get("identification"):
        lines.append(f"- Identification: {result['identification']}")
    if result.get("description"):
        lines.append(f"- Appearance: {result['description']}")
    markings = result.get("markings") or []
    if markings:
        lines.append(f"- Markings: {', '.join(markings)}")
    candidates = result.get("candidates") or []
    if candidates:
        best = candidates[0]
        lines.append(f"- Encyclopedia: {best.get('title', '')} — {(best.get('extract') or '')[:300]}")
        if best.get("url"):
            lines.append(f"  {best['url']}")
        if len(candidates) > 1:
            lines.append("- Other matches: "
                         + "; ".join(c.get("title", "") for c in candidates[1:4]))
    elif result.get("sources"):
        lines.append("- No encyclopedia entry matched. Pictures of it: "
                     + "; ".join(s.get("title", "") for s in result["sources"][:3]))
    if result.get("unavailable"):
        lines.append(f"- (Could not be reached: {', '.join(result['unavailable'])})")
    lines.append("Tell them what it is, in your own voice, in a sentence or two — no lists, and "
                 "do not read this back. Use only what is here; do not invent detail beyond it. "
                 "If it is not something you can name, say what you can see.")
    return "\n".join(lines)


async def run_agent(messages: list[dict], sess: dict, conversational: bool) -> str:
    agent = build_conversationalist(sess) if conversational else build_director(sess)
    reply = await agent.ask(to_ag2_messages(messages, sess, conversational))
    text = getattr(reply, "content", None)
    if callable(text):
        text = await text()
    return apply_protocol(_strip_think(str(text if text is not None else reply)), sess, conversational)


# ---------------------------------------------------------------- app

app = FastAPI(title="jarvis brain (AG2 as OpenAI models)")


@app.get("/health")
async def health():
    with STATE_LOCK:
        sessions, jobs = len(SESSIONS), len(JOBS)
        running = sum(1 for j in JOBS.values() if j["status"] == "running")
    return {"status": "ok", "models": [MODEL_NAME, DIRECTOR_MODEL_NAME], "sessions": sessions,
            "jobs": {"total": jobs, "running": running},
            "backends": {"conversational": CONVERSATIONAL_URL, "director": DIRECTOR_URL,
                         "coder": CODER_URL}}


@app.get("/v1/models")
async def models():
    now = int(time.time())
    return {"object": "list", "data": [
        {"id": MODEL_NAME, "object": "model", "created": now, "owned_by": "jarvis"},
        {"id": DIRECTOR_MODEL_NAME, "object": "model", "created": now, "owned_by": "jarvis"},
        {"id": WEB_MODEL_NAME, "object": "model", "created": now, "owned_by": "jarvis"},
        {"id": NEWS_MODEL_NAME, "object": "model", "created": now, "owned_by": "jarvis"},
        {"id": WIKI_MODEL_NAME, "object": "model", "created": now, "owned_by": "jarvis"},
        {"id": MAIL_MODEL_NAME, "object": "model", "created": now, "owned_by": "jarvis"},
        {"id": CALENDAR_MODEL_NAME, "object": "model", "created": now, "owned_by": "jarvis"},
    ]}


@app.get("/recall")
async def recall_endpoint(q: str, limit: int = 8):
    """Search everything ever said here. The seed of Jarvis's memory: rather
    than holding the past in the window, the past is looked up on demand."""
    hits = recall(q, limit)
    return {"query": q, "hits": len(hits), "results": hits}


@app.get("/api/device/messages")
def device_messages(limit: int = 20):
    """What the device has not been told yet. The phone polls or holds /events."""
    try:
        _device_messages_ready()
        with _db() as c:
            # _db() sets no row_factory, so rows are plain tuples and dict(r)
            # raises "object is not iterable". Set it here rather than changing
            # _db(), which every other query in this file already relies on.
            c.row_factory = sqlite3.Row
            rows = [dict(r) for r in c.execute(
                "SELECT id, ts, kind, title, body, item_id FROM device_messages"
                " WHERE ack_ts IS NULL ORDER BY id LIMIT ?", (max(1, min(limit, 100)),))]
    except sqlite3.Error as exc:
        # Distinguishable from "nothing pending". An empty list and a broken
        # query must never look the same to a caller deciding whether to notify.
        return JSONResponse({"error": "db", "detail": str(exc)}, status_code=500)
    return {"pending": len(rows), "messages": rows}


@app.post("/api/device/notify")
def device_notify(payload: dict):
    """Queue a message for the device. The producer side of the channel.

    Anything that finishes work the human is waiting on can call this — the
    devteam at the end of a plan, a long job, Jarvis himself. Kept separate from
    /api/device/messages (which the phone reads) so the read path stays read-only.
    """
    body = ((payload or {}).get("body") or "").strip()
    if not body:
        return JSONResponse({"error": "body is required"}, status_code=400)
    row_id = queue_device_message(
        body,
        title=(payload or {}).get("title") or "Jarvis",
        kind=(payload or {}).get("kind") or "followup",
        item_id=(payload or {}).get("item_id") or "")
    if not row_id:
        return JSONResponse({"error": "could not queue"}, status_code=500)
    return {"ok": True, "id": row_id}


# ---------------------------------------------------------------- chats

@app.get("/api/conversations")
def conversations_list(topic: str | None = None, limit: int = 50):
    """The chat list, most recently used first."""
    try:
        rows = list_conversations(topic, limit)
    except sqlite3.Error as exc:
        # Explicit, not an empty list: "no chats" and "the query broke" must not
        # look alike to a UI deciding what to draw.
        return JSONResponse({"error": "db", "detail": str(exc)}, status_code=500)
    return {"count": len(rows), "conversations": rows}


@app.get("/api/conversations/{cid}/turns")
def conversations_turns(cid: str, limit: int = 24):
    """One conversation's transcript, oldest first."""
    try:
        turns = conversation_turns(cid, limit)
    except sqlite3.Error as exc:
        return JSONResponse({"error": "db", "detail": str(exc)}, status_code=500)
    return {"id": cid, "count": len(turns), "turns": turns}


@app.post("/api/conversations")
def conversations_new():
    """Start a chat. Returns the id to send back with every turn."""
    cid = uuid.uuid4().hex[:16]
    try:
        with _db() as c:
            c.execute("INSERT INTO sessions (key, started, touched, turns) VALUES (?,?,?,0)",
                      (cid, time.time(), time.time()))
    except sqlite3.Error as exc:
        return JSONResponse({"error": "db", "detail": str(exc)}, status_code=500)
    return {"id": cid}


@app.patch("/api/conversations/{cid}")
async def conversations_rename(cid: str, payload: dict):
    """Rename and/or move to a topic. Either field may be omitted."""
    title, topic = payload.get("title"), payload.get("topic")
    if title is None and topic is None:
        return JSONResponse({"error": "nothing to change"}, status_code=400)
    try:
        ok = rename_conversation(cid, title, topic)
    except sqlite3.Error as exc:
        return JSONResponse({"error": "db", "detail": str(exc)}, status_code=500)
    if not ok:
        return JSONResponse({"error": "no such conversation"}, status_code=404)
    emit("conversation", id=cid, title=title, topic=topic)
    return {"ok": True, "id": cid, "title": title, "topic": topic}


@app.delete("/api/conversations/{cid}")
def conversations_delete(cid: str):
    """Forget a conversation — transcript, search copies and what was learned.

    Irreversible, and it reports what it destroyed rather than just claiming
    success, so the caller can say "that removed 12 turns and 4 remembered facts"
    instead of a bare OK. It also tombstones the id, which is what lets the
    Workhorse purge it out of the backup snapshots taken before now.
    """
    try:
        counts = delete_conversation(cid)
    except sqlite3.Error as exc:
        return JSONResponse({"error": "db", "detail": str(exc)}, status_code=500)
    emit("conversation_deleted", id=cid, **counts)
    return {"ok": True, "id": cid, "destroyed": counts}


@app.get("/api/topics")
def topics():
    """The topic vocabulary already in use, for a picker."""
    try:
        return {"topics": topic_list()}
    except sqlite3.Error as exc:
        return JSONResponse({"error": "db", "detail": str(exc)}, status_code=500)


@app.post("/api/device/ack")
def device_ack(payload: dict):
    """Mark delivered. Idempotent: a re-delivered notification must not error."""
    ids = (payload or {}).get("ids")
    if ids is None:
        ids = [(payload or {}).get("id")]
    if isinstance(ids, int):          # a bare int is not iterable
        ids = [ids]
    elif not isinstance(ids, (list, tuple)):
        ids = []
    ids = [i for i in ids if isinstance(i, int)]
    if not ids:
        return JSONResponse({"error": "ids (list of ints) required"}, status_code=400)
    try:
        _device_messages_ready()
        with _db() as c:
            c.executemany("UPDATE device_messages SET ack_ts=? WHERE id=? AND ack_ts IS NULL",
                          [(time.time(), i) for i in ids])
    except sqlite3.Error as exc:
        return JSONResponse({"error": "db", "detail": str(exc)}, status_code=500)
    return {"ok": True, "acked": len(ids)}


@app.get("/events")
async def events(request: Request):
    sink: queue.Queue = queue.Queue(maxsize=256)
    with STATE_LOCK:
        EVENT_SINKS.add(sink)

    async def gen():
        try:
            yield f"data: {json.dumps({'kind': 'hello', 'ts': time.time()})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = sink.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.25)
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            with STATE_LOCK:
                EVENT_SINKS.discard(sink)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/approve")
async def approve(payload: dict):
    key = payload.get("session")
    approved = bool(payload.get("approved"))
    them: dict | None = None
    with STATE_LOCK:
        if key and key in SESSIONS:
            SESSIONS[key]["approved"] = approved
            plan = SESSIONS[key]["plan"]
            if not approved:
                SESSIONS[key]["plan"] = ""
            else:
                them = SESSIONS[key]
        else:
            pending = [s for s in SESSIONS.values() if s["plan"] and not s["approved"]]
            if not pending:
                return JSONResponse({"status": "no pending plan"}, status_code=404)
            them = max(pending, key=lambda s: s["touched"])
            them["approved"] = approved
            plan = them["plan"]
            if not approved:
                them["plan"] = ""
                them = None
    emit("approval", approved=approved, plan=plan[:400])
    # Deliberately OUTSIDE the lock: resume_escalation() -> escalate() takes
    # STATE_LOCK itself, and it is a plain Lock, not a reentrant one. Holding it
    # here would deadlock the approval — which looks exactly like the button
    # doing nothing.
    if them is not None:
        resume_escalation(them)
    return {"status": "approved" if approved else "rejected", "plan": plan[:400]}


@app.post("/vision")
async def vision(request: Request):
    """Identify a photo the human attached in the panel.

    Raw bytes rather than multipart, for the same reason the panel's
    /api/transcribe reads a raw body and forwards it: neither side then needs a
    multipart parser, and the image is passed along exactly as it arrived.

    Returns the pipeline's reading plus `context` — the block the panel hands
    back to /v1/chat/completions so the conversationalist can talk about it.
    """
    image = await request.body()
    if not image:
        return JSONResponse({"error": "empty body"}, status_code=400)
    emit("vision_start", bytes=len(image))
    try:
        result = await asyncio.to_thread(identify, image)
    except Exception as exc:  # noqa: BLE001
        # A dead eyes service must not look like an object nobody can name.
        emit("vision_failed", error=f"{type(exc).__name__}: {exc}")
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=502)
    emit("vision_done", identification=result.get("identification", ""))
    return {**result, "context": vision_context(result)}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    requested = (body.get("model") or MODEL_NAME).lower()
    web = WEB_MODEL_NAME in requested
    news = NEWS_MODEL_NAME in requested
    wiki = WIKI_MODEL_NAME in requested
    mail = MAIL_MODEL_NAME in requested
    calendar = CALENDAR_MODEL_NAME in requested
    fetching = web or news or wiki or mail or calendar
    conversational = (DIRECTOR_MODEL_NAME not in requested) and not fetching
    sess = session_for(messages, body.get("conversation"))
    # A photo the panel attached to this turn: the context block /vision composed
    # from what the eyes read. See to_ag2_messages for why it rides the session.
    if body.get("vision"):
        sess["vision"] = str(body["vision"])[:4000]
    # epub = "keep nothing". Set by the panel's incognito toggle; OWUI callers
    # simply never send it, so their conversations are kept by default.
    ephemeral = bool(body.get("ephemeral"))
    # Carried on the session so the directive handlers can see it: a promise made
    # in a test chat must not be queued to the phone. See take_followups.
    sess["ephemeral"] = ephemeral

    last_user = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
    # `not conversational` used to be here, which meant an approval was only heard
    # if the owner had switched the panel to jarvis-director. Now that the actor can
    # escalate, the plan is put to them by the voice they actually talk to — so a
    # "yes" to Kunou must open the same gate. The gate itself is unchanged: it still
    # needs a plan to exist and to be unapproved, and it is still only ever opened by
    # the human's own words (or the panel button), never by a model.
    if not fetching and sess.get("plan") and not sess.get("approved") \
            and looks_like_approval(last_user):
        sess["approved"] = True
        emit("approval", approved=True, plan=sess["plan"][:400])
        resume_escalation(sess)

    def fetch_kind():
        if news:
            return run_web(messages, category="news")
        if wiki:
            return run_wiki(messages)
        if mail:
            return run_mail(messages)
        if calendar:
            return run_calendar(messages)
        return run_web(messages)

    if not stream:
        sources: list[dict] = []
        timings: dict = {}
        if fetching:
            text, sources, timings = await asyncio.to_thread(fetch_kind)
        else:
            text = await run_agent(messages, sess, conversational)
        body_out = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}", "object": "chat.completion",
            "created": int(time.time()), "model": requested,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
        }
        # Non-standard fields: clients that know about them (the docks/live
        # pages) follow a voice change and show sources; everyone else ignores.
        voice = sess.pop("voice", None)
        if voice:
            body_out["voice"] = voice
        media = sess.pop("media", None)
        if media:
            body_out["media"] = media
        if sources:
            body_out["sources"] = sources
        if timings:
            body_out["timings"] = timings
        # Keep the exchange unless this conversation is incognito.
        record(sess.get("key", ""), requested, "user", last_user, ephemeral)
        record(sess.get("key", ""), requested, "assistant", text, ephemeral)
        return JSONResponse(body_out)

    async def gen():
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        def frame_of(delta: dict, finish=None) -> str:
            payload = {"id": cid, "object": "chat.completion.chunk", "created": created,
                       "model": requested,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            return f"data: {json.dumps(payload)}\n\n"

        # The conversationalist streams for real: the client hears the first
        # sentence while the rest is still being written. This is the whole
        # point of the split — speech overlaps generation instead of waiting
        # for it.
        if conversational and not fetching:
            yield frame_of({"role": "assistant", "content": ""})
            said = []
            try:
                async for piece in _aiter_stream(messages, sess):
                    said.append(piece)
                    yield frame_of({"content": piece})
            except Exception as exc:  # noqa: BLE001
                yield frame_of({"content": f"\n\n_[trouble: {type(exc).__name__}: {exc}]_"})
            text = "".join(said).strip()
            if not text:
                text = "Of course, sir — here it is."
                yield frame_of({"content": text})
            record(sess.get("key", ""), requested, "user", last_user, ephemeral)
            record(sess.get("key", ""), requested, "assistant", text, ephemeral)
            media = sess.pop("media", None)
            if media:
                yield "data: " + json.dumps({"media": media}) + "\n\n"
            voice = sess.pop("voice", None)
            if voice:
                yield "data: " + json.dumps({"voice": voice}) + "\n\n"
            yield frame_of({}, finish="stop")
            yield "data: [DONE]\n\n"
            return

        out: queue.Queue = queue.Queue()

        def worker():
            try:
                if fetching:
                    text, srcs, tm = fetch_kind()
                    if srcs:                      # sources first: the stream ends at "final"
                        out.put(("sources", srcs))
                    if tm:
                        out.put(("timings", tm))
                    out.put(("final", text))
                else:
                    out.put(("final", asyncio.run(run_agent(messages, sess, conversational))))
            except Exception as exc:  # noqa: BLE001
                out.put(("error", f"{type(exc).__name__}: {exc}"))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        def frame(delta: dict, finish=None) -> str:
            payload = {"id": cid, "object": "chat.completion.chunk", "created": created,
                       "model": requested,
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            return f"data: {json.dumps(payload)}\n\n"

        yield frame({"role": "assistant", "content": ""})
        if sess.get("media"):
            yield "data: " + json.dumps({"media": sess.pop("media")}) + "\n\n"
        started = time.time()
        while True:
            try:
                kind, payload = out.get_nowait()
            except queue.Empty:
                if not thread.is_alive():
                    break
                await asyncio.sleep(0.2)
                if time.time() - started > 20:
                    started = time.time()
                    yield ": keepalive\n\n"
                continue
            if kind == "final":
                record(sess.get("key", ""), requested, "user", last_user, ephemeral)
                record(sess.get("key", ""), requested, "assistant", payload, ephemeral)
                for word in re.findall(r"\S+\s*", payload):
                    yield frame({"content": word})
                    await asyncio.sleep(0.012)
                break
            if kind == "sources":
                yield "data: " + json.dumps({"model": requested, "sources": payload}) + "\n\n"
                continue
            if kind == "timings":
                yield "data: " + json.dumps({"model": requested, "timings": payload}) + "\n\n"
                continue
            yield frame({"content": f"\n\n_[{payload}]_\n\n"})
        yield frame({}, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8092")))
