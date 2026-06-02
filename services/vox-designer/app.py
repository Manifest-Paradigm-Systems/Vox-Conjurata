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
    global _generator
    if _generator is not None:
        return _generator
    logger.info(f"Loading Parler-TTS {MODEL_ID} (device={_device})...")
    model = ParlerTTSForConditionalGeneration.from_pretrained(MODEL_ID).to(_device)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    _generator = (model, tokenizer)
    logger.info("Parler-TTS model loaded.")
    return _generator


def _evict_model():
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
    # The "text" field is an acoustic description (e.g. "a raspy old knight")
    # Parler-TTS needs both: a text to speak, and a description of the voice.
    acoustic_desc = payload.get("text", "")
    if not acoustic_desc:
        raise HTTPException(status_code=400, detail="No acoustic description provided.")

    # Also accept a custom prompt text; default to a simple test phrase
    prompt_text = payload.get("prompt_text", "Hello, I am a character in this world.")

    logger.info(f"Parler-TTS: desc='{acoustic_desc[:60]}...'  prompt='{prompt_text[:60]}...'")

    model, tokenizer = _load_model()

    try:
        # Parler-TTS: input_ids = text to speak, prompt_input_ids = voice description
        input_ids = tokenizer(prompt_text, return_tensors="pt").to(_device)
        prompt_ids = tokenizer(acoustic_desc, return_tensors="pt").to(_device)

        with torch.no_grad():
            generation = model.generate(
                input_ids=input_ids.input_ids,
                prompt_input_ids=prompt_ids.input_ids,
                do_sample=True,
                temperature=1.0,
                top_k=50,
                top_p=0.9,
                max_new_tokens=512,
            )
            audio_arr = generation.cpu().numpy().squeeze()

        sample_rate = model.config.sampling_rate
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        scipy.io.wavfile.write(output_path, sample_rate, audio_arr)
        logger.info(f"Parler-TTS done ({len(audio_arr)} samples @ {sample_rate} Hz)")
        return FileResponse(output_path, media_type="audio/wav")

    except Exception as e:
        logger.error(f"Parler-TTS error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _evict_model()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5010)