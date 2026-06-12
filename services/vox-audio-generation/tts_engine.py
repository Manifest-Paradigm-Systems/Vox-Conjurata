from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import os
import tempfile
import torch
import gc
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

if ENGINE_TYPE == "sfx":
    logger.info(f"Pre-loading Stable Audio SFX model {MODEL_ID} to VRAM...")
    pipe = StableAudioPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.float16).to(device)
    logger.info(f"Stable Audio SFX model loaded and resident in VRAM.")
else:
    logger.info(f"Stable Audio Music engine ready for JIT loading.")

@app.post("/generate")
async def generate_audio(request: AudioRequest):
    global pipe
    logger.info(f"Generating {ENGINE_TYPE} audio for prompt: '{request.prompt[:50]}...'")
    
    active_pipe = pipe
    jit_mode = False
    
    try:
        if ENGINE_TYPE == "music":
            logger.info(f"JIT Loading Music model {MODEL_ID}...")
            active_pipe = StableAudioPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.float16).to(device)
            jit_mode = True
            
        if active_pipe is None:
            raise HTTPException(status_code=500, detail=f"Pipeline not loaded for {ENGINE_TYPE}")

        prompt_prefix = "TrackType: Music, VocalType: Instrumental, " if ENGINE_TYPE == "music" else "TrackType: SFX, "
        formatted_prompt = f"{prompt_prefix}{request.prompt}"
        
        with torch.inference_mode():
            audio = active_pipe(prompt=formatted_prompt, audio_end_in_s=request.duration_seconds).audios[0]
            
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        import scipy.io.wavfile
        scipy.io.wavfile.write(output_path, active_pipe.vae.sampling_rate, audio.T.cpu().numpy())
        return FileResponse(output_path, media_type="audio/wav")
            
    except Exception as e:
        logger.error(f"Stable Audio Gen ({ENGINE_TYPE}) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if jit_mode and active_pipe is not None:
            logger.info("Evicting JIT Music model from VRAM...")
            del active_pipe
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("JIT Eviction complete.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

