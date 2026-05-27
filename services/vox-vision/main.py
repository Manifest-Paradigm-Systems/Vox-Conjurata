from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import logging
import os
import torch
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

# Pre-load SDXL on startup to keep it resident in VRAM for instant delivery
logger.info(f"Loading SDXL model {MODEL_ID} to GPU VRAM...")
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
logger.info("SDXL model successfully loaded and resident in VRAM.")

@app.post("/generate")
async def generate_image(request: ImageRequest):
    logger.info(f"Generating image for prompt: '{request.prompt[:50]}...'")
    try:
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
