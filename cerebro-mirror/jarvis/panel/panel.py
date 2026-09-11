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
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

BRAIN_URL = os.getenv("JARVIS_BRAIN_URL", "http://127.0.0.1:8092")
VIZ_URL = os.getenv("JARVIS_VIZ_URL", "http://127.0.0.1:8790")
BUS_DIR = os.getenv("JARVIS_BUS_DIR", "/var/home/admin/jarvis/viz-bus")
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=8.0)) as c:
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
        async with httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=8.0)) as c:
            r = await c.post(f"{BRAIN_URL}/v1/chat/completions", json=body)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


FEATURED_VOICES = [v.strip() for v in
                   os.getenv("JARVIS_FEATURED_VOICES", "jarvis,c3po,narrator").split(",") if v.strip()]


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
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=8.0)) as c:
            r = await c.post(f"{TTS_URL}/v1/audio/speech", json=body)
    except httpx.HTTPError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    if r.status_code != 200:
        return JSONResponse({"error": f"tts {r.status_code}"}, status_code=502)
    return Response(content=r.content, media_type="audio/mpeg")


@app.post("/api/state")
async def api_state(payload: dict):
    return await set_state(payload)


@app.get("/viz/{path:path}")
async def viz_proxy(path: str, request: Request):
    """Pass faces, /state, core.js and assets straight through — upstream stays
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
  picker.onchange = () => { document.getElementById('face').src = '/viz/faces/' + picker.value + '/'; };
  document.getElementById('face').src = '/viz/faces/' + (cfg.default || 'board') + '/';
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
<title>JARVIS — live</title>
<style>
  :root { --bg:#05070a; --line:#16202b; --text:#cfe3f2; --dim:#6b8497;
          --accent:#39d0ff; --ok:#4ade80; --warn:#ffb454; --bad:#ff6b6b; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); height:100vh; overflow:hidden;
         font:14px/1.6 ui-monospace, Menlo, monospace; display:flex; flex-direction:column; }
  header { display:flex; align-items:center; gap:14px; padding:10px 18px;
           border-bottom:1px solid var(--line); }
  h1 { font-size:13px; letter-spacing:.2em; color:var(--accent); margin:0; }
  .spacer { flex:1; }
  select, button, input { background:#0e1620; color:var(--text); border:1px solid var(--line);
           border-radius:6px; padding:8px 12px; font:inherit; }
  button { cursor:pointer; } button:hover { border-color:var(--accent); }
  a { color:var(--accent); text-decoration:none; }
  main { flex:1; display:grid; grid-template-columns:1fr 1fr; min-height:0; }
  iframe { border:0; width:100%; height:100%; background:#000; }
  #side { border-left:1px solid var(--line); display:flex; flex-direction:column; min-height:0; }
  #status { padding:12px 18px; border-bottom:1px solid var(--line); }
  #status .state { font-size:15px; letter-spacing:.14em; color:var(--dim); text-transform:uppercase; }
  #log { flex:1; overflow-y:auto; padding:12px 18px; }
  .m { margin-bottom:10px; }
  .m .who { color:var(--dim); font-size:11px; letter-spacing:.1em; }
  .m.you .who { color:var(--warn); } .m.jarvis .who { color:var(--accent); }

  /* Gemini-style control bar: type, or go live with the round waveform button */
  footer { display:flex; align-items:center; gap:12px; padding:14px 18px;
           border-top:1px solid var(--line); }
  #typeBox { flex:1; border-radius:22px; padding:12px 18px; }
  #liveBtn { width:58px; height:58px; border-radius:50%; display:flex; align-items:center;
             justify-content:center; gap:3px; padding:0; flex:0 0 auto;
             border:2px solid var(--line); background:#0e1620; transition:border-color .2s, box-shadow .2s; }
  #liveBtn .bar { width:4px; height:8px; border-radius:2px; background:var(--dim);
                  transition:height .09s linear, background .2s; }
  #liveBtn.on { border-color:var(--accent); box-shadow:0 0 18px rgba(57,208,255,.35); }
  #liveBtn.on .bar { background:var(--accent); }
  #liveBtn.speaking { border-color:var(--ok); box-shadow:0 0 18px rgba(74,222,128,.35); }
  #liveBtn.speaking .bar { background:var(--ok); }
  #liveBtn.thinking { border-color:var(--warn); }
  #liveBtn.thinking .bar { background:var(--warn); animation:pulse 1s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { height:8px; } 50% { height:22px; } }
</style>
</head>
<body>
<header>
  <h1>JARVIS LIVE</h1>
  <span id="hint" style="color:var(--dim)">type, or tap the circle and just talk</span>
  <div class="spacer"></div>
  <select id="voicePicker" title="Voice Jarvis speaks in"></select>
  <a href="/"><button>Docks ↗</button></a>
</header>
<main>
  <iframe id="face" src="/viz/faces/board/" allow="autoplay"></iframe>
  <div id="side">
    <div id="status"><div class="state" id="stateEl">idle</div></div>
    <div id="log"></div>
  </div>
</main>
<footer>
  <input id="typeBox" placeholder="Ask Jarvis…" autocomplete="off">
  <button id="liveBtn" class="circle" title="Live voice — continuous listening">
    <span class="bar"></span><span class="bar"></span><span class="bar"></span>
  </button>
</footer>
<script>
const SR = 16000;                 // whisper wants 16 kHz
const SILENCE_MS = 900;           // stop an utterance after this much quiet
const MIN_SPEECH_MS = 300;        // ignore coughs
const MAX_UTTERANCE_MS = 25000;
let ctx, stream, node, analyser, running = false, speaking = false;
// Voice is shared with the docks panel through localStorage.
let VOICE = localStorage.getItem('jarvis.voice') || '';
let buf = [], speechMs = 0, silentMs = 0, recording = false, noiseFloor = 0.004;
let history = [];
const fmt = { idle:'idle', listening:'listening…', thinking:'thinking…', transcribing:'transcribing…', speaking:'speaking' };

const $ = id => document.getElementById(id);
function setState(s) {
  $('stateEl').textContent = s;
  $('stateEl').style.color = (s === 'listening…' ? 'var(--warn)' :
                              s === 'speaking' ? 'var(--ok)' : 'var(--dim)');
  const btn = $('liveBtn');
  btn.classList.toggle('speaking', s === 'speaking');
  btn.classList.toggle('thinking', s.startsWith('think') || s.startsWith('transcri'));
  fetch('/viz/state', {method:'POST', headers:{'Content-Type':'application/json'},
                       body: JSON.stringify({state: s.startsWith('listen') ? 'listening'
                                                  : s.startsWith('think') ? 'thinking'
                                                  : s.startsWith('transcri') ? 'thinking'
                                                  : s === 'speaking' ? 'speaking' : 'idle'})}).catch(()=>{});
}
function say(who, text) {
  const d = document.createElement('div');
  d.className = 'm ' + who;
  d.innerHTML = `<div class="who">${who.toUpperCase()}</div><div>${text.replace(/[<>&]/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</div>`;
  $('log').appendChild(d); $('log').scrollTop = $('log').scrollHeight;
}

async function start() {
  stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation:true,
    noiseSuppression:true, autoGainControl:true}});
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream);
  analyser = ctx.createAnalyser(); analyser.fftSize = 2048;
  src.connect(analyser);
  node = ctx.createScriptProcessor(4096, 1, 1);
  node.onaudioprocess = onAudio;
  src.connect(node); node.connect(ctx.destination);
  running = true;
  $('liveBtn').classList.add('on');
  $('hint').textContent = 'live — just talk';
  setState('idle');
}

function onAudio(e) {
  if (!running) return;
  const block = e.inputBuffer.getChannelData(0);
  let sum = 0; for (let i = 0; i < block.length; i++) sum += block[i] * block[i];
  const rms = Math.sqrt(sum / block.length);
  // Three bars behind the circle: the middle one tracks the mic, the outer
  // pair follow at a fraction — a voice-shaped figure rather than a level bar.
  const bars = document.querySelectorAll('#liveBtn .bar');
  if (bars.length === 3 && !$('liveBtn').classList.contains('thinking')) {
    const level = Math.min(1, rms * 9);
    bars[0].style.height = (7 + level * 12) + 'px';
    bars[1].style.height = (9 + level * 22) + 'px';
    bars[2].style.height = (7 + level * 12) + 'px';
  }
  const blockMs = (block.length / ctx.sampleRate) * 1000;

  if (!recording) {
    noiseFloor = 0.995 * noiseFloor + 0.005 * rms;
    const threshold = Math.max(0.012, noiseFloor * 3.5);
    if (rms > threshold) {
      speechMs += blockMs;
      if (speechMs > MIN_SPEECH_MS) {
        if (speaking) stopPlayback();          // barge-in
        recording = true; buf = []; silentMs = 0;
        setState('listening');
      }
    } else { speechMs = Math.max(0, speechMs - blockMs); }
    if (recording) buf.push(new Float32Array(block));
  } else {
    buf.push(new Float32Array(block));
    const threshold = Math.max(0.012, noiseFloor * 3.5);
    if (rms < threshold) { silentMs += blockMs; } else { silentMs = 0; }
    const totalMs = buf.reduce((n, b) => n + b.length, 0) / ctx.sampleRate * 1000;
    if (silentMs > SILENCE_MS || totalMs > MAX_UTTERANCE_MS) finishUtterance();
  }
}

function stopPlayback() {
  const a = document.querySelector('audio');
  if (a) a.pause();
  speaking = false;
}

async function finishUtterance() {
  recording = false; speechMs = 0; silentMs = 0;
  const total = buf.reduce((n, b) => n + b.length, 0);
  const flat = new Float32Array(total);
  let o = 0; for (const b of buf) { flat.set(b, o); o += b.length; }
  buf = [];
  if (total / ctx.sampleRate < 0.3) { setState('idle'); return; }
  const pcm = resample(flat, ctx.sampleRate, SR);
  const wav = encodeWAV(pcm, SR);

  setState('transcribing…');
  const fd = new FormData();
  fd.append('file', wav, 'utterance.wav');
  fd.append('model', 'whisper-1');
  let text = '';
  try {
    const r = await fetch('/api/transcribe', {method:'POST', body: fd});
    text = (await r.json()).text?.trim() || '';
  } catch (err) { setState('idle'); return; }
  if (!text) { setState('idle'); return; }
  say('you', text);
  ask(text);
}

// One path for both typed and spoken turns.
async function ask(text) {
  setState('thinking…');
  history.push({role:'user', content:text});
  let reply = '';
  try {
    const r = await fetch('/api/chat', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({messages: history})});
    const d = await r.json();
    reply = d.choices?.[0]?.message?.content || '';
    if (d.voice && d.voice !== VOICE) {          // Jarvis changed his own voice
      VOICE = d.voice;
      localStorage.setItem('jarvis.voice', VOICE);
      applyVoice(VOICE);
      say('system', 'voice → ' + VOICE);
    }
  } catch (err) { setState('idle'); return; }
  if (!reply) { setState('idle'); return; }
  say('jarvis', reply);
  history.push({role:'assistant', content:reply});
  if (history.length > 24) history = history.slice(-24);

  setState('speaking');
  try {
    const r = await fetch('/api/speak', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({input: reply, voice: VOICE})});
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    speaking = true;
    audio.onended = () => { speaking = false; setState('idle'); URL.revokeObjectURL(url); };
    await audio.play();
  } catch (err) { speaking = false; setState('idle'); }
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

// Voice: a short dropdown (the named voices), because the library is 900+
// seeds. Anything else is called up by description — say or type "speak as a
// scottish dwarf" and Jarvis resolves it (see @@VOICE on the brain).
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
    const o = document.createElement('option');
    o.value = v; o.textContent = v;
    picker.appendChild(o);
  });
  if (!VOICE) VOICE = cfg.default || 'jarvis';
  applyVoice(VOICE);
  picker.onchange = () => {
    VOICE = picker.value;
    localStorage.setItem('jarvis.voice', VOICE);
  };
}).catch(() => {});

// The circle: tap once to arm continuous listening, tap again to stop.
$('liveBtn').onclick = async () => {
  if (running) {
    running = false;
    if (stream) stream.getTracks().forEach(t => t.stop());
    if (ctx) ctx.close();
    $('liveBtn').classList.remove('on', 'speaking', 'thinking');
    $('hint').textContent = 'type, or tap the circle and just talk';
    setState('idle');
  } else {
    try { await start(); }
    catch (err) {
      $('hint').textContent = 'microphone blocked — use the text box, or allow mic access';
      setState('idle');
    }
  }
};

// Typed turns go through exactly the same path as spoken ones.
$('typeBox').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  const text = e.target.value.trim();
  if (!text) return;
  e.target.value = '';
  say('you', text);
  ask(text);
});
</script>
</body>
</html>
"""


@app.get("/live", response_class=HTMLResponse)
async def live():
    return LIVE_PAGE


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8093")))
