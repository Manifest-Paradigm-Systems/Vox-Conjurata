"""jarvis docks — the architect panel.

One page that shows what the agent is *doing*: plans it has proposed, tasks it
has handed to the coder, results coming back, and the approval gate. It also
hosts the face (upstream ai-visualizer, proxied — the panel is the only thing
that knows both the event stream and the face bus).

Layout: the face is the centrepiece, the docks are the running commentary.

  GET  /                 the page
  GET  /events           SSE, proxied from the brain
  GET  /api/faces        face list, proxied from the visualizer
  GET  /viz/*            everything else, proxied from the visualizer (faces,
                         /state, core.js, assets)
  POST /viz/state        {state: idle|listening|thinking|speaking}  -> bus file
  POST /viz/waveform     {samples: [floats]}                        -> bus file
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

BRAIN_URL = os.getenv("JARVIS_BRAIN_URL", "http://127.0.0.1:8092")
VIZ_URL = os.getenv("JARVIS_VIZ_URL", "http://127.0.0.1:8790")
BUS_DIR = os.getenv("JARVIS_BUS_DIR", "/var/home/admin/jarvis/viz-bus")
# The Android shell is published here so a phone can install or update it
# straight from the panel — no adb, no cable. Source: ~/jarvis-android.
APK_PATH = os.getenv("JARVIS_APK_PATH", "/var/home/admin/jarvis/jarvis.apk")
VALID_STATES = {"idle", "listening", "thinking", "speaking"}
# The conversational sting: a sub-second rising kalimba from the Sonniss pack
# (UCS category UI). Played client-side at low volume when Jarvis starts
# speaking — it is a UI sound, so it exists only where there is a screen, which
# is also why nothing needs to suppress it on a phone line.
# ---- the sonic vocabulary -------------------------------------------------
# A map of conversational EVENT -> sound from the foley library. The words
# carry content; these carry STATE, which is why they are rare and why they
# fire at sentence boundaries rather than over his speech.
VOCAB_PATH = os.getenv("JARVIS_SFX_VOCAB",
                       "/var/home/admin/jarvis/panel/sfx-vocabulary.json")
FOLEY_ROOT = os.getenv(
    "JARVIS_FOLEY_ROOT",
    "/var/home/EvokeStudio/manifest-paradigm/cinematome/data/foley/pack")


def load_vocabulary() -> dict:
    """The vocabulary is DATA here, not files.

    The panel runs on cerebro and the foley library lives on the Workhorse, so
    checking each entry against a local path would drop all of them (it did).
    Whether a sting can actually be produced is the adapter's business; this
    only needs each event's meaning and mix level.
    """
    try:
        raw = json.loads(pathlib.Path(VOCAB_PATH).read_text())
    except (OSError, ValueError) as exc:
        print(f"[panel] no sfx vocabulary ({exc})", flush=True)
        return {}
    events = raw.get("events") or {}
    print(f"[panel] sfx vocabulary: {len(events)} events", flush=True)
    return events


VOCABULARY = load_vocabulary()


CHIRP_PATH = os.getenv(
    "JARVIS_CHIRP_PATH",
    "CB_Sounddesign - Applicable Sounds - Organic UI and Building Games SFX/"
    "UIMisc_Kalimba 3 Up_CB Sounddesign_APPlicable Sounds.opus")

app = FastAPI(title="jarvis docks")


@app.get("/api/sfx/{event}")
async def sfx(event: str):
    """One sting, proxied from the adapter (the audio lives on the Workhorse)."""
    if event not in VOCABULARY:
        return JSONResponse({"error": f"no sting for {event!r}"}, status_code=404)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0)) as c:
            r = await c.get(f"{TTS_MEDIA_URL}/stings/{event}")
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return Response(content=r.content, status_code=r.status_code, media_type="audio/ogg")


@app.get("/api/sfx")
async def sfx_catalogue():
    """What the vocabulary contains — the client fetches this once."""
    return {e: {"means": s.get("means"), "when": s.get("when"), "gain": s.get("gain", 0.2)}
            for e, s in VOCABULARY.items()}


def _write_bus(name: str, payload: str) -> None:
    os.makedirs(BUS_DIR, exist_ok=True)
    tmp = os.path.join(BUS_DIR, f".{name}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
    os.replace(tmp, os.path.join(BUS_DIR, name))


@app.post("/viz/state")
async def set_state(payload: dict):
    state = (payload.get("state") or "idle").lower()
    if state not in VALID_STATES:
        return JSONResponse({"error": f"unknown state {state!r}"}, status_code=400)
    _write_bus(".voice_state", state)
    return {"state": state}


@app.post("/viz/waveform")
async def set_waveform(payload: dict):
    samples = payload.get("samples") or []
    try:
        samples = [max(-1.0, min(1.0, float(s))) for s in samples][:64]
    except (TypeError, ValueError):
        return JSONResponse({"error": "samples must be numbers"}, status_code=400)
    _write_bus(".voice_waveform", json.dumps({"ts": time.time(), "samples": samples}))
    return {"samples": len(samples)}


@app.get("/api/faces")
async def faces():
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as c:
            cfg = (await c.get(f"{VIZ_URL}/config")).json()
        return {"name": cfg.get("name", "JARVIS"), "default": cfg.get("face", "board"),
                "faces": cfg.get("faces", [])}
    except (httpx.HTTPError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.get("/events")
async def events(request: Request):
    """Server-side proxy of the brain's SSE feed, so the page stays same-origin."""

    async def gen():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=8.0)) as c:
                async with c.stream("GET", f"{BRAIN_URL}/events") as upstream:
                    async for line in upstream.aiter_lines():
                        if await request.is_disconnected():
                            break
                        if line:
                            yield line + "\n"
                        else:
                            yield "\n"
        except httpx.HTTPError as exc:
            yield f"data: {json.dumps({'kind': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/approve")
async def approve(payload: dict):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            r = await c.post(f"{BRAIN_URL}/approve", json=payload)
        return Response(content=r.content, status_code=r.status_code, media_type="application/json")
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


STT_URL = os.getenv("JARVIS_STT_URL", "http://127.0.0.1:8091")
TTS_URL = os.getenv("JARVIS_TTS_URL", "http://192.168.0.62:7863")
# Speech and media were one host and are not any more: Jarvis's voice moved to Kokoro
# (:8025, CPU) so it stops competing with the GPU, while the stings, the chirp and the
# foley library still live on the voices-adapter on the Workhorse. Keeping them apart is
# what lets the voice move without taking the sonic vocabulary with it. Defaults to
# TTS_URL so an unset environment behaves exactly as before.
TTS_MEDIA_URL = os.getenv("JARVIS_TTS_MEDIA_URL", TTS_URL)
VOICE = os.getenv("JARVIS_VOICE", "jarvis")


@app.post("/api/transcribe")
async def transcribe(request: Request):
    """Same-origin proxy for the mic: the browser posts audio here, the panel
    forwards it to the STT shim. Keeps the page free of CORS."""
    body = await request.body()
    headers = {"content-type": request.headers.get("content-type", "multipart/form-data")}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=8.0)) as c:
            r = await c.post(f"{STT_URL}/v1/audio/transcriptions", content=body, headers=headers)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


@app.post("/api/vision")
async def vision(request: Request):
    """Identify a photo the human attached.

    The same shape as /api/transcribe: the browser posts the image here and the
    panel forwards it on, so the page stays free of CORS and the brain is never
    exposed to the network. Raw bytes rather than multipart, for the same
    reason — neither side then needs a multipart parser.
    """
    body = await request.body()
    if not body:
        return JSONResponse({"error": "empty image"}, status_code=400)
    headers = {"content-type": request.headers.get("content-type", "image/jpeg")}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=8.0)) as c:
            r = await c.post(f"{BRAIN_URL}/vision", content=body, headers=headers)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


@app.post("/api/chat")
async def chat(payload: dict):
    """Chat, streaming when the caller asks for it.

    The live page streams so it can start SPEAKING the first sentence while the
    rest is still being written — so SSE is relayed straight through rather
    than buffered into a single JSON reply."""
    body = dict(payload)
    body.setdefault("model", "jarvis")
    if not body.get("stream"):
        body["stream"] = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=8.0)) as c:
                r = await c.post(f"{BRAIN_URL}/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return Response(content=r.content, status_code=r.status_code,
                        media_type="application/json")

    async def relay():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=8.0)) as c:
                async with c.stream("POST", f"{BRAIN_URL}/v1/chat/completions", json=body) as r:
                    async for line in r.aiter_lines():
                        yield line + "\n"
        except httpx.HTTPError as exc:
            yield "data: " + json.dumps({"choices": [{"delta": {
                "content": "_[connection lost] %s_" % exc}}]}) + "\n\n"

    return StreamingResponse(relay(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


FEATURED_VOICES = [v.strip() for v in
                   os.getenv("JARVIS_FEATURED_VOICES", "jarvis,c3po,narrator").split(",") if v.strip()]


@app.get("/api/models")
async def api_models():
    """Which brains the conversationalist picker offers — straight from the
    brain, so adding one there is enough."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as c:
            data = (await c.get(f"{BRAIN_URL}/v1/models")).json()
    except (httpx.HTTPError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
    return {"models": ids, "default": "jarvis"}


@app.get("/api/voices")
async def voices():
    """Every voice seed the Higgs library offers, featured ones first — the
    list feeds the pickers, so the voice can change without a redeploy."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as c:
            data = (await c.get(f"{TTS_URL}/v1/audio/voices")).json()
    except (httpx.HTTPError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    all_voices = data.get("voices", [])
    featured = [v for v in FEATURED_VOICES if v in all_voices]
    rest = [v for v in all_voices if v not in featured]
    return {"featured": featured, "voices": rest, "default": os.getenv("JARVIS_VOICE", VOICE)}


@app.post("/api/speak")
async def speak(payload: dict):
    body = {"input": payload.get("input", ""), "voice": payload.get("voice", VOICE),
            "response_format": "mp3"}
    # Higgs renders a handful of clips at a time and answers 502 for the overflow, so a
    # reply rendered sentence by sentence loses whichever sentences did not fit. Keeping
    # the upstream's own status matters: flattening every failure to 502 is what made
    # the page's "queue is full, back off" retry unreachable.
    detail, status = "tts unavailable", 502
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=8.0)) as c:
                r = await c.post(f"{TTS_URL}/v1/audio/speech", json=body)
        except httpx.HTTPError as exc:
            detail, status = f"tts unreachable: {exc}", 502
        else:
            if r.status_code == 200:
                return Response(content=r.content, media_type="audio/mpeg")
            detail, status = f"tts {r.status_code}", r.status_code
            if r.status_code not in (429, 500, 502, 503):
                break                    # a real refusal, not a busy queue
        await asyncio.sleep(0.4 * (attempt + 1))
    return JSONResponse({"error": detail}, status_code=status)


@app.post("/api/state")
async def api_state(payload: dict):
    return await set_state(payload)


async def _viz_get(path: str) -> Response:
    """Fetch one path from the visualizer and hand it back as-is."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as c:
            r = await c.get(f"{VIZ_URL}/{path}")
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    ctype = r.headers.get("content-type", "application/json")
    return Response(content=r.content, status_code=r.status_code, media_type=ctype)


# core.js polls these with ABSOLUTE paths, so a face served from
# /viz/faces/<id>/ asks the PANEL's root for them, not the visualizer's — and
# without them the face has no state to draw: no pulses, no waveform, and its
# fallback thinking loop. They must be proxied at the root, not under /viz/.
@app.get("/state")
async def viz_state():
    return await _viz_get("state")


@app.get("/config")
async def viz_config():
    return await _viz_get("config")


@app.get("/api/chirp")
async def chirp():
    """The sting itself, same-origin so the page can just play it."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0)) as c:
            r = await c.get(f"{TTS_MEDIA_URL}/media/foley/audio", params={"path": CHIRP_PATH})
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return Response(content=r.content, status_code=r.status_code, media_type="audio/ogg")


@app.get("/media/{path:path}")
async def media_proxy(path: str, request: Request):
    """Foley and music live on the Workhorse (audio stays there); the panel
    re-serves them same-origin so the browser can just play the URL."""
    # The adapter's media routes live under /media — keep the prefix.
    url = f"{TTS_MEDIA_URL}/media/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(200.0, connect=8.0)) as c:
            r = await c.get(url)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    headers = {}
    ctype = r.headers.get("content-type")
    if ctype:
        headers["content-type"] = ctype
    return Response(content=r.content, status_code=r.status_code, headers=headers)


BOARD_URL = os.getenv("JARVIS_BOARD_URL", "http://127.0.0.1:8094")


@app.get("/team")
async def team_root():
    """Redirect to the trailing-slash form so the panels' relative links resolve
    inside /team/ rather than escaping to the docks' own routes."""
    return RedirectResponse("/team/", status_code=307)


@app.get("/team/{path:path}")
async def team_proxy(path: str, request: Request):
    """Serve the dev team panels (silent / architect / tasks) under this origin."""
    url = f"{BOARD_URL}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as c:
            r = await c.get(url)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    headers = {}
    ctype = r.headers.get("content-type")
    if ctype:
        headers["content-type"] = ctype
    return Response(content=r.content, status_code=r.status_code, headers=headers)


@app.get("/viz/{path:path}")
async def viz_proxy(path: str, request: Request):
    """Pass faces, core.js and assets straight through — upstream stays
    untouched (and unmodified, per its AGPL terms)."""
    url = f"{VIZ_URL}/{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as c:
            r = await c.get(url)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    headers = {}
    ctype = r.headers.get("content-type")
    if ctype:
        headers["content-type"] = ctype
    return Response(content=r.content, status_code=r.status_code, headers=headers)


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>JARVIS — architect docks</title>
<style>
  :root { --bg:#05070a; --panel:#0b1016; --line:#16202b; --text:#cfe3f2; --dim:#6b8497;
          --accent:#39d0ff; --ok:#4ade80; --warn:#ffb454; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  header { display:flex; align-items:center; gap:16px; padding:10px 16px;
           border-bottom:1px solid var(--line); background:var(--panel); }
  header h1 { font-size:14px; letter-spacing:.18em; margin:0; color:var(--accent); font-weight:600; }
  header .spacer { flex:1; }
  select, button { background:#0e1620; color:var(--text); border:1px solid var(--line);
                   border-radius:6px; padding:6px 10px; font:inherit; }
  button:hover { border-color:var(--accent); cursor:pointer; }
  a { color:var(--accent); text-decoration:none; }
  main { display:grid; grid-template-columns:1fr 380px; height:calc(100vh - 49px); }
  #face { border:0; width:100%; height:100%; background:#000; }
  aside { border-left:1px solid var(--line); background:var(--panel);
          display:flex; flex-direction:column; overflow:hidden; }
  aside h2 { font-size:11px; letter-spacing:.16em; color:var(--dim); margin:0;
             padding:12px 14px 8px; text-transform:uppercase; }
  #feed { overflow-y:auto; padding:0 14px 14px; flex:1; }
  .ev { border-left:2px solid var(--line); padding:8px 10px; margin-bottom:8px;
        background:#0d141c; border-radius:0 6px 6px 0; animation:fade .25s ease; }
  .ev .t { color:var(--dim); font-size:11px; }
  .ev.plan { border-left-color:var(--warn); }
  .ev.coder_start { border-left-color:var(--accent); }
  .ev.coder_done.ok { border-left-color:var(--ok); }
  .ev.coder_done.bad { border-left-color:#ff6b6b; }
  .ev.approval { border-left-color:var(--ok); }
  #gate { padding:12px 14px; border-top:1px solid var(--line); display:none; gap:8px; }
  #gate button { flex:1; }
  #gate button.yes { border-color:var(--ok); color:var(--ok); }
  #gate button.no { border-color:#ff6b6b; color:#ff6b6b; }
  @keyframes fade { from { opacity:0; transform:translateY(-3px);} to {opacity:1;} }
</style>
</head>
<body>
<header>
  <h1>ARCHITECT DOCKS</h1>
  <span id="status" style="color:var(--dim)">connecting…</span>
  <div class="spacer"></div>
  <select id="facePicker" title="Choose a face"></select>
  <input id="voicePicker" list="voiceList" title="Voice for spoken replies (shared with Live)"
         placeholder="voice…" style="width:130px">
  <datalist id="voiceList"></datalist>
  <a href="/live"><button>● Live voice</button></a>
  <a href="/team/"><button>Dev team</button></a>
  <a href="http://192.168.0.67:8080" target="_blank"><button>Open WebUI ↗</button></a>
</header>
<main>
  <iframe id="face" src="/viz/" allow="autoplay"></iframe>
  <aside>
    <h2>Agent activity</h2>
    <div id="feed"></div>
    <div id="gate">
      <button class="yes" onclick="decide(true)">Approve plan</button>
      <button class="no" onclick="decide(false)">Reject</button>
    </div>
  </aside>
</main>
<script>
const feed = document.getElementById('feed');
const gate = document.getElementById('gate');
const status = document.getElementById('status');
let pendingPlan = false;

function line(kind, text) {
  const el = document.createElement('div');
  el.className = 'ev ' + kind;
  el.innerHTML = `<div class="t">${new Date().toLocaleTimeString()}</div><div>${text}</div>`;
  feed.prepend(el);
  while (feed.children.length > 120) feed.lastChild.remove();
}
const esc = s => (s || '').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

// Voice picker — shared with /live via localStorage, so the voice you pick
// here is the voice Jarvis speaks in everywhere.
let VOICE = localStorage.getItem('jarvis.voice') || '';
fetch('/api/voices').then(r => r.json()).then(cfg => {
  if (cfg.error) return;
  const list = document.getElementById('voiceList');
  const add = (v, label) => {
    const o = document.createElement('option');
    o.value = v; o.label = label;
    list.appendChild(o);
  };
  (cfg.featured || []).forEach(v => add(v, v + '  ★'));
  (cfg.voices || []).forEach(v => add(v, ''));
  if (!VOICE) VOICE = cfg.default || 'jarvis';
  const picker = document.getElementById('voicePicker');
  picker.value = VOICE;
  picker.onchange = () => {
    VOICE = picker.value.trim();
    localStorage.setItem('jarvis.voice', VOICE);
  };
}).catch(() => {});

fetch('/api/faces').then(r => r.json()).then(cfg => {
  const picker = document.getElementById('facePicker');
  (cfg.faces || []).forEach(f => {
    const o = document.createElement('option');
    o.value = f.id; o.textContent = f.title + ' — ' + (f.tagline || '').slice(0, 48);
    if (f.id === cfg.default) o.selected = true;
    picker.appendChild(o);
  });
  // Shared with /live — whichever page you pick a face on, both follow.
  const chosen = localStorage.getItem('jarvis.face') || cfg.default || 'board';
  picker.value = chosen;
  picker.onchange = () => {
    localStorage.setItem('jarvis.face', picker.value);
    document.getElementById('face').src = '/viz/faces/' + picker.value + '/';
  };
  document.getElementById('face').src = '/viz/faces/' + chosen + '/';
}).catch(() => {});

const es = new EventSource('/events');
es.onopen = () => { status.textContent = 'live'; status.style.color = 'var(--ok)'; };
es.onerror = () => { status.textContent = 'reconnecting…'; status.style.color = 'var(--warn)'; };
es.onmessage = ev => {
  let e; try { e = JSON.parse(ev.data); } catch { return; }
  if (e.kind === 'plan') {
    pendingPlan = true; gate.style.display = 'flex';
    line('plan', '<b>Plan for approval</b><br>' + esc(e.plan));
  } else if (e.kind === 'coder_start') {
    line('coder_start', '→ coder: ' + esc(e.task));
  } else if (e.kind === 'coder_done') {
    line('coder_done ' + (e.ok ? 'ok' : 'bad'),
         (e.ok ? '✓ ' : '✗ ') + esc(e.task) + '<br><span style="color:var(--dim)">' + esc(e.reply) + '</span>');
  } else if (e.kind === 'approval') {
    pendingPlan = false; gate.style.display = 'none';
    line('approval', e.approved ? 'Plan approved' : 'Plan rejected');
  } else if (e.kind === 'error') {
    line('coder_done bad', 'brain: ' + esc(e.error));
  }
};
// The brain's event stream is the trigger source: delegation, completion,
// searches, faults and approvals all arrive here already.
const sfxSource = new EventSource('/events');
sfxSource.onmessage = ev => {
  let e; try { e = JSON.parse(ev.data); } catch (err) { return; }
  const sting = EVENT_STING[e.kind];
  if (sting) queueSting(sting);
};

function decide(yes) {
  fetch('/approve', {method:'POST', headers:{'Content-Type':'application/json'},
                     body: JSON.stringify({approved: yes})})
    .then(() => { gate.style.display = 'none'; });
}
</script>
</body>
</html>
"""


LIVE_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>JARVIS — live</title>
<style>
  :root { --bg:#05070a; --panel:rgba(9,14,20,.86); --line:#16202b; --text:#cfe3f2;
          --dim:#6b8497; --accent:#39d0ff; --ok:#4ade80; --warn:#ffb454; --bad:#ff6b6b; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text); overflow:hidden;
               font:15px/1.6 ui-monospace, Menlo, monospace; }

  /* The face IS the interface: full bleed, everything else floats over it. */
  #face { position:fixed; inset:0; width:100%; height:100%; border:0; background:#000; z-index:0; }

  #bar { position:fixed; left:0; right:0; bottom:0; z-index:3;
         display:flex; align-items:center; gap:10px; padding:12px 14px calc(12px + env(safe-area-inset-bottom));
         background:linear-gradient(to top, rgba(3,5,8,.92), rgba(3,5,8,0)); }
  #typeBox { flex:1; min-width:0; border-radius:22px; padding:12px 16px; color:var(--text);
             background:var(--panel); border:1px solid var(--line); font:inherit; }
  #typeBox:focus { outline:none; border-color:var(--accent); }
  button { background:var(--panel); color:var(--text); border:1px solid var(--line);
           border-radius:10px; padding:10px 12px; font:inherit; cursor:pointer; }
  button:hover { border-color:var(--accent); }

  /* Attaching: the button, the little menu it opens, and the pending photo. */
  #attachBtn { width:44px; height:44px; flex:0 0 auto; padding:0; border-radius:50%;
               font-size:22px; line-height:1; color:var(--dim); }
  #attachBtn.on { border-color:var(--accent); color:var(--accent); }
  #attachMenu { display:none; position:fixed; z-index:6; padding:6px; border-radius:12px;
                background:var(--panel); border:1px solid var(--line); }
  #attachMenu.open { display:block; }
  #attachMenu button { display:block; width:100%; text-align:left; border:0; background:none;
                       padding:10px 16px; border-radius:8px; white-space:nowrap; }
  #attachMenu button:hover { background:rgba(57,208,255,.10); color:var(--accent); }

  /* The photo waiting to be sent. Sits above the bar, tap it to identify. */
  #pending { display:none; position:fixed; left:14px; right:14px; z-index:5; cursor:pointer;
             bottom:calc(84px + env(safe-area-inset-bottom));
             padding:8px; border-radius:12px; background:var(--panel);
             border:1px solid var(--line); align-items:center; gap:10px; }
  #pending.open { display:flex; }
  #pending:hover { border-color:var(--accent); }
  #pending img { width:44px; height:44px; border-radius:8px; object-fit:cover; flex:0 0 auto; }
  #pendingLabel { flex:1; min-width:0; font-size:12px; color:var(--dim); }
  #pendingX { flex:0 0 auto; padding:6px 11px; }

  /* Send appears only when there is something to send — a transcript that just
     landed, or something you typed. Hidden otherwise, so the bar stays short. */
  #sendBtn { display:none; width:44px; height:44px; flex:0 0 auto; padding:0;
             border-radius:50%; font-size:18px; line-height:1;
             color:var(--accent); border-color:var(--accent); }

  #liveBtn { width:56px; height:56px; border-radius:50%; flex:0 0 auto; padding:0;
             display:flex; align-items:center; justify-content:center; gap:3px;
             border:2px solid var(--line); transition:border-color .2s, box-shadow .2s; }
  #liveBtn .bar { width:4px; height:8px; border-radius:2px; background:var(--dim);
                  transition:height .09s linear, background .2s; }
  #liveBtn.on { border-color:var(--accent); box-shadow:0 0 18px rgba(57,208,255,.4); }
  #liveBtn.on .bar { background:var(--accent); }
  /* Tap and hold share one button, so the colour carries the difference: blue is
     a single question, green is an open session. No label to read, no room taken. */
  #liveBtn.session { border-color:var(--ok); box-shadow:0 0 18px rgba(74,222,128,.4); }
  #liveBtn.session .bar { background:var(--ok); }
  #liveBtn.speaking { border-color:var(--ok); box-shadow:0 0 18px rgba(74,222,128,.4); }
  #liveBtn.speaking .bar { background:var(--ok); }
  #liveBtn.thinking { border-color:var(--warn); }
  #liveBtn.thinking .bar { background:var(--warn); animation:pulse 1s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { height:8px; } 50% { height:22px; } }

  #stateChip { position:fixed; top:calc(10px + env(safe-area-inset-top)); left:50%; transform:translateX(-50%);
               z-index:3; padding:6px 14px; border-radius:16px; background:var(--panel);
               border:1px solid var(--line); color:var(--dim); font-size:12px; letter-spacing:.12em;
               text-transform:uppercase; pointer-events:none; }

  /* The sheet: closed by default. You should watch the face, not the transcript. */
  #sheet { position:fixed; left:0; right:0; bottom:0; z-index:4; height:72vh;
           background:var(--panel); backdrop-filter:blur(14px);
           border-top:1px solid var(--line); border-radius:16px 16px 0 0;
           transform:translateY(100%); transition:transform .26s ease;
           display:flex; flex-direction:column; }
  #sheet.open { transform:none; }
  #handle { padding:10px 0 6px; display:flex; justify-content:center; cursor:pointer; flex:0 0 auto; }
  #handle span { width:44px; height:4px; border-radius:2px; background:var(--line); }
  #controls { display:flex; gap:8px; padding:0 14px 8px; flex-wrap:wrap; flex:0 0 auto; }
  #controls select { color:var(--text); background:#0e1620; border:1px solid var(--line);
                     border-radius:8px; padding:7px 10px; font:inherit; max-width:45%; }
  #incognitoLbl { display:flex; align-items:center; gap:6px; color:var(--dim); font-size:12px;
                  padding:7px 10px; border:1px solid var(--line); border-radius:8px; cursor:pointer; }
  #incognitoLbl.on { color:var(--ok); border-color:var(--ok); }
  #micHint { padding:0 16px 8px; color:var(--dim); font-size:11px; flex:0 0 auto; }
  #log { flex:1; overflow-y:auto; padding:4px 16px 16px; }
  .m { margin-bottom:10px; }
  .m .who { color:var(--dim); font-size:11px; letter-spacing:.1em; }
  .m.you .who { color:var(--warn); } .m.jarvis .who { color:var(--accent); }
  .m.system { color:var(--dim); font-style:italic; }

  #approval { display:none; margin:0 16px 14px; padding:12px; border:1px solid var(--warn);
              border-radius:10px; background:rgba(255,180,84,.07); flex:0 0 auto; }
  #approval h3 { margin:0 0 8px; font-size:12px; letter-spacing:.14em; color:var(--warn);
                 text-transform:uppercase; }
  #planText { white-space:pre-wrap; max-height:24vh; overflow-y:auto; margin:0 0 10px;
              font:13px/1.5 ui-monospace, Menlo, monospace; }
  #approval .row { display:flex; gap:8px; }
  #approval .row button { flex:1; }
  #approval .yes { border-color:var(--ok); color:var(--ok); }
  #approval .no { border-color:var(--bad); color:var(--bad); }
</style>
</head>
<body>
<iframe id="face" allow="autoplay; microphone"></iframe>
<div id="stateChip">idle</div>
<a id="teamLink" href="/team/" title="What the dev team is working on — plans, items, and the option buttons" style="position:fixed;top:calc(58px + env(safe-area-inset-top));right:12px;z-index:60;font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.08em;color:var(--accent);text-decoration:none;border:1px solid var(--accent);border-radius:8px;padding:7px 11px;background:rgba(11,16,22,.92);box-shadow:0 0 14px rgba(57,208,255,.22);text-transform:uppercase">◆ team</a>

<div id="sheet">
  <div id="handle" title="Close"><span></span></div>
  <div id="controls">
    <select id="facePicker" title="Visualization"></select>
    <select id="voicePicker" title="Voice"></select>
    <select id="modelPicker" title="Which model answers"></select>
    <label id="incognitoLbl" title="Keep nothing: no memory, no transcript on disk">
      <input type="checkbox" id="incognito"> incognito
    </label>
  </div>
  <!-- The mic carries two gestures and a gesture has no affordance of its own,
       so it is written down once, where the other controls are explained. -->
  <div id="micHint">Mic: tap for one question · hold for a live session</div>
  <div id="approval">
    <h3>Plan awaiting your approval</h3>
    <div id="planText"></div>
    <div class="row">
      <button class="yes" id="approveBtn">Approve</button>
      <button class="no" id="rejectBtn">Reject</button>
    </div>
  </div>
  <div id="log"></div>
</div>

<div id="pending">
  <img id="pendingThumb" alt="">
  <span id="pendingLabel">Tap to identify this — or type a question and press enter</span>
  <button id="pendingX" title="Remove">×</button>
</div>

<div id="bar">
  <button id="attachBtn" title="Attach an image">+</button>
  <input id="typeBox" placeholder="Ask Jarvis…" autocomplete="off">
  <button id="sendBtn" title="Send">▸</button>
  <button id="liveBtn" title="Tap for one question · hold for a live session">
    <span class="bar"></span><span class="bar"></span><span class="bar"></span>
  </button>
  <button id="chatBtn" title="Show the conversation">⌃</button>
</div>

<div id="attachMenu">
  <button data-mode="camera">Take photo</button>
  <button data-mode="library">Choose image</button>
</div>
<!-- Hidden OFF-SCREEN, not display:none. Desktop Chrome will open a chooser for a
     display:none input; Android WebView will not — it treats the element as
     unrendered and the .click() does nothing at all, which reads as a dead
     button. Positioned out of view keeps it rendered and still unreachable. -->
<input type="file" id="filePicker" accept="image/*" style="position:fixed;left:-9999px;top:0;width:1px;height:1px;opacity:0">


<script>
const SR = 16000;
// Quiet long enough to end a turn. Was 2000/3200, which after the Kokoro move was
// the single largest cost in a turn — well over the 400 ms STT and 500 ms TTS it
// was waiting on. Shortened now that a truncated transcript is REVIEWABLE before
// it is sent (tap mode), so an early cut costs a glance, not a wrong answer.
const SILENCE_MS = 1100;          // end a turn after this much quiet
const SILENCE_LONG_MS = 2000;     // ...more, if they have barely started talking
const MIN_SPEECH_MS = 300;
const MAX_UTTERANCE_MS = 40000;
const MIC_GUARD_MS = 900;         // let the speaker (and its tail) die
const HOLD_MS = 400;              // past this, the press is a session, not a question

let ctx, stream, node, analyser, running = false, speaking = false;
let mode = 'tap';                 // 'tap' = one question, 'async' = live session
let want = false;                 // does the human still want the mic open?
let arming = false;               // a start() is in flight, waiting on the device
let micGuardUntil = 0;
let lastSpoken = '';              // for echo rejection, see looksLikeEcho()
let lastLevelLog = 0;             // keeps the mic-level trace readable
let speakingSince = 0;
let chirpedThisTurn = false;     // one sting per turn, not one per sentence

/** Every client fetch gets a deadline.
 *
 * Without one, a hung TTS render means speakReply() never resolves, `speaking`
 * stays true forever, and because the mic is deliberately ignored while
 * speaking (half-duplex) Jarvis goes permanently DEAF — the "freeze where he
 * stops answering" that needed a reload to clear. A deadline turns that into
 * an error the UI can report. */
function fetchWithTimeout(url, opts, ms) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), ms);
  return fetch(url, Object.assign({}, opts || {}, {signal: ctl.signal}))
    .finally(() => clearTimeout(timer));
}

// Belt to the deadline's braces: however speaking got stuck, it cannot stay
// stuck — a deaf assistant is worse than a dropped turn.
setInterval(() => {
  if (speaking && Date.now() - speakingSince > 180000) {
    console.log('jarvis: watchdog cleared a stuck speaking state');
    speaking = false;
    micGuardUntil = Date.now() + MIC_GUARD_MS;
    setState('idle');
  }
}, 5000);

/** Render one piece of text to audio. 503 = Higgs's queue is full: back off. */
async function renderSpeech(text) {
  // Back off on ANY server-side failure, not just 503: Higgs answers 502 when it is
  // full, so `if (r.status !== 503) throw` threw on precisely the case the retry was
  // written for.
  for (let attempt = 0; attempt < 4; attempt++) {
    const r = await fetchWithTimeout('/api/speak', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({input: text, voice: VOICE})}, 240000);
    if (r.ok) return r.blob();
    if (r.status < 500 && r.status !== 429) throw new Error('speak ' + r.status);
    await new Promise(res => setTimeout(res, 500 * (attempt + 1)));
  }
  return null;      // this sentence gave up; the reply continues
}

/** Play one rendered clip; resolves when it ends (or fails). */
function playBlob(blob, sentence) {
  const url = URL.createObjectURL(blob);
  return new Promise((resolve) => {
    const audio = new Audio(url);
    if (sentence) {
      audio.addEventListener('loadedmetadata', () => {
        if (isFinite(audio.duration) && audio.duration > 0.4) {
          scheduleAccents(audio, keyWordTimes(sentence, audio.duration));
        }
      });
    }
    audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
    audio.onerror = () => { URL.revokeObjectURL(url); resolve(); };
    audio.play().catch(() => resolve());
  });
}

/** Speaks text AS IT ARRIVES.
 *
 * The old path waited for the whole reply, then rendered, then played — so a
 * long answer was seconds of silence followed by a wall of speech. Here each
 * completed sentence is rendered and queued the moment the model finishes it,
 * so he starts talking while the rest is still being written. Rendering runs
 * ahead of playback, but playback is strictly sequential: overlapping speech
 * would be gibberish. */
class Speaker {
  constructor() {
    this.buf = ''; this.queue = []; this.busy = false; this.failed = false;
    this.spoke = false;      // has this turn produced a sentence yet?
  }
  push(piece) {
    this.buf += piece;
    // Break at a real sentence end only (terminator + space + capital), or at
    // the end of a completed line — the same rule the chunker uses.
    let m;
    while ((m = this.buf.match(/^([\s\S]*?[.!?]+)\s+(?=["'(\[]?[A-Z0-9])/))) {
      const sentence = m[1].trim();
      this.buf = this.buf.slice(m[0].length);
      if (sentence) this.enqueue(sentence);
    }
  }
  enqueue(s) {
    // The first sentence of a turn already gets the ack; a cue there would be
    // two sounds in the same instant, so it waits for the next one.
    this.queue.push({text: s, cue: this.spoke ? detectCue(s) : null, ack: !this.spoke});
    this.spoke = true;
    if (!this.busy && !this.failed) this.pump();
  }
  async pump() {
    if (this.busy || this.failed) return;
    this.busy = true;
    let misses = 0;                       // consecutive, not lifetime
    while (this.queue.length) {
      const item = this.queue.shift();
      const s = item.text;
      try {
        // One sound per sentence boundary, never two: a cue if the sentence
        // has one, otherwise a pending event sting.
        if (item.cue) playSting(item.cue);   // the sentence's own cue wins
        else flushSting();                   // otherwise a pending event sting
        beginSpeaking();
        setState('speaking');
        const blob = await renderSpeech(s);
        if (!blob) continue;
        await playBlob(blob, s);
        misses = 0;
      } catch (err) {
        // A failed sentence is a gap, not a reason to abandon the rest. The old
        // `this.failed = true; break` is what made him go silent mid-reply.
        console.log('jarvis: speak failed ' + err);
        if (++misses >= 3) { this.failed = true; break; }
        continue;
      }
    }
    this.busy = false;
  }
  /** Flush whatever is left and wait for the queue to drain. */
  async finish() {
    const rest = this.buf.trim();
    this.buf = '';
    if (rest) this.enqueue(rest);
    while (this.busy || this.queue.length) {
      await new Promise(r => setTimeout(r, 50));
    }
  }
}

// ---- sonic vocabulary -----------------------------------------------------
// Sounds that carry STATE, not content. They are queued when an event arrives
// and fired at the next SENTENCE BOUNDARY — landing a sting mid-word is the
// difference between a language and a nuisance.
const STINGS = {};          // name -> {url, gain}
let pendingStings = [];

/** Which conversational event maps to which sting. Mirrors the vocabulary the
 *  panel serves at /api/sfx. */
const EVENT_STING = {
  job_start: 'rise', coder_start: 'rise',      // something significant is starting
  job_done: 'done', coder_done: 'done',        // the snap: it came back
  web_start: 'search', web_done: 'found',
  plan: 'impact',                              // weight: this one needs YOU
  job_failed: 'fault', media_error: 'fault',
  media: 'present',
  voice: 'shift',
};

// Cues fire DURING speech, matched to the SHAPE of a sentence rather than to an
// event: agreement, refusal, a reveal, a hedge, a warning, a conclusion. The
// words carry meaning; these carry tone of voice. Order matters — a warning
// beats a hedge, and a reveal beats agreement.
const CUES = [
  ['cue_weight',  /\b(importantly|critical|crucial|be careful|caution|warning|urgent|dangerous|must not|do not touch)\b/i],
  ['cue_no',      /\b(cannot|can't|won't|unable|impossible|refus\w*|denied|no longer|afraid not)\b/i],
  ['cue_reveal',  /\b(I (?:have )?found|I've found|here (?:it|they) (?:is|are)|I (?:have )?located|it (?:appears|seems) that|turns out|I have it)\b/i],
  ['cue_unsure',  /\b(perhaps|maybe|possibly|uncertain|unclear|not sure|not certain|might be|could be)\b/i],
  ['cue_resolve', /\b(in short|in summary|to summari[sz]e|therefore|which means|all told|on balance)\b/i],
  ['cue_yes',     /\b(certainly|of course|indeed|absolutely|agreed|quite right|exactly so)\b/i],
];

function detectCue(sentence) {
  for (const [name, re] of CUES) if (re.test(sentence)) return name;
  return null;
}

function queueSting(name) {
  const s = STINGS[name];
  if (!s || pendingStings.length >= 2) return;   // never stack up a queue of noise
  pendingStings.push(name);
}

// The bed is not a sting: it is the room he is in. It loops, very quietly,
// for as long as he is working — the reference clip's sustained electronic
// texture rather than another punctuation mark.
// One continuous floor under the whole conversation, not just under the
// thinking: he should sound like he exists between sentences too. The work
// lift raises it while he is actually busy rather than starting and stopping.
const FLOOR_GAIN = 0.05;      // present, never noticed
let bedAudio = null;         // the floor's audio element, if it is up
function bed(working) {
  const target = working ? (STINGS.bed ? STINGS.bed.gain : 0.10) : FLOOR_GAIN;
  if (!STINGS.bed) return;
  if (!bedAudio) {
    try {
      bedAudio = new Audio(STINGS.bed.url);
      bedAudio.loop = true;
      bedAudio.volume = FLOOR_GAIN;
      bedAudio.play().catch(() => { bedAudio = null; });
    } catch (err) { bedAudio = null; return; }
  }
  // glide to the new level so the lift is felt rather than heard as a step
  const from = bedAudio.volume, steps = 12;
  let k = 0;
  const glide = setInterval(() => {
    k += 1;
    if (!bedAudio) { clearInterval(glide); return; }
    bedAudio.volume = from + (target - from) * (k / steps);
    if (k >= steps) clearInterval(glide);
  }, 60);
}

/** The floor starts when the mic arms and stops when it stops — it belongs to
 *  the conversation, not to the page. */
function floorFollow() {
  if (running) bed(false);
  else if (bedAudio) { try { bedAudio.pause(); } catch (err) {} bedAudio = null; }
}

// ---- emphasis ------------------------------------------------------------
// Higgs returns no word timings, so a key word's moment is ESTIMATED by
// character position within the sentence and scheduled against the audio's
// real duration. That is approximate by nature — good enough to land an accent
// near the word, not good enough to promise it on the syllable.
const EMPHASIS_MARKER = /^(especially|particularly|notably|crucially|precisely|exactly|above all|most importantly)$/i;

function keyWordTimes(sentence, duration) {
  const words = sentence.split(/\s+/).filter(Boolean);
  const total = words.reduce((n, w) => n + w.length + 1, 0) || 1;
  const times = [];
  let acc = 0;
  for (let i = 0; i < words.length; i++) {
    const bare = words[i].replace(/[^\w'\-]/g, '');
    const isNumber = /^\d[\d,.%]*$/.test(bare);
    const marked = i > 0 && EMPHASIS_MARKER.test(words[i - 1]);
    // A number is worth accenting however short it is ("42"), but a marked
    // word needs body — "especially a" should not tick.
    if (isNumber || (marked && bare.length > 2)) times.push((acc / total) * duration);
    acc += words[i].length + 1;
  }
  return times.slice(0, 3);          // at most three accents in one sentence
}

/** Fire accents under the words as the clip plays. */
function scheduleAccents(audio, times) {
  times.forEach(offset => {
    setTimeout(() => {
      if (audio.paused || audio.ended) return;
      playSting('accent');
    }, Math.max(0, offset * 1000));
  });
}

/** Called as each sentence starts — stings land in the gaps, not over words. */
function playSting(name) {
  const s = STINGS[name];
  if (!s) return;
  try {
    const a = new Audio(s.url);
    a.volume = s.gain;
    a.play().catch(() => {});
  } catch (err) { /* never worth an error */ }
}

function flushSting() {
  const name = pendingStings.shift();
  if (!name || !STINGS[name]) return;
  try {
    const a = new Audio(STINGS[name].url);
    a.volume = STINGS[name].gain;
    a.play().catch(() => {});
  } catch (err) { /* a sting is never worth an error */ }
}

fetch('/api/sfx').then(r => r.json()).then(cat => {
  Object.keys(cat).forEach(name => {
    fetch('/api/sfx/' + name)
      .then(r => r.ok ? r.blob() : null)
      .then(b => { if (b) STINGS[name] = {url: URL.createObjectURL(b), gain: cat[name].gain || 0.2}; })
      .catch(() => {});
  });
}).catch(() => {});

let chirpBlob = null;
fetch('/api/chirp').then(r => r.ok ? r.blob() : null).then(b => { chirpBlob = b; }).catch(() => {});

/** The little UI sting that says "the machine is talking now". Quiet, and
 *  never allowed to delay or break speech — if it fails, he just talks. */
function chirp() {
  if (!chirpBlob) return;
  try {
    const a = new Audio(URL.createObjectURL(chirpBlob));
    a.volume = 0.25;
    a.play().catch(() => {});
  } catch (err) { /* a sting is never worth an error */ }
}

function beginSpeaking() {
  speaking = true;
  speakingSince = Date.now();
  if (!chirpedThisTurn) { chirpedThisTurn = true; chirp(); }
}
let VOICE = localStorage.getItem('jarvis.voice') || '';
let FACE = localStorage.getItem('jarvis.face') || '';
let buf = [], speechMs = 0, silentMs = 0, bufSpeechMs = 0, recording = false, noiseFloor = 0.004;
let history = [];
let sheetOpen = false, pendingPlan = null;

const $ = id => document.getElementById(id);
const esc = s => (s || '').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

// Persistent by default; incognito is the exception. When on, the brain is
// told to write nothing — the transcript still lives in this page's memory.
let MODEL = localStorage.getItem('jarvis.model') || 'jarvis';
let INCOGNITO = localStorage.getItem('jarvis.incognito') === '1';

function setState(s) {
  const label = (s === 'idle' && running) ? 'your turn — take your time' : s;
  $('stateChip').textContent = label;
  $('stateChip').style.borderColor = (s === 'speaking' ? 'var(--ok)' :
                                      s === 'listening…' ? 'var(--warn)' : 'var(--line)');
  bed(s.startsWith('think') || s.startsWith('transcri') || s === 'listening…');
  const btn = $('liveBtn');
  btn.classList.toggle('speaking', s === 'speaking');
  btn.classList.toggle('thinking', s.startsWith('think') || s.startsWith('transcri'));
  fetch('/viz/state', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({state: s.startsWith('listen') ? 'listening'
                               : (s.startsWith('think') || s.startsWith('transcri')) ? 'thinking'
                               : s === 'speaking' ? 'speaking' : 'idle'})}).catch(()=>{});
}
function say(who, text) {
  const d = document.createElement('div');
  d.className = 'm ' + who;
  d.innerHTML = '<div class="who">' + who.toUpperCase() + '</div><div>' + esc(text) + '</div>';
  $('log').appendChild(d);
  $('log').scrollTop = $('log').scrollHeight;
}
function openSheet(open) {
  sheetOpen = (open === undefined) ? !sheetOpen : open;
  $('sheet').classList.toggle('open', sheetOpen);
  $('chatBtn').textContent = sheetOpen ? '⌄' : '⌃';
}
$('handle').onclick = () => openSheet(false);
$('chatBtn').onclick = () => openSheet();

/* ---- live audio ------------------------------------------------------- */
async function start(which) {
  mode = (which === 'async') ? 'async' : 'tap';
  want = true;
  // Deliberately NOT asking for echoCancellation/noiseSuppression: on Android
  // those select the VOICE_COMMUNICATION source, whose AEC can cancel the mic
  // outright — the capture reads digital silence (measured rms 0.0000) while
  // every other indicator says the microphone is live. Echo is handled in the
  // app instead, by half-duplex playback and looksLikeEcho().
  stream = await navigator.mediaDevices.getUserMedia({audio: {
    echoCancellation: false, noiseSuppression: false, autoGainControl: true}});
  if (!want) {
    // They cancelled while the device was still opening. Hand it straight back
    // rather than coming up armed a moment after they asked for it closed.
    try { stream.getTracks().forEach(t => t.stop()); } catch (err) {}
    stream = null;
    return;
  }
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  analyser = ctx.createAnalyser(); analyser.fftSize = 2048; src.connect(analyser);
  node = ctx.createScriptProcessor(4096, 1, 1);
  node.onaudioprocess = onAudio;
  // Sink the processor through a SILENT gain node rather than ctx.destination:
  // routing a mic source to the speakers makes the phone play your own voice
  // back at you (and howl), which also makes the level detector useless.
  const sink = ctx.createGain();
  sink.gain.value = 0;
  src.connect(node); node.connect(sink); sink.connect(ctx.destination);
  running = true;
  console.log('jarvis: mic open, mode=' + mode + ' rate=' + ctx.sampleRate
              + ' state=' + ctx.state);
  $('liveBtn').classList.add('on');
  $('liveBtn').classList.toggle('session', mode === 'async');
  setState('idle');
}

/** Close the mic. Shared by the button and finishUtterance().
 *
 * In tap mode an arm buys exactly ONE utterance, so this fires as soon as they
 * stop talking and there is no window where he is still capturing a room he is
 * no longer being spoken to in — which is how ambient noise got transcribed as a
 * turn in the first place. A session calls this only when you end it.
 */
function stop() {
  want = false;                    // authoritative: a start() in flight will bail
  running = false; recording = false;
  if (stream) stream.getTracks().forEach(t => t.stop());
  if (ctx) ctx.close();
  stream = null; ctx = null;
  $('liveBtn').classList.remove('on', 'speaking', 'thinking', 'session');
  floorFollow();
  setState('idle');
}

function onAudio(e) {
  if (!running) return;
  const block = e.inputBuffer.getChannelData(0);
  let sum = 0; for (let i = 0; i < block.length; i++) sum += block[i] * block[i];
  const rms = Math.sqrt(sum / block.length);
  // Audible-level trace every ~2 s, so a silent mic is obvious in logcat.
  if (Date.now() - lastLevelLog > 2000) {
    lastLevelLog = Date.now();
    console.log('jarvis: rms=' + rms.toFixed(4) + ' floor=' + noiseFloor.toFixed(4)
                + ' rec=' + recording + ' speaking=' + speaking);
  }
  const bars = document.querySelectorAll('#liveBtn .bar');
  if (bars.length === 3 && !$('liveBtn').classList.contains('thinking')) {
    const level = Math.min(1, rms * 9);
    bars[0].style.height = (7 + level * 12) + 'px';
    bars[1].style.height = (9 + level * 22) + 'px';
    bars[2].style.height = (7 + level * 12) + 'px';
  }
  // Half-duplex: while he speaks we do not listen, or he hears himself and answers.
  if (speaking || Date.now() < micGuardUntil) return;
  const blockMs = (block.length / ctx.sampleRate) * 1000;
  const hangoverMs = (bufSpeechMs < 1200 ? SILENCE_LONG_MS : SILENCE_MS);

  if (!recording) {
    noiseFloor = 0.995 * noiseFloor + 0.005 * rms;
    const threshold = Math.max(0.012, noiseFloor * 3.5);
    if (rms > threshold) {
      speechMs += blockMs;
      if (speechMs > MIN_SPEECH_MS) {
        recording = true; buf = []; silentMs = 0; bufSpeechMs = 0;
        setState('listening');
      }
    } else { speechMs = Math.max(0, speechMs - blockMs); }
    if (recording) buf.push(new Float32Array(block));
  } else {
    buf.push(new Float32Array(block));
    const threshold = Math.max(0.012, noiseFloor * 3.5);
    if (rms < threshold) { silentMs += blockMs; } else { silentMs = 0; bufSpeechMs += blockMs; }
    const totalMs = buf.reduce((n, b) => n + b.length, 0) / ctx.sampleRate * 1000;
    if (silentMs > hangoverMs || totalMs > MAX_UTTERANCE_MS) finishUtterance();
  }
}

async function finishUtterance() {
  // Read the rate BEFORE stop(): it nulls ctx, and the resample below needs it.
  const rate = ctx ? ctx.sampleRate : SR;
  const oneShot = (mode === 'tap');
  recording = false; speechMs = 0; silentMs = 0; bufSpeechMs = 0;
  const total = buf.reduce((n, b) => n + b.length, 0);
  const flat = new Float32Array(total);
  let o = 0; for (const b of buf) { flat.set(b, o); o += b.length; }
  buf = [];
  // A single question ends here, so the mic goes off the instant they stop and
  // everything below runs without it. A session is still waiting for the next
  // turn and leaves the microphone where it is.
  if (oneShot) stop();
  if (total / rate < 0.3) { setState('idle'); return; }
  const wav = encodeWAV(resample(flat, rate, SR), SR);
  setState('transcribing…');
  const fd = new FormData();
  fd.append('file', wav, 'utterance.wav');
  fd.append('model', 'whisper-1');
  console.log('jarvis: utterance ' + (total / rate).toFixed(1) + 's -> transcribing');
  let text = '';
  try {
    const r = await fetchWithTimeout('/api/transcribe', {method:'POST', body: fd}, 60000);
    text = (await r.json()).text?.trim() || '';
  } catch (err) { console.log('jarvis: transcribe failed ' + err); setState('idle'); return; }
  console.log('jarvis: transcript=' + JSON.stringify(text));
  if (!text) {
    // Say so rather than going quiet: an empty transcript looks identical to
    // "Jarvis is broken", and the usual cause is mumbling or a clipped start.
    say('system', "I didn't catch that, sir — try again?");
    setState('idle');
    return;
  }
  // He heard himself. On a phone the speaker sits next to the mic and echo
  // cancellation is not reliable, so the tail of his own reply gets transcribed
  // and answered — which is a loop with no end. Compare against what he just
  // said before treating it as the human's turn.
  if (looksLikeEcho(text)) {
    setState('idle');
    return;
  }
  if (oneShot) {
    // Offer it; do not send it. Nothing reaches the brain that the human has not
    // seen — STT mishears, and it mishears more in a noisy room, so a wrong word
    // used to become a wrong answer with no chance to catch it.
    offerTranscript(text);
  } else {
    // A session is hands-free by definition: the point of it is never touching
    // the screen between turns, so a session sends what it hears.
    submit(text);
  }
}

/** Park a transcript in the text box and wait for Send.
 *
 * The box is the existing one, so the transcript is editable in place — fixing
 * one word costs a tap, not a re-record — and Enter still sends, so the
 * keyboard path and the button path end in the same submit().
 */
function offerTranscript(text) {
  const box = $('typeBox');
  box.value = text;
  $('sendBtn').style.display = 'block';
  setState('idle');
  try { box.focus(); } catch (err) {}
}

/** Send a turn. The one path for typed, corrected and spoken input. */
function submit(text) {
  const t = (text || '').trim();
  if (!t) return;
  // Clear the box only if it still holds what we are sending — a session must
  // not wipe something you typed while it was listening.
  const box = $('typeBox');
  if (box.value.trim() === t) box.value = '';
  $('sendBtn').style.display = box.value.trim() ? 'block' : 'none';
  say('you', t);
  queueSting('heard');       // "I heard you" — before the thinking beat
  ask(t);
}

/** True when a transcript is mostly words Jarvis just spoke. */
function looksLikeEcho(text) {
  if (!lastSpoken) return false;
  const words = s => (s || '').toLowerCase().replace(/[^a-z0-9\s]/g, ' ')
                            .split(/\s+/).filter(w => w.length > 3);
  const heard = words(text);
  if (!heard.length) return false;
  const said = new Set(words(lastSpoken));
  let hits = 0;
  for (const w of new Set(heard)) if (said.has(w)) hits++;
  return hits / new Set(heard).size >= 0.5;
}

/* ---- one path for typed and spoken turns ------------------------------ */
async function ask(text, vision) {
  chirpedThisTurn = false;
  setState('thinking…');
  history.push({role:'user', content:text});
  let reply = '';
  const speaker = new Speaker();
  let voiceChanged = null, media = null;

  try {
    const r = await fetchWithTimeout('/api/chat', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({messages: history, model: MODEL,
                            ephemeral: INCOGNITO, stream: true,
                            ...(vision ? {vision: vision} : {})})}, 120000);
    if (!r.ok) throw new Error('chat ' + r.status);

    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (!payload || payload === '[DONE]') continue;
        let d;
        try { d = JSON.parse(payload); } catch (err) { continue; }
        if (d.media) { media = d.media; continue; }
        if (d.voice) { voiceChanged = d.voice; continue; }
        const piece = d.choices && d.choices[0] && d.choices[0].delta
                      ? (d.choices[0].delta.content || '') : '';
        if (!piece) continue;
        reply += piece;
        // Speak it as it lands — but only once we know it is not a directive
        // line, which the brain holds back before sending.
        speaker.push(piece);
      }
    }
  } catch (err) {
    console.log('jarvis: chat failed ' + err);
    say('system', 'No answer came back — try that again.');
    setState('idle');
    return;
  }

  reply = reply.trim();
  console.log('jarvis: reply chars=' + reply.length);
  if (!reply) { setState('idle'); return; }
  say('jarvis', reply);
  history.push({role:'assistant', content:reply});
  if (history.length > 24) history = history.slice(-24);
  lastSpoken = reply;

  if (voiceChanged && voiceChanged !== VOICE) {
    VOICE = voiceChanged;
    localStorage.setItem('jarvis.voice', VOICE);
    applyVoice(VOICE);
  }
  if (media) {
    // He was asked to play something: play that, and stop the narration that
    // was already streaming — talking over it would be rude.
    speaker.failed = true;
    speaker.queue = [];
    setState('speaking'); beginSpeaking();
    console.log('jarvis: playing ' + media.kind + ' ' + media.name);
    await playMedia(media);
  } else {
    await speaker.finish();
    if (!speaker.failed) playSting('close');   // the bookend to `ack`
  }
  speaking = false;
  micGuardUntil = Date.now() + MIC_GUARD_MS;
  setState('idle');
}

function resample(input, inRate, outRate) {
  if (inRate === outRate) return input;
  const ratio = inRate / outRate, len = Math.floor(input.length / ratio);
  const out = new Float32Array(len);
  for (let i = 0; i < len; i++) {
    const pos = i * ratio, i0 = Math.floor(pos), i1 = Math.min(i0 + 1, input.length - 1);
    const f = pos - i0;
    out[i] = input[i0] * (1 - f) + input[i1] * f;
  }
  return out;
}
function encodeWAV(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
  const str = (off, s) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); };
  str(0,'RIFF'); v.setUint32(4, 36 + samples.length*2, true); str(8,'WAVE'); str(12,'fmt ');
  v.setUint32(16,16,true); v.setUint16(20,1,true); v.setUint16(22,1,true);
  v.setUint32(24,rate,true); v.setUint32(28,rate*2,true); v.setUint16(32,2,true);
  v.setUint16(34,16,true); str(36,'data'); v.setUint32(40,samples.length*2,true);
  for (let i = 0, o = 44; i < samples.length; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return new Blob([v], {type:'audio/wav'});
}

/* ---- pickers ---------------------------------------------------------- */
function applyVoice(v) {
  const picker = $('voicePicker');
  if (![...picker.options].some(o => o.value === v)) {
    const o = document.createElement('option');
    o.value = v; o.textContent = v + ' (by description)';
    picker.appendChild(o);
  }
  picker.value = v;
}
fetch('/api/voices').then(r => r.json()).then(cfg => {
  if (cfg.error) return;
  const picker = $('voicePicker');
  (cfg.featured || []).forEach(v => {
    const o = document.createElement('option'); o.value = v; o.textContent = v;
    picker.appendChild(o);
  });
  if (!VOICE) VOICE = cfg.default || 'jarvis';
  applyVoice(VOICE);
  picker.onchange = () => { VOICE = picker.value; localStorage.setItem('jarvis.voice', VOICE); };
}).catch(() => {});

// Which brain answers. Kunou by default; the director and the fetching lanes
// are separate models, so the routing is chosen rather than guessed.
fetch('/api/models').then(r => r.json()).then(cfg => {
  if (cfg.error) return;
  const picker = $('modelPicker');
  const label = {jarvis: 'kunou', 'jarvis-director': 'director (r1)',
                 'jarvis-web': 'web search', 'jarvis-news': 'news', 'jarvis-wiki': 'wiki'};
  (cfg.models || []).forEach(m => {
    const o = document.createElement('option');
    o.value = m; o.textContent = label[m] || m;
    picker.appendChild(o);
  });
  if (![...picker.options].some(o => o.value === MODEL)) MODEL = cfg.default || 'jarvis';
  picker.value = MODEL;
  picker.onchange = () => { MODEL = picker.value; localStorage.setItem('jarvis.model', MODEL); };
}).catch(() => {});

// Incognito: the brain writes nothing for this conversation.
const inc = $('incognito');
inc.checked = INCOGNITO;
$('incognitoLbl').classList.toggle('on', INCOGNITO);
inc.onchange = () => {
  INCOGNITO = inc.checked;
  localStorage.setItem('jarvis.incognito', INCOGNITO ? '1' : '0');
  $('incognitoLbl').classList.toggle('on', INCOGNITO);
  if (INCOGNITO) { history = []; say('system', 'Incognito — nothing from here is kept.'); }
};

fetch('/api/faces').then(r => r.json()).then(cfg => {
  if (cfg.error) return;
  const picker = $('facePicker');
  (cfg.faces || []).forEach(f => {
    const o = document.createElement('option'); o.value = f.id; o.textContent = f.title;
    picker.appendChild(o);
  });
  FACE = FACE || cfg.default || 'board';
  picker.value = FACE;
  loadFace(FACE);
  picker.onchange = () => {
    FACE = picker.value; localStorage.setItem('jarvis.face', FACE); loadFace(FACE);
  };
}).catch(() => { loadFace('board'); });

function loadFace(id) {
  const f = $('face');
  f.src = '/viz/faces/' + id + '/';
  // Tapping the face reveals the conversation; the faces do not mind a click.
  f.onload = () => {
    try {
      f.contentDocument.addEventListener('click', () => openSheet());
    } catch (err) { /* cross-document — the bar's button still works */ }
  };
}

/* ---- the architect's plans interrupt; nothing else does --------------- */
const es = new EventSource('/events');
es.onmessage = ev => {
  let e; try { e = JSON.parse(ev.data); } catch { return; }
  if (e.kind !== 'plan') return;
  pendingPlan = e.plan;
  $('planText').textContent = e.plan;
  $('approval').style.display = 'block';
  openSheet(true);
  say('system', 'A plan is waiting for your approval.');
};
// The brain's event stream is the trigger source: delegation, completion,
// searches, faults and approvals all arrive here already.
const sfxSource = new EventSource('/events');
sfxSource.onmessage = ev => {
  let e; try { e = JSON.parse(ev.data); } catch (err) { return; }
  const sting = EVENT_STING[e.kind];
  if (sting) queueSting(sting);
};

function decide(yes) {
  // No session key: the brain approves the newest pending plan, which is the
  // one this sheet is showing.
  fetch('/approve', {method:'POST', headers:{'Content-Type':'application/json'},
                     body: JSON.stringify({approved: yes})})
    .then(() => {
      $('approval').style.display = 'none';
      pendingPlan = null;
      say('system', yes ? 'Approved.' : 'Rejected.');
      openSheet(false);
    });
}
$('approveBtn').onclick = () => decide(true);
$('rejectBtn').onclick = () => decide(false);

/* ---- showing him a thing ---------------------------------------------- */
// A photo goes to the brain, which runs the visual_lookup pipeline the VISUAL2
// work verified (eyes → wiki tiers → image search) and hands back a block of
// fact about what it read. He then speaks about it in his own voice — the model
// is told what the eyes saw rather than being shown the picture, because the
// conversationalist is Kunou and Kunou has no eyes.
let pendingShot = null;          // {blob, url} — attached, not yet sent

function menuOpen(open) {
  $('attachMenu').classList.toggle('open', open);
  $('attachBtn').classList.toggle('on', open);
}

$('attachBtn').onclick = e => {
  e.stopPropagation();
  const r = $('attachBtn').getBoundingClientRect();
  const m = $('attachMenu');
  m.style.left = Math.max(8, r.left - 8) + 'px';
  m.style.bottom = (window.innerHeight - r.top + 8) + 'px';
  menuOpen(!m.classList.contains('open'));
};
document.addEventListener('click', () => menuOpen(false));

for (const b of $('attachMenu').querySelectorAll('button')) {
  b.onclick = () => {
    menuOpen(false);
    const picker = $('filePicker');
    // `capture` must be set BEFORE the click — it is the browser's only signal
    // that this is the camera rather than the gallery. On the phone the system
    // picker offers both anyway; this just chooses which one leads.
    if (b.dataset.mode === 'camera') picker.setAttribute('capture', 'environment');
    else picker.removeAttribute('capture');
    picker.click();
  };
}

function setPending(shot) {
  pendingShot = shot;
  $('pending').classList.toggle('open', !!shot);
  if (shot) $('pendingThumb').src = shot.url;
}

$('pendingX').onclick = e => {
  e.stopPropagation();
  if (pendingShot) URL.revokeObjectURL(pendingShot.url);
  setPending(null);
};
$('pending').onclick = () => sendShot();

$('filePicker').onchange = async e => {
  const file = e.target.files && e.target.files[0];
  e.target.value = '';                 // so picking the same file twice still fires
  if (!file) return;
  try {
    const shot = await shrink(file);
    if (pendingShot) URL.revokeObjectURL(pendingShot.url);
    setPending(shot);
  } catch (err) {
    say('system', 'That image could not be read (' + err + ').');
  }
};

/** Downscale before sending.
 *
 * A phone photo is several megabytes and the eyes downsample it anyway, so the
 * original is pure upload latency. 1600px on the long edge keeps small markings
 * legible, which is the entire point of the part-number case.
 */
function shrink(file, maxEdge = 1600, quality = 0.85) {
  return new Promise((resolve, reject) => {
    const src = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      try {
        const scale = Math.min(1, maxEdge / Math.max(img.width, img.height));
        const w = Math.max(1, Math.round(img.width * scale));
        const h = Math.max(1, Math.round(img.height * scale));
        const cv = document.createElement('canvas');
        cv.width = w; cv.height = h;
        cv.getContext('2d').drawImage(img, 0, 0, w, h);
        cv.toBlob(blob => {
          URL.revokeObjectURL(src);
          if (!blob) return reject(new Error('could not encode it'));
          resolve({blob, url: URL.createObjectURL(blob), w, h});
        }, 'image/jpeg', quality);
      } catch (err) { URL.revokeObjectURL(src); reject(err); }
    };
    img.onerror = () => { URL.revokeObjectURL(src); reject(new Error('not an image')); };
    img.src = src;
  });
}

/** Identify the attached photo, then let him talk about it. */
async function sendShot() {
  const shot = pendingShot;
  if (!shot) return;
  setPending(null);
  const question = $('typeBox').value.trim();
  $('typeBox').value = '';
  say('you', (question ? question + ' ' : '') + '[photo]');
  queueSting('heard');
  queueSting('search');       // "reaching outside the house" — the eyes and the wiki
  setState('thinking…');
  let view;
  try {
    const r = await fetchWithTimeout('/api/vision', {method:'POST',
      headers:{'Content-Type':'image/jpeg'}, body: shot.blob}, 180000);
    view = await r.json();
    if (!r.ok || view.error) throw new Error(view.error || ('vision ' + r.status));
  } catch (err) {
    URL.revokeObjectURL(shot.url);
    say('system', 'The eyes did not answer — ' + err);
    setState('idle');
    return;
  }
  URL.revokeObjectURL(shot.url);
  // Show what was read, with the encyclopedia link, so the guess can be checked
  // rather than taken on faith.
  const best = (view.candidates || [])[0];
  say('system', (view.identification ? 'Looks like: ' + view.identification
                                     : 'I could not name this one.')
                + (best ? '  ·  ' + best.title + '  ' + best.url : ''));
  ask(question || 'What is this?', view.context);
}

/* ---- controls --------------------------------------------------------- */
/* One button, two gestures.
 *
 * Tap and hold were competing for the same control, and the bar has no room for
 * a second circle without crushing the text box. A press that ends inside
 * HOLD_MS opens the mic for ONE question and then waits for Send; a press that
 * outlives it opens a session that stays armed and sends each turn as it comes.
 * Pressing at all while something is armed ends it.
 */
const micDenied = () => say('system',
  'Microphone blocked — use the text box, or allow mic access.');
let holdTimer = null, holdFired = false, pressStops = false;

function micDown() {
  holdFired = false;
  pressStops = running || arming;    // anything already armed or opening ends here
  if (pressStops) return;
  holdTimer = setTimeout(() => {
    holdTimer = null; holdFired = true; arming = true;
    // the session opens under the finger
    start('async').catch(micDenied).then(() => { arming = false; });
  }, HOLD_MS);
}

function micUp() {
  if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
  if (pressStops) { pressStops = false; stop(); return; }
  // `arming` matters: start() waits on the device, so without it a second tap
  // arrives while the first is still opening and we get two live captures.
  if (holdFired || running || arming) return;
  arming = true;
  start('tap').catch(micDenied).then(() => { arming = false; });
}

if (window.PointerEvent) {
  $('liveBtn').addEventListener('pointerdown', e => { e.preventDefault(); micDown(); });
  $('liveBtn').addEventListener('pointerup', e => { e.preventDefault(); micUp(); });
  $('liveBtn').addEventListener('pointercancel', () => {
    if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
    holdFired = false;
    if (pressStops) { pressStops = false; stop(); }
  });
} else {
  // No pointer events: fall back to the old toggle rather than a dead button.
  $('liveBtn').onclick = async () => {
    if (running) stop();
    else { try { await start('tap'); } catch (err) { micDenied(); } }
  };
}
$('sendBtn').onclick = () => submit($('typeBox').value);
$('typeBox').addEventListener('input', () => {
  // Keep the button honest about whether there is anything to send.
  $('sendBtn').style.display = $('typeBox').value.trim() ? 'block' : 'none';
});
$('typeBox').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  // An attached photo goes with whatever is in the box — including nothing.
  if (pendingShot) { sendShot(); return; }
  submit(e.target.value);
});
setState('idle');
</script>
</body>
</html>
"""


# no-store on the HTML: the Android shell is a WebView, and a cached page means
# the phone silently keeps running an old UI (it did — the app was showing a
# build from before several fixes). HTML is small; caching it buys nothing.
_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}


@app.get("/live", response_class=HTMLResponse)
async def live():
    return HTMLResponse(LIVE_PAGE, headers=_NO_CACHE)


@app.get("/jarvis.apk")
async def download_apk():
    """Install/update the Android shell from the phone's browser."""
    if not os.path.exists(APK_PATH):
        return JSONResponse({"error": "no apk published"}, status_code=404)
    return FileResponse(APK_PATH, media_type="application/vnd.android.package-archive",
                        filename="jarvis.apk")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(PAGE, headers=_NO_CACHE)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8093")))
