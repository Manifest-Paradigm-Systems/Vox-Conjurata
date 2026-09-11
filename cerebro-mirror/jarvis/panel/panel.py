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

import json
import os
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse

BRAIN_URL = os.getenv("JARVIS_BRAIN_URL", "http://127.0.0.1:8092")
VIZ_URL = os.getenv("JARVIS_VIZ_URL", "http://127.0.0.1:8790")
BUS_DIR = os.getenv("JARVIS_BUS_DIR", "/var/home/admin/jarvis/viz-bus")
# The Android shell is published here so a phone can install or update it
# straight from the panel — no adb, no cable. Source: ~/jarvis-android.
APK_PATH = os.getenv("JARVIS_APK_PATH", "/var/home/admin/jarvis/jarvis.apk")
VALID_STATES = {"idle", "listening", "thinking", "speaking"}

app = FastAPI(title="jarvis docks")


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


@app.post("/api/chat")
async def chat(payload: dict):
    body = dict(payload)
    body.setdefault("model", "jarvis")
    body["stream"] = False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=8.0)) as c:
            r = await c.post(f"{BRAIN_URL}/v1/chat/completions", json=body)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


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
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=8.0)) as c:
            r = await c.post(f"{TTS_URL}/v1/audio/speech", json=body)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    if r.status_code != 200:
        return JSONResponse({"error": f"tts {r.status_code}"}, status_code=502)
    return Response(content=r.content, media_type="audio/mpeg")


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


@app.get("/media/{path:path}")
async def media_proxy(path: str, request: Request):
    """Foley and music live on the Workhorse (audio stays there); the panel
    re-serves them same-origin so the browser can just play the URL."""
    # The adapter's media routes live under /media — keep the prefix.
    url = f"{TTS_URL}/media/{path}"
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


PAGE = """<!doctype html>
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
function decide(yes) {
  fetch('/approve', {method:'POST', headers:{'Content-Type':'application/json'},
                     body: JSON.stringify({approved: yes})})
    .then(() => { gate.style.display = 'none'; });
}
</script>
</body>
</html>
"""


LIVE_PAGE = """<!doctype html>
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

  #liveBtn { width:56px; height:56px; border-radius:50%; flex:0 0 auto; padding:0;
             display:flex; align-items:center; justify-content:center; gap:3px;
             border:2px solid var(--line); transition:border-color .2s, box-shadow .2s; }
  #liveBtn .bar { width:4px; height:8px; border-radius:2px; background:var(--dim);
                  transition:height .09s linear, background .2s; }
  #liveBtn.on { border-color:var(--accent); box-shadow:0 0 18px rgba(57,208,255,.4); }
  #liveBtn.on .bar { background:var(--accent); }
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

<div id="bar">
  <input id="typeBox" placeholder="Ask Jarvis…" autocomplete="off">
  <button id="liveBtn" title="Live voice — tap once to arm, again to stop">
    <span class="bar"></span><span class="bar"></span><span class="bar"></span>
  </button>
  <button id="chatBtn" title="Show the conversation">⌃</button>
</div>

<script>
const SR = 16000;
const SILENCE_MS = 2000;          // end a turn after this much quiet
const SILENCE_LONG_MS = 3200;     // ...more, if they have barely started talking
const MIN_SPEECH_MS = 300;
const MAX_UTTERANCE_MS = 40000;
const MIC_GUARD_MS = 900;         // let the speaker (and its tail) die

let ctx, stream, node, analyser, running = false, speaking = false;
let micGuardUntil = 0;
let lastSpoken = '';              // for echo rejection, see looksLikeEcho()
let lastLevelLog = 0;             // keeps the mic-level trace readable
let speakingSince = 0;

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

function beginSpeaking() {
  speaking = true;
  speakingSince = Date.now();
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
async function start() {
  // Deliberately NOT asking for echoCancellation/noiseSuppression: on Android
  // those select the VOICE_COMMUNICATION source, whose AEC can cancel the mic
  // outright — the capture reads digital silence (measured rms 0.0000) while
  // every other indicator says the microphone is live. Echo is handled in the
  // app instead, by half-duplex playback and looksLikeEcho().
  stream = await navigator.mediaDevices.getUserMedia({audio: {
    echoCancellation: false, noiseSuppression: false, autoGainControl: true}});
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
  console.log('jarvis: mic open, rate=' + ctx.sampleRate + ' state=' + ctx.state);
  $('liveBtn').classList.add('on');
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
  recording = false; speechMs = 0; silentMs = 0; bufSpeechMs = 0;
  const total = buf.reduce((n, b) => n + b.length, 0);
  const flat = new Float32Array(total);
  let o = 0; for (const b of buf) { flat.set(b, o); o += b.length; }
  buf = [];
  if (total / ctx.sampleRate < 0.3) { setState('idle'); return; }
  const wav = encodeWAV(resample(flat, ctx.sampleRate, SR), SR);
  setState('transcribing…');
  const fd = new FormData();
  fd.append('file', wav, 'utterance.wav');
  fd.append('model', 'whisper-1');
  console.log('jarvis: utterance ' + (total / ctx.sampleRate).toFixed(1) + 's -> transcribing');
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
  say('you', text);
  ask(text);
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
async function ask(text) {
  setState('thinking…');
  history.push({role:'user', content:text});
  let reply = '', voiceChanged = null;
  try {
    const r = await fetchWithTimeout('/api/chat', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({messages: history, model: MODEL, ephemeral: INCOGNITO})}, 120000);
    const d = await r.json();
    reply = d.choices?.[0]?.message?.content || '';
    if (d.voice && d.voice !== VOICE) {
      VOICE = d.voice; voiceChanged = d.voice;
      localStorage.setItem('jarvis.voice', VOICE);
      applyVoice(VOICE);
    }
    // A sound or a piece of music was asked for: play it instead of narrating
    // it. Saying the line as well would talk over what they asked to hear.
    if (d.media && d.media.url) {
      say('jarvis', reply);
      history.push({role:'assistant', content:reply});
      lastSpoken = reply;
      setState('speaking'); beginSpeaking();
      console.log('jarvis: playing ' + d.media.kind + ' ' + d.media.name);
      await playMedia(d.media);
      speaking = false;
      micGuardUntil = Date.now() + MIC_GUARD_MS;
      setState('idle');
      return;
    }
  } catch (err) {
    console.log('jarvis: chat failed ' + err);
    say('system', 'No answer came back — try that again.');
    setState('idle'); return;
  }
  console.log('jarvis: reply chars=' + reply.length);
  if (!reply) { setState('idle'); return; }
  say('jarvis', reply);
  history.push({role:'assistant', content:reply});
  if (history.length > 24) history = history.slice(-24);
  lastSpoken = reply;          // what the mic must not be allowed to answer

  setState('speaking');
  beginSpeaking();
  try {
    await speakReply(reply);
    console.log('jarvis: spoke ' + reply.length + ' chars');
  } catch (err) {
    console.log('jarvis: playback blocked ' + err);
  }
  speaking = false;
  micGuardUntil = Date.now() + MIC_GUARD_MS;
  setState('idle');
}

/** Play a foley hit or a music track the brain resolved for us. The URL is
    same-origin (/media/... proxied to the Workhorse), so it just plays. */
function playMedia(media) {
  return new Promise((resolve) => {
    const audio = new Audio(media.url);
    audio.onended = () => resolve();
    audio.onerror = () => { console.log('jarvis: media failed ' + media.url); resolve(); };
    audio.play().catch((err) => { console.log('jarvis: media blocked ' + err); resolve(); });
  });
}

/** Split into speakable chunks — sentence-sized, so the first can play early. */
function chunks(text) {
  // Break only at a real sentence end: terminator + space + capital. A plain
  // "split on periods" mangled "llama.cpp" into "llama." + "cpp…", and an
  // earlier regex attempt silently DROPPED text — hence the preserved-text
  // check when this was written.
  // Mask abbreviation periods first so "Dr. Smith" and "3 p.m. It" stay whole.
  const MASK = '\uE000';   // private-use sentinel (invisible control chars did not survive editing)
  const ABBREV = /\b(Dr|Mr|Mrs|Ms|Prof|St|vs|etc|e\.g|i\.e|p\.m|a\.m|Jr|Sr|No|Inc|Ltd)\./gi;
  const masked = (text || '').replace(ABBREV, '$1' + MASK);
  const parts = masked.replace(/\s+/g, ' ')
      .split(/(?<=[.!?])\s+(?=["'(\[]?[A-Z0-9])/);
  const out = [];
  for (const p of parts) {
    const s = p.trim();
    if (!s) continue;
    // A monster sentence is still worth splitting at a clause.
    if (s.length > 220) {
      out.push(...s.split(/[,;:]\s+/).reduce((acc, bit) => {
        if (acc.length && (acc[acc.length - 1] + ' ' + bit).length <= 220) {
          acc[acc.length - 1] += ' ' + bit;
        } else acc.push(bit);
        return acc;
      }, []));
    } else out.push(s);
  }
  // Put the masked abbreviation periods back before speaking.
  const restored = out.map(s => s.split(MASK).join('.'));
  return restored.length ? restored : [text];
}

/** Higgs renders at ~1.5x realtime, so synthesising a whole reply before
    playing it means ~14 s of silence on a two-sentence answer. Render chunk 1,
    play it, and render the rest WHILE it plays. */
async function speakReply(reply) {
  const parts = chunks(reply);
  // Higgs renders on one GPU worker behind a queue of 4 (a full queue answers
  // 503), so render ahead — but never more than the queue can hold, and always
  // PLAY in order: overlapping speech would be gibberish.
  const MAX_AHEAD = 3;
  const render = async (text) => {
    for (let attempt = 0; attempt < 3; attempt++) {
      const r = await fetchWithTimeout('/api/speak', {method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({input: text, voice: VOICE})}, 240000);
      if (r.ok) return r.blob();
      if (r.status !== 503) throw new Error('speak ' + r.status);
      await new Promise(res => setTimeout(res, 400));   // queue full: back off
    }
    return null;
  };

  // A few workers pull chunks in order; results land in their own slot, then
  // playback walks the list — so rendering overlaps and speech never does.
  const rendered = new Array(parts.length).fill(null);
  let cursor = 0;
  const worker = async () => {
    while (cursor < parts.length) {
      const i = cursor++;
      try { rendered[i] = await render(parts[i]); }
      catch (err) { console.log('jarvis: chunk ' + i + ' failed ' + err); }
    }
  };
  await Promise.all(Array.from({length: Math.min(MAX_AHEAD, parts.length)}, worker));

  for (const blob of rendered) {
    if (!blob) continue;
    const url = URL.createObjectURL(blob);
    await new Promise((resolve) => {
      const audio = new Audio(url);
      audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
      audio.onerror = () => { URL.revokeObjectURL(url); resolve(); };
      audio.play().catch(() => resolve());
    });
  }
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

/* ---- controls --------------------------------------------------------- */
$('liveBtn').onclick = async () => {
  if (running) {
    running = false;
    if (stream) stream.getTracks().forEach(t => t.stop());
    if (ctx) ctx.close();
    $('liveBtn').classList.remove('on', 'speaking', 'thinking');
    setState('idle');
  } else {
    try { await start(); }
    catch (err) { say('system', 'Microphone blocked — use the text box, or allow mic access.'); }
  }
};
$('typeBox').addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  const text = e.target.value.trim();
  if (!text) return;
  e.target.value = '';
  say('you', text);
  ask(text);
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
