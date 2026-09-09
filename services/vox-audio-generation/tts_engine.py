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
from stable_audio_3 import StableAudioModel

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
# SA3 model id: "medium" (1.4B, 380s, ~5-6.5 GiB peak) or "small-music"/"small-sfx"
# (433M, 120s, CPU-capable). Stable Audio 3 native loader (replaces the old
# diffusers/stable-audio-open-1.0 path + torchsde CPU-wedge; 2026-09-09).
SA3_MODEL_ID = os.getenv("SA3_MODEL_ID", "medium")

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

logger.info(f"Pre-loading Stable Audio 3 model '{SA3_MODEL_ID}'...")
pipe = StableAudioModel.from_pretrained(SA3_MODEL_ID)  # auto device (ROCm torch reports cuda)
SA3_SR = int(getattr(getattr(pipe, "model", None), "sample_rate", 44100))
logger.info(f"Stable Audio 3 model loaded and resident (sample_rate={SA3_SR}).")

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
        
        # SA3 native params: 8-step pingpong diffusion, CFG 1.0 defaults.
        # Caller values override when supplied (steps>1 useful for quality).
        audio = pipe.generate(
            prompt=formatted_prompt,
            duration=request.duration_seconds,
            steps=request.num_inference_steps or 8,
            cfg_scale=request.guidance_scale or 1.0,
        )  # torch.Tensor

        arr = audio.detach().cpu().float().numpy()
        if arr.ndim == 3:
            arr = arr[0]  # (batch, channels, samples) -> (channels, samples)

        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        import scipy.io.wavfile
        scipy.io.wavfile.write(output_path, SA3_SR, arr.T)
        return FileResponse(output_path, media_type="audio/wav")
            
    except Exception as e:
        logger.error(f"Stable Audio Gen ({ENGINE_TYPE}) error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

