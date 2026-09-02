from fastapi import FastAPI, HTTPException
from PIL import ImageDraw, ImageFont
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

# System-level injection configuration for CyberRealisticPony
SYSTEM_POSITIVE_PREFIX = "score_9, score_8_up, score_7_up, source_anime, cinematic photo, dark fantasy atmospheric,"
SYSTEM_NEGATIVE_PREFIX = "score_4, score_5, score_6, 3d render, vector graphic, low quality, distorted anatomy, bright, vibrant, neon,"

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
    # Owner 2026-09-01: img2img — an optional INIT image (base64 PNG) that
    # gets colorized/re-imagined per the prompt (watercolor pass on the
    # book's original plates); strength = how far from the original.
    image: str = ""
    strength: float = 0.65
    lora_name: str | None = None
    lora_multiplier: float = 1.0
    width: int = 1024
    height: int = 1024
    steps: int = 20
    cfg_scale: float = 7.0
    sample_method: str = "euler"

@app.get("/")
async def root():
    return {"service": "vox-vision-gen", "status": "running" if sd_model else "failed"}

@app.post("/generate")
async def generate_image(request: ImageRequest):
    if sd_model is None:
        raise HTTPException(status_code=500, detail="Base model not initialized.")

    # Combine the baked container tags with the dynamic DM prompt
    final_positive_prompt = f"{SYSTEM_POSITIVE_PREFIX} {request.prompt}"
    final_negative_prompt = f"{SYSTEM_NEGATIVE_PREFIX} {request.negative_prompt}"

    logger.info(f"Final Prompt: {final_positive_prompt[:100]}...")
    
    try:
        init_image = None
        if request.image:
            import base64 as _b64, io as _io
            init_image = Image.open(_io.BytesIO(_b64.b64decode(request.image)))
            init_image = init_image.convert("RGB")
        images = sd_model.generate_image(
            prompt=final_positive_prompt,
            negative_prompt=final_negative_prompt,
            width=request.width,
            height=request.height,
            sample_steps=request.steps,
            cfg_scale=request.cfg_scale,
            sample_method=request.sample_method,
            scheduler="discrete",
            init_image=init_image,
            strength=request.strength,
        )
        
        image = images[0]
        
        # Upscale to 1080p if not already
        if image.width != 1920 or image.height != 1080:
            logger.info(f"Upscaling from {image.width}x{image.height} to 1920x1080")
            image = image.resize((1920, 1080), Image.LANCZOS)

        # Owner 2026-09-01: the "Enhanced by CinemaTome" brand line — a
        # PROMINENT watermark band on the bottom (phone-sized viewing):
        # ~10% of the frame height, bold gold text with a dark outline on
        # a solid dark band.
        _draw = ImageDraw.Draw(image)
        _w, _h = image.size
        _band_h = max(int(_h * 0.10), 96)
        _band = Image.new("RGBA", (_w, _band_h), (20, 16, 12, 235))
        image.paste(_band, (0, _h - _band_h), _band)
        _font = None
        for _fp in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ):
            if os.path.exists(_fp):
                try:
                    _font = ImageFont.truetype(_fp, int(_band_h * 0.60))
                except Exception:
                    _font = None
                if _font is not None:
                    break
        if _font is None:
            _font = ImageFont.load_default()
        _text = "Enhanced by CinemaTome"
        _bbox = _draw.textbbox((0, 0), _text, font=_font)
        _tw = _bbox[2] - _bbox[0]
        _th = _bbox[3] - _bbox[1]
        _x = (_w - _tw) // 2
        _y = _h - _band_h + (_band_h - _th) // 2
        for _dx in (-3, 3):
            for _dy in (-3, 3):
                _draw.text((_x + _dx, _y + _dy), _text, font=_font, fill=(10, 8, 6, 255))
        _draw.text((_x, _y), _text, font=_font, fill=(235, 190, 60, 255))

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
