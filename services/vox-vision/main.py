from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import logging
import os
import torch
import gc
from diffusers import StableDiffusionXLPipeline, AutoencoderKL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-vision")

app = FastAPI(title="vox-vision")

MODEL_ID = os.getenv("SDXL_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")

class ImageRequest(BaseModel):
    prompt: str
    height: int = 1024
    width: int = 1024
    num_inference_steps: int = 30

@app.get("/")
async def root():
    return {"service": "vox-vision", "status": "running"}

@app.post("/generate")
async def generate_image(request: ImageRequest):
    logger.info(f"JIT Loading SDXL model {MODEL_ID} to GPU VRAM...")
    pipe = None
    vae = None
    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16)
        pipe = StableDiffusionXLPipeline.from_pretrained(
            MODEL_ID,
            vae=vae,
            torch_dtype=torch.float16,
            variant="fp16",
            use_safetensors=True
        ).to(device)
        pipe.enable_vae_tiling()
        
        logger.info(f"Generating image for prompt: '{request.prompt[:50]}...'")
        image = pipe(
            prompt=request.prompt,
            height=request.height,
            width=request.width,
            num_inference_steps=request.num_inference_steps
        ).images[0]
        
        output_path = "/tmp/vision_output.png"
        image.save(output_path)
        return FileResponse(output_path, media_type="image/png")
    except Exception as e:
        logger.error(f"SDXL Visual Gen error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Evict SDXL from VRAM immediately to free up GPU memory
        if pipe is not None:
            logger.info("Evicting SDXL from GPU VRAM...")
            del pipe
            if vae is not None:
                del vae
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("SDXL JIT eviction complete.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)

