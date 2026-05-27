from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import os
import tempfile
import edge_tts
import asyncio
import torch
from diffusers import StableAudioPipeline

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

ENGINE_TYPE = os.getenv("AUDIO_ENGINE_TYPE", "music")
MODEL_ID = os.getenv("STABLE_AUDIO_MODEL", "stabilityai/stable-audio-3-small")

class AudioRequest(BaseModel):
    prompt: str
    duration_seconds: float = 30.0

@app.get("/")
async def root():
    return {"service": f"vox-audio-generation-{ENGINE_TYPE}", "status": "running"}

# Global pre-loaded pipeline to keep the model resident in VRAM for instant triggers
pipe = None
device = "cuda" if torch.cuda.is_available() else "cpu"

logger.info(f"Pre-loading Stable Audio {ENGINE_TYPE} model {MODEL_ID} to VRAM...")
pipe = StableAudioPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.float16).to(device)
logger.info(f"Stable Audio {ENGINE_TYPE} model loaded and resident in VRAM.")

@app.post("/v1/audio/speech")
async def text_to_speech(payload: dict):
    text = payload.get("text", "")
    voice = payload.get("voice", "en-US-ChristopherNeural")
    
    if not text:
        raise HTTPException(status_code=400, detail="No text provided.")

    logger.info(f"Generating speech for: '{text[:50]}...' using {voice}")
    fd, output_path = tempfile.mkstemp(suffix=".webm")
    os.close(fd)

    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        return FileResponse(output_path, media_type="audio/webm")
    except Exception as e:
        logger.error(f"TTS error: {e}")
        if os.path.exists(output_path):
            os.remove(output_path)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate")
async def generate_ambient(request: AudioRequest):
    logger.info(f"Generating ambient audio ({ENGINE_TYPE}) for prompt: '{request.prompt[:50]}...'")
    
    try:
        if ENGINE_TYPE == "music":
            formatted_prompt = f"TrackType: Music, VocalType: Instrumental, {request.prompt}"
            audio = pipe(prompt=formatted_prompt, audio_end_in_s=min(request.duration_seconds, 120.0)).audios[0]
            
            fd, output_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            import scipy.io.wavfile
            scipy.io.wavfile.write(output_path, pipe.vae.sampling_rate, audio.T.cpu().numpy())
            return FileResponse(output_path, media_type="audio/wav")
            
        else: # sfx
            formatted_prompt = f"TrackType: SFX, {request.prompt}"
            audio = pipe(prompt=formatted_prompt, audio_end_in_s=request.duration_seconds).audios[0]
            
            fd, output_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            import scipy.io.wavfile
            scipy.io.wavfile.write(output_path, pipe.vae.sampling_rate, audio.T.cpu().numpy())
            return FileResponse(output_path, media_type="audio/wav")
            
    except Exception as e:
        logger.error(f"Stable Audio Gen ({ENGINE_TYPE}) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
