from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
import os
import tempfile
import torch
import gc
from diffusers import AutoPipelineForText2Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-vision-gen")

app = FastAPI(title="vox-vision-gen-sdxl")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL_ID = "stabilityai/sdxl-turbo"

class ImageRequest(BaseModel):
    prompt: str
    width: int = 512
    height: int = 512
    num_inference_steps: int = 2
    guidance_scale: float = 0.0

@app.get("/")
async def root():
    return {"service": "vox-vision-gen", "status": "running"}

@app.post("/generate")
async def generate_image(request: ImageRequest):
    logger.info(f"Generating image (SDXL Turbo JIT) for prompt: '{request.prompt[:50]}...'")
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipe = None
    
    try:
        logger.info(f"JIT Loading {MODEL_ID} to VRAM...")
        pipe = AutoPipelineForText2Image.from_pretrained(
            MODEL_ID, 
            torch_dtype=torch.float16, 
            variant="fp16"
        ).to(device)
        
        # sdxl-turbo is optimized for 1-4 steps and guidance_scale=0.0
        image = pipe(
            prompt=request.prompt, 
            num_inference_steps=request.num_inference_steps, 
            guidance_scale=request.guidance_scale,
            width=request.width,
            height=request.height
        ).images[0]
        
        fd, output_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        image.save(output_path)
        
        return FileResponse(output_path, media_type="image/png")
        
    except Exception as e:
        logger.error(f"SDXL Gen error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if pipe is not None:
            logger.info("Evicting SDXL model from VRAM...")
            del pipe
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info("SDXL JIT eviction complete.")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
