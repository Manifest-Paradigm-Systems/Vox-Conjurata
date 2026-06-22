from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import os
import tempfile
import torch
import gc
import time
import asyncio
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

last_used_time = time.time()

def update_last_used():
    global last_used_time
    last_used_time = time.time()

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(vram_flusher_loop())

async def vram_flusher_loop():
    global last_used_time
    logger.info("🧹 VRAM Flusher background loop started.")
    while True:
        await asyncio.sleep(15)
        if time.time() - last_used_time > 60:
            if torch.cuda.is_available():
                before = torch.cuda.memory_reserved()
                torch.cuda.empty_cache()
                gc.collect()
                after = torch.cuda.memory_reserved()
                if before > after:
                    logger.info(f"🧹 VRAM Flusher: Cleaned PyTorch cache. Freed {(before - after)/1024**2:.2f} MB. Reserved: {after/1024**2:.2f} MB")

ENGINE_TYPE = os.getenv("AUDIO_ENGINE_TYPE", "music")
MODEL_ID = os.getenv("STABLE_AUDIO_MODEL", "stabilityai/stable-audio-3-small")

class AudioRequest(BaseModel):
    prompt: str
    duration_seconds: float = 30.0
    engine_type: str | None = None
    num_inference_steps: int | None = None
    guidance_scale: float | None = None

@app.get("/")
async def root():
    return {"service": f"vox-audio-generation-consolidated", "status": "running"}

# Global pre-loaded pipeline to keep the model resident in VRAM for instant triggers
pipe = None
device = "cuda" if torch.cuda.is_available() else "cpu"

logger.info(f"Pre-loading Stable Audio model {MODEL_ID} to VRAM...")
pipe = StableAudioPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.float16).to(device)
logger.info(f"Stable Audio model loaded and resident in VRAM.")

@app.post("/generate")
async def generate_audio(request: AudioRequest):
    global pipe
    engine_type = request.engine_type or ENGINE_TYPE
    logger.info(f"Generating {engine_type} audio for prompt: '{request.prompt[:50]}...'")
    
    try:
        update_last_used()
        if pipe is None:
            raise HTTPException(status_code=500, detail=f"Pipeline not loaded")

        prompt_prefix = "TrackType: Music, VocalType: Instrumental, " if engine_type == "music" else "TrackType: SFX, "
        formatted_prompt = f"{prompt_prefix}{request.prompt}"
        
        generate_kwargs = {
            "prompt": formatted_prompt,
            "audio_end_in_s": request.duration_seconds,
        }
        if request.num_inference_steps is not None:
            generate_kwargs["num_inference_steps"] = request.num_inference_steps
        if request.guidance_scale is not None:
            generate_kwargs["guidance_scale"] = request.guidance_scale

        with torch.inference_mode():
            audio = pipe(**generate_kwargs).audios[0]
            
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        import scipy.io.wavfile
        scipy.io.wavfile.write(output_path, pipe.vae.sampling_rate, audio.T.cpu().to(torch.float32).numpy())
        return FileResponse(output_path, media_type="audio/wav")
            
    except Exception as e:
        logger.error(f"Stable Audio Gen ({ENGINE_TYPE}) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

