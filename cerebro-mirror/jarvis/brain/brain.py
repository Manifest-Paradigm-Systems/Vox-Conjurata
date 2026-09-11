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


def emit(kind: str, **fields) -> None:
    event = {"ts": time.time(), "kind": kind, **fields}
    with STATE_LOCK:
        sinks = list(EVENT_SINKS)
    for sink in sinks:
        try:
            sink.put_nowait(event)
        except queue.Full:
            pass


def session_for(messages: list[dict]) -> dict:
    first_user = next((m.get("content") or "" for m in messages if m.get("role") == "user"), "")
    system = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
    key = hashlib.sha1(f"{system}\x00{first_user}".encode()).hexdigest()[:16]
    now = time.time()
    with STATE_LOCK:
        for k, s in list(SESSIONS.items()):
            if now - s["touched"] > SESSION_TTL:
                SESSIONS.pop(k, None)
        sess = SESSIONS.get(key)
        if sess is None:
            sess = {"approved": False, "plan": "", "failures": 0, "log": [],
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
    system = CODER_SYSTEM if target == "coder" else DIRECTOR_SYSTEM
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


DELEGATE_RE = re.compile(r"^\s*@@DELEGATE\s+(coder|director)\s*:\s*(.+?)\s*$",
                         re.IGNORECASE | re.MULTILINE)
PLAN_RE = re.compile(r"@@PLAN\s*\n(.*?)\n\s*@@END", re.DOTALL)
HANDOFF_RE = re.compile(r"^\s*@@HANDOFF\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)

# AG2 1.0.4 registers tools but never executes them (llama.cpp returns a valid
# tool_calls block; AG2 answers with the model's raw text and no ToolResult).
# Delegation therefore rides an explicit text protocol the brain parses — less
# elegant, but it works with every model here and is trivial to debug.
VOICE_RE = re.compile(r"^\s*@@VOICE\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
VOICES_URL = os.getenv("JARVIS_VOICES_URL", "http://192.168.0.62:7863/v1/audio/voices")
_VOICE_CACHE: dict = {"at": 0.0, "voices": []}


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


def resolve_voice(description: str) -> str | None:
    """Best seed for a spoken description ("a scottish dwarf" ->
    archetype_dwarf_male_scottish). Ranks by how many query words match, then
    by how *little else* the name says, then gender and name for determinism.
    Nothing sensible -> None, and the voice stays put."""
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
    "    @@DELEGATE coder: <one concrete task>\n"
    "or  @@DELEGATE director: <a task needing a considered plan>\n"
    "You may emit more than one. Say in one short sentence that it is underway, then carry on. The "
    "system removes the @@ line before the human sees your reply, and it will show you the result "
    "when it lands. Never invent a result you have not been given. If a result is in the background "
    "state, report it naturally in speech.\n"
    "When the human asks you to speak in a different voice, emit:\n"
    "    @@VOICE: <a description of the voice, e.g. a scottish dwarf, a calm narrator>\n"
    "The system picks the closest voice from the library and switches to it. Confirm briefly; the "
    "line itself is hidden."
)


def build_conversationalist(sess: dict) -> Agent:
    prompt = (
        f"{load_persona()}\n\n"
        "You are the VOICE — the one the human actually talks to. You are quick, and you stay "
        "quick: you never sit in silence while heavy work happens. Keep replies short — this is "
        "speech, not an essay: one to three sentences unless asked for detail."
        f"{PROTOCOL}"
    )
    return Agent(
        name="jarvis",
        prompt=prompt,
        config=OpenAIConfig(model=CONVERSATIONAL_MODEL, api_key="local",
                            base_url=f"{CONVERSATIONAL_URL}/v1"),
    )


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
        f"{DIRECTOR_PROTOCOL}"
    )
    return Agent(
        name="director",
        prompt=prompt,
        config=OpenAIConfig(model="director", api_key="local", base_url=f"{DIRECTOR_URL}/v1"),
    )


def apply_protocol(reply: str, sess: dict, conversational: bool) -> str:
    """Turn the model's @@ directives into real work, and return what the human
    sees (directives stripped, handoff results inlined)."""
    if conversational:
        delegations = DELEGATE_RE.findall(reply)
        text = DELEGATE_RE.sub("", reply).strip()
        for target, task in delegations:
            job = start_job(target.lower(), task.strip())
            if not text:
                text = (f"Of course, sir — I've put that in front of the "
                        f"{target.lower()} and will report back shortly.")
        voice_match = VOICE_RE.search(text)
        if voice_match:
            wanted = voice_match.group(1).strip()
            chosen = resolve_voice(wanted)
            text = VOICE_RE.sub("", text).strip()
            if chosen:
                sess["voice"] = chosen
                # Say what it became — otherwise the model's "as you wish" is
                # all the human gets, with no idea which voice was picked.
                text = f"{text}\n\n_[voice → {chosen}]_".strip()
            else:
                text = (f"{text}\n\n_[no voice in the library matches "
                        f"“{wanted}”]_").strip()
        return text or reply.strip()

    plan_match = PLAN_RE.search(reply)
    if plan_match:
        plan = plan_match.group(1).strip()
        sess["plan"] = plan
        sess["approved"] = False
        sess["failures"] = 0
        sess["log"] = []
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
            result = _chat(CODER_URL, "coder", CODER_SYSTEM, task, max_tokens=2000)
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


def to_ag2_messages(messages: list[dict], sess: dict, conversational: bool) -> list[dict]:
    out = [m for m in messages if m.get("role") in ("user", "assistant", "system") and m.get("content")]
    if conversational:
        digest = job_digest()
        if digest:
            out.append({"role": "system",
                        "content": f"Background work state (do not read this aloud; use it):\n{digest}"})
        return out
    if sess.get("approved") and sess.get("plan"):
        out.append({"role": "system",
                    "content": f"The human APPROVED your plan. You may now hand tasks to the coder, "
                               f"one at a time.\nPlan:\n{sess['plan']}"})
    elif sess.get("plan"):
        out.append({"role": "system",
                    "content": "A plan is awaiting the human's verdict. If their last message approves "
                               "it, proceed; otherwise treat it as a revision request."})
    return out


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
    ]}


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
    with STATE_LOCK:
        if key and key in SESSIONS:
            SESSIONS[key]["approved"] = approved
            plan = SESSIONS[key]["plan"]
            if not approved:
                SESSIONS[key]["plan"] = ""
        else:
            pending = [s for s in SESSIONS.values() if s["plan"] and not s["approved"]]
            if not pending:
                return JSONResponse({"status": "no pending plan"}, status_code=404)
            sess = max(pending, key=lambda s: s["touched"])
            sess["approved"] = approved
            plan = sess["plan"]
            if not approved:
                sess["plan"] = ""
    emit("approval", approved=approved, plan=plan[:400])
    return {"status": "approved" if approved else "rejected", "plan": plan[:400]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    requested = (body.get("model") or MODEL_NAME).lower()
    conversational = DIRECTOR_MODEL_NAME not in requested
    sess = session_for(messages)

    last_user = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
    if not conversational and sess.get("plan") and not sess.get("approved") and looks_like_approval(last_user):
        sess["approved"] = True
        emit("approval", approved=True, plan=sess["plan"][:400])

    if not stream:
        text = await run_agent(messages, sess, conversational)
        body_out = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}", "object": "chat.completion",
            "created": int(time.time()), "model": requested,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}],
        }
        # Non-standard field: clients that know about it (the docks/live pages)
        # follow a voice change; everyone else ignores it.
        voice = sess.pop("voice", None)
        if voice:
            body_out["voice"] = voice
        return JSONResponse(body_out)

    async def gen():
        cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        out: queue.Queue = queue.Queue()

        def worker():
            try:
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
                for word in re.findall(r"\S+\s*", payload):
                    yield frame({"content": word})
                    await asyncio.sleep(0.012)
                break
            yield frame({"content": f"\n\n_[{payload}]_\n\n"})
        yield frame({}, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8092")))
