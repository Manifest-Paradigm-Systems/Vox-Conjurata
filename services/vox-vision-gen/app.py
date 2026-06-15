from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import logging
import os
import tempfile
import gc
from PIL import Image

from stable_diffusion_cpp import StableDiffusion

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sdxl-gguf-service")

app = FastAPI(title="SDXL GGUF LoRA Gen")

MODEL_PATH = os.getenv("MODEL_PATH", "/models/stable-diffusion-xl-base-1.0-Q4_0.gguf")
CLIP_L_PATH = os.getenv("CLIP_L_PATH", "/models/clip/clip_l.safetensors")
CLIP_G_PATH = os.getenv("CLIP_G_PATH", "/models/clip/clip_g.safetensors")
VAE_PATH = os.getenv("VAE_PATH", "")
LORA_DIR = os.getenv("LORA_DIR", "/loras")
THREADS = int(os.getenv("THREADS", "8"))

try:
    logger.info(f"Loading SDXL Components (HOT). UNet: {MODEL_PATH}")
    kwargs = {
        "model_path": MODEL_PATH,
        "clip_l_path": CLIP_L_PATH,
        "clip_g_path": CLIP_G_PATH,
        "wtype": "q4_0",
        "n_threads": THREADS
    }
    if VAE_PATH:
        kwargs["vae_path"] = VAE_PATH
        
    sd_model = StableDiffusion(**kwargs)
    logger.info("SDXL Base + Text Encoders + Custom VAE loaded successfully in VRAM.")
except Exception as e:
    logger.error(f"Failed to load base model: {e}")
    sd_model = None

class ImageRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    lora_name: str | None = None
    lora_multiplier: float = 1.0
    width: int = 1024
    height: int = 1024
    steps: int = 4
    cfg_scale: float = 1.0
    sample_method: str = "euler"

@app.get("/")
async def root():
    return {"service": "vox-vision-gen", "status": "running" if sd_model else "failed"}

@app.post("/generate")
async def generate_image(request: ImageRequest):
    if sd_model is None:
        raise HTTPException(status_code=500, detail="Base model not initialized.")

    logger.info(f"Prompt: {request.prompt[:50]}...")
    
    try:
        images = sd_model.generate_image(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            width=request.width,
            height=request.height,
            sample_steps=request.steps,
            cfg_scale=request.cfg_scale,
            sample_method=request.sample_method,
            scheduler="discrete"
        )
        
        image = images[0]
        
        # Upscale to 1080p if not already
        if image.width != 1920 or image.height != 1080:
            logger.info(f"Upscaling from {image.width}x{image.height} to 1920x1080")
            image = image.resize((1920, 1080), Image.LANCZOS)
        
        fd, output_path = tempfile.mkstemp(suffix=".webp")
        os.close(fd)
        
        image.save(output_path, format="WEBP", quality=85)
        
        return FileResponse(output_path, media_type="image/webp")
        
    except Exception as e:
        logger.error(f"Generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        gc.collect()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
