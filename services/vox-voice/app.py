from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import logging
import os
import shutil
import tempfile
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
    
    with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as temp_audio:
        shutil.copyfileobj(file.file, temp_audio)
        temp_path = temp_audio.name

    try:
        segments, info = model.transcribe(temp_path, beam_size=5, language=language)
        text = " ".join([segment.text for segment in segments]).strip()
        
        logger.info(f"Transcription complete: '{text}'")
        return {"text": text}
    
    except Exception as e:
        logger.error(f"Transcription failed: {e}")
        return {"text": "", "error": str(e)}
    
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
