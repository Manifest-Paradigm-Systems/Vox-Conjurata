from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import logging
import os
import tempfile
import torch
import gc
from PIL import Image
from diffusers import StableDiffusionXLPipeline, EulerDiscreteScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dreamshaper-xl-service")

app = FastAPI(title="DreamShaper XL Vision Gen")

pipe = None

@app.on_event("startup")
def load_clean_radeon_pipeline():
    global pipe
    logger.info("Loading DreamShaper XL Turbo Pipeline (Fully Resident FP16)...")
    
    # 1. Standard FP16 loader 
    pipe = StableDiffusionXLPipeline.from_pretrained(
        "Lykon/dreamshaper-xl-v2-turbo", 
        torch_dtype=torch.float16, 
        variant="fp16"
    ).to("cuda")

    # 2. Optimized VAE & Memory Flattening
    pipe.enable_vae_slicing()
    pipe.enable_vae_tiling()
    
    # This prevents the UNet from trying to grab massive 6GB+ contiguous blocks of VRAM during generation
    pipe.enable_attention_slicing(1)

    # 3. Apply standard Lightning scheduler
    pipe.scheduler = EulerDiscreteScheduler.from_config(
        pipe.scheduler.config, 
        timestep_spacing="trailing"
    )
    
    logger.info("Stable Diffusion Pipeline Ready (SDPA + VAE Optimizations Active)")

class ImageRequest(BaseModel):
    prompt: str
    negative_prompt: str = ""
    width: int = 1344
    height: int = 768
    steps: int = 4
    cfg_scale: float = 2.0  # Turbo/Lightning usually prefer lower CFG, user hinted at 2.0 earlier for DreamShaper

@app.get("/")
async def root():
    return {"service": "vox-vision-gen", "status": "running" if pipe else "loading"}

@app.post("/generate")
async def generate_image(request: ImageRequest):
    if pipe is None:
        raise HTTPException(status_code=500, detail="Model not loaded.")

    logger.info(f"Prompt: {request.prompt[:50]}...")
    
    try:
        with torch.inference_mode():
            image = pipe(
                prompt=request.prompt,
                negative_prompt=request.negative_prompt,
                num_inference_steps=request.steps,
                guidance_scale=request.cfg_scale,
                width=request.width,
                height=request.height
            ).images[0]
        
        # Upscale to 1080p as per user request
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
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
