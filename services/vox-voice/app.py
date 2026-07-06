from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import logging
import os
import shutil
import subprocess
import tempfile
import soundfile as sf
import numpy as np
import io
from faster_whisper import WhisperModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-voice")

app = FastAPI(title="vox-voice-stt")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load model once on startup
# Using "base" model as requested in the JS client
MODEL_SIZE = os.getenv("WHISPER_MODEL", "tiny.en")
DEVICE = os.getenv("DEVICE", "cpu") 

logger.info(f"Loading Whisper model '{MODEL_SIZE}' on {DEVICE}...")
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type="int8")

@app.get("/")
async def root():
    return {"service": "vox-voice", "status": "running", "model": MODEL_SIZE}

@app.post("/v1/audio/transcriptions")
async def transcribe_audio(
    file: UploadFile = File(...),
    model_name: str = Form("base"),
    language: str = Form("en")
):
    logger.info(f"Received transcription request: {file.filename} ({file.content_type})")

    # Save incoming audio to temp file (could be WebM, WAV, or other)
    raw_suffix = ".webm" if "webm" in file.content_type else ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=raw_suffix) as temp_raw:
        shutil.copyfileobj(file.file, temp_raw)
        raw_path = temp_raw.name

    # Convert to 16kHz mono WAV via ffmpeg (Whisper's audio backends are picky)
    wav_path = raw_path + "_conv.wav"
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", raw_path, "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", "-y", wav_path],
            capture_output=True, timeout=30
        )
        if result.returncode != 0:
            logger.error(f"ffmpeg conversion failed: {result.stderr.decode()[:200]}")
            if os.path.exists(raw_path): os.remove(raw_path)
            return {"text": "", "error": f"Audio conversion failed (return code {result.returncode})"}
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            logger.error("ffmpeg produced empty output")
            if os.path.exists(raw_path): os.remove(raw_path)
            return {"text": "", "error": "Audio conversion produced empty file"}
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg conversion timed out")
        if os.path.exists(raw_path): os.remove(raw_path)
        return {"text": "", "error": "Audio conversion timed out"}
    except Exception as e:
        logger.error(f"Audio conversion failed: {e}")
        if os.path.exists(raw_path): os.remove(raw_path)
        if os.path.exists(wav_path): os.remove(wav_path)
        return {"text": "", "error": f"Audio conversion failed: {str(e)}"}

    try:
        segments, info = model.transcribe(wav_path, beam_size=5, language=language)
        text = " ".join([segment.text for segment in segments]).strip()

        logger.info(f"Transcription complete: '{text}'")
        return {"text": text}

    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return {"text": "", "error": str(e)}

    finally:
        if os.path.exists(raw_path): os.remove(raw_path)
        if os.path.exists(wav_path): os.remove(wav_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
