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

    # Save incoming audio to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_raw:
        shutil.copyfileobj(file.file, temp_raw)
        raw_path = temp_raw.name

    # Convert to 16kHz mono WAV for Whisper
    wav_path = raw_path + ".wav"
    try:
        # Try soundfile first (handles WAV/FLAC/OGG natively)
        data, sr = sf.read(raw_path)
        if sr != 16000:
            # Simple linear resample to 16kHz
            ratio = 16000 / sr
            new_len = int(len(data) * ratio)
            indices = np.round(np.linspace(0, len(data) - 1, new_len)).astype(int)
            data = data[indices]
        if data.ndim > 1:
            data = data.mean(axis=1)  # mono mix
        sf.write(wav_path, data, 16000, subtype='PCM_16')
        logger.info(f"Converted to WAV: {os.path.getsize(wav_path)} bytes")
    except Exception as e:
        logger.warning(f"soundfile failed ({e}), trying ffmpeg fallback...")
        try:
            result = subprocess.run(
                ["ffmpeg", "-i", raw_path, "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", "-y", wav_path],
                capture_output=True, timeout=30
            )
            if result.returncode != 0:
                logger.error(f"ffmpeg also failed: {result.stderr.decode()[:200]}")
                if os.path.exists(raw_path): os.remove(raw_path)
                return {"text": "", "error": "Audio conversion failed"}
        except Exception as e2:
            logger.error(f"ffmpeg fallback failed: {e2}")
            if os.path.exists(raw_path): os.remove(raw_path)
            return {"text": "", "error": f"Audio conversion failed: {str(e2)}"}

    try:
        segments, info = model.transcribe(wav_path, beam_size=5, language=language)
        text = " ".join([segment.text for segment in segments]).strip()
        # Owner 2026-09-02: faster-whisper auto-detects the language when
        # none was requested — the detected code rides the reply so the
        # archivist can switch languages with the listener.
        detected = getattr(info, "language", None) or (language or "")

        logger.info(f"Transcription complete: '{text}'")
        return {"text": text, "language": detected}

    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return {"text": "", "error": str(e)}

    finally:
        if os.path.exists(raw_path): os.remove(raw_path)
        if os.path.exists(wav_path): os.remove(wav_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
