from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from parler_tts import ParlerTTSForConditionalGeneration
from transformers import AutoTokenizer
import logging
import os
import tempfile
import torch
import gc
import scipy.io.wavfile
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-designer")

app = FastAPI(title="vox-designer-parler-tts")

MODEL_ID = os.getenv("PARLER_MODEL", "parler-tts/parler-tts-large-v1")
_device = "cuda" if torch.cuda.is_available() else "cpu"
_generator: tuple | None = None  # lazy-loaded (model, tokenizer)


def _load_model():
    """Lazy-load Parler-TTS Large on first request; stays resident until evicted."""
    global _generator
    if _generator is not None:
        return _generator

    logger.info(f"Loading Parler-TTS model {MODEL_ID} (device={_device})...")
    model = ParlerTTSForConditionalGeneration.from_pretrained(MODEL_ID).to(_device)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    _generator = (model, tokenizer)
    logger.info("Parler-TTS model loaded.")
    return _generator


def _evict_model():
    """Unload the model from VRAM after generation (JIT pattern)."""
    global _generator
    if _generator is None:
        return
    logger.info("Evicting Parler-TTS model from VRAM...")
    model, tokenizer = _generator
    del model
    del tokenizer
    _generator = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("Parler-TTS eviction complete.")


@app.get("/")
async def root():
    loaded = _generator is not None
    return {"service": "vox-designer-parler-tts", "status": "running", "model_loaded": loaded, "device": _device}


@app.post("/generate")
async def generate_seed(payload: dict):
    text = payload.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="No acoustic description provided.")

    logger.info(f"Generating voice seed with Parler-TTS: '{text[:80]}...'")

    model, tokenizer = _load_model()

    # Build a default description prompt — the acoustic description IS the prompt
    # Parler-TTS: model generates speech matching the text description
    input_ids = tokenizer(text, return_tensors="pt").to(_device)
    # The model also needs a prompt describing the speaker/location/style
    # Default to a clear speaking voice
    description = payload.get("description", text)

    gen_kwargs = {
        "input_ids": input_ids.input_ids,
        "attention_mask": input_ids.attention_mask,
        "max_new_tokens": 256,
        "do_sample": True,
        "temperature": 1.0,
        "top_k": 50,
        "top_p": 0.9,
    }

    with torch.no_grad():
        generation = model.generate(**gen_kwargs)

    # Decode to audio array
    audio_arr = generation.cpu().numpy().squeeze()

    # Write to temp WAV file
    fd, output_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        scipy.io.wavfile.write(output_path, model.config.sampling_rate, audio_arr)
        logger.info(f"Parler-TTS generation complete ({len(audio_arr)} samples @ {model.config.sampling_rate} Hz)")
        return FileResponse(output_path, media_type="audio/wav")
    except Exception as e:
        logger.error(f"Parler-TTS error: {e}")
        if os.path.exists(output_path):
            os.remove(output_path)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # JIT eviction: free VRAM after each generation
        _evict_model()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5010)