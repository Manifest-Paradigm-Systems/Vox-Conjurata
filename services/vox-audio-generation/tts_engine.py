from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import logging
import os
import tempfile
import edge_tts
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-audio-generation")

app = FastAPI(title="vox-audio-generation-tts")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Default voice
DEFAULT_VOICE = os.getenv("VOICE", "en-US-ChristopherNeural")

@app.get("/")
async def root():
    return {"service": "vox-audio-generation", "status": "running", "provider": "edge-tts"}

@app.get("/v1/audio/speech")
async def text_to_speech_get(text: str, voice: str = DEFAULT_VOICE):
    if not text:
        raise HTTPException(status_code=400, detail="No text provided.")
    return await text_to_speech({"text": text, "voice": voice})

@app.post("/v1/audio/speech")
async def text_to_speech(payload: dict):
    text = payload.get("text", "")
    voice = payload.get("voice", DEFAULT_VOICE)
    
    if not text:
        raise HTTPException(status_code=400, detail="No text provided.")

    logger.info(f"Generating speech for: '{text[:50]}...' using {voice}")

    # Create a temporary file for the output - using .webm as requested
    fd, output_path = tempfile.mkstemp(suffix=".webm")
    os.close(fd)

    try:
        # Communicate defaults to mp3, we can try to let edge-tts handle output format if supported, 
        # or just use webm as a container for whatever it sends.
        # Note: edge-tts primarily supports mp3/wav/opus. webm is often opus.
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        return FileResponse(output_path, media_type="audio/webm")

    except Exception as e:
        logger.error(f"TTS error: {e}")
        if os.path.exists(output_path):
            os.remove(output_path)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
