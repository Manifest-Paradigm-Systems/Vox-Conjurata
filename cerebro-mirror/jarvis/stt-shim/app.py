"""jarvis stt-shim — OpenAI /v1/audio/transcriptions → whisper.cpp.

Open WebUI (and anything else expecting the OpenAI audio API) cannot talk to
whisper.cpp directly: that server is POST /inference with a multipart `file`
field and query-string options, answering with its own JSON shape.

Lives on cerebro so the audio never crosses the LAN — whisper.cpp large-v3
(:8085, HIP on the iGPU) decodes on this box.

Endpoints mirror what the OpenAI SDK sends; `model` is accepted and ignored.
"""

import os

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

WHISPER_URL = os.getenv("WHISPER_URL", "http://127.0.0.1:8085")
DEFAULT_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "en")
TIMEOUT = float(os.getenv("WHISPER_TIMEOUT", "600"))

# whisper.cpp understands these; anything else is normalised to json.
_PASSTHROUGH_FORMATS = {"json", "verbose_json", "srt", "vtt", "text"}

app = FastAPI(title="jarvis stt-shim (OpenAI -> whisper.cpp)")


@app.get("/")
async def root():
    return {"service": "stt-shim", "upstream": WHISPER_URL}


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as c:
            r = await c.get(f"{WHISPER_URL}/")
        ok = r.status_code == 200
    except httpx.HTTPError:
        ok = False
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "upstream": WHISPER_URL},
        status_code=200 if ok else 503,
    )


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form("whisper-1"),  # accepted for compatibility, unused
    language: str = Form(None),
    prompt: str = Form(None),
    temperature: str = Form(None),
    response_format: str = Form("json"),
):
    fmt = (response_format or "json").lower()
    params = {"response_format": fmt if fmt in _PASSTHROUGH_FORMATS else "json"}
    if language:
        params["language"] = language
    elif DEFAULT_LANGUAGE:
        params["language"] = DEFAULT_LANGUAGE
    if prompt:
        params["prompt"] = prompt
    if temperature:
        params["temperature"] = temperature

    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="empty audio upload")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT, connect=5.0)) as c:
            r = await c.post(
                f"{WHISPER_URL}/inference",
                params=params,
                files={"file": (file.filename or "audio.wav", body, file.content_type or "audio/wav")},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"whisper unreachable: {exc}")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=f"whisper failed ({r.status_code}): {r.text[:200]}")

    if params["response_format"] == "text":
        return PlainTextResponse(r.text)
    if params["response_format"] == "srt":
        return PlainTextResponse(r.text, media_type="text/plain")
    if params["response_format"] == "vtt":
        return PlainTextResponse(r.text, media_type="text/vtt")
    try:
        data = r.json()
    except ValueError:
        raise HTTPException(status_code=502, detail=f"whisper returned non-JSON: {r.text[:200]}")
    if params["response_format"] == "verbose_json":
        return data
    return {"text": (data.get("text") or "").strip()}
