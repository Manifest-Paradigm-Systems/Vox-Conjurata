from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import logging
import os
import tempfile
import gc

from stable_diffusion_cpp import StableDiffusion

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sdxl-gguf-service")

app = FastAPI(title="SDXL GGUF LoRA Gen")

MODEL_PATH = os.getenv("MODEL_PATH", "/models/stable-diffusion-xl-base-1.0-Q4_0.gguf")
CLIP_L_PATH = os.getenv("CLIP_L_PATH", "/models/clip/clip_l.safetensors")
CLIP_G_PATH = os.getenv("CLIP_G_PATH", "/models/clip/clip_g.safetensors")
VAE_PATH = os.getenv("VAE_PATH", "/models/vae/xlVAEC_c91.safetensors")
LORA_DIR = os.getenv("LORA_DIR", "/loras")
THREADS = int(os.getenv("THREADS", "8"))

try:
    logger.info(f"Loading SDXL Components (HOT). UNet: {MODEL_PATH}")
    sd_model = StableDiffusion(
        model_path=MODEL_PATH,
        clip_l_path=CLIP_L_PATH,
        clip_g_path=CLIP_G_PATH,
        vae_path=VAE_PATH,
        wtype="q4_0",
        n_threads=THREADS,
    )
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
    steps: int = 20
    cfg_scale: float = 7.0

@app.get("/")
async def root():
    return {"service": "vox-vision-gen", "status": "running" if sd_model else "failed"}

@app.post("/generate")
async def generate_image(request: ImageRequest):
    if sd_model is None:
        raise HTTPException(status_code=500, detail="Base model not initialized.")

    logger.info(f"Prompt: {request.prompt[:50]}...")
    
    lora_path = ""
    if request.lora_name:
        lora_path = os.path.join(LORA_DIR, f"{request.lora_name}.safetensors")
        if not os.path.exists(lora_path):
            raise HTTPException(status_code=404, detail=f"LoRA '{request.lora_name}' not found.")
        logger.info(f"Patching LoRA weights from: {lora_path} at strength {request.lora_multiplier}")

    try:
        images = sd_model.txt2img(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            lora_model_dir=lora_path if lora_path else "",
            lora_multiplier=request.lora_multiplier,
            width=request.width,
            height=request.height,
            sample_steps=request.steps,
            cfg_scale=request.cfg_scale,
        )
        
        fd, output_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        
        images[0].save(output_path)
        
        return FileResponse(output_path, media_type="image/png")
        
    except Exception as e:
        logger.error(f"Generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        gc.collect()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
