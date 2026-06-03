"""
vox-actor — CosyVoice 3 TTS Service
Platform: AMD ROCm (gfx1201 / RDNA3)
Engine:   CosyVoice 3 (zero-shot voice cloning via Qwen2.5-0.5B + DiT)
Port:     5020

Performance optimisations:
  - Model loaded on first request; kept warm for subsequent calls.
  - MIOpen kernel warm-up on startup via dummy inference.
  - Reference audio saved as tempfile, passed to CosyVoice inference_zero_shot.
"""

import sys
import os
os.environ["MIOPEN_FIND_MODE"] = "2"

# Add CosyVoice package path (cloned at build time from GitHub)
_cosyvoice_dir = os.getenv("COSYVOICE_PACKAGE_DIR", "/app/CosyVoice")
_matcha_dir = os.path.join(_cosyvoice_dir, "third_party", "Matcha-TTS")
for _p in [_cosyvoice_dir, _matcha_dir]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import torch
import torchaudio
import soundfile as sf
import tempfile
import logging
import gc
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse

# ---------------------------------------------------------------------------
# Monkeypatch: torchaudio in 2026 forces torchcodec, which is broken on ROCm
# ---------------------------------------------------------------------------
def _monkeypatch_torchaudio_load(uri, **kwargs):
    logging.info(f"Monkeypatch loading audio: {uri}")
    # Simple soundfile-based fallback that ignores ignored arguments
    # soundfile.read returns (time, channels), torchaudio expects (channels, time)
    data, samplerate = sf.read(uri, dtype='float32')
    tensor = torch.from_numpy(data)
    if tensor.ndim == 1:
        tensor = tensor.unsqueeze(0)
    else:
        tensor = tensor.T
    logging.info(f"Monkeypatch loaded: {tensor.shape} @ {samplerate} Hz")
    return tensor, samplerate

def _monkeypatch_torchaudio_save(uri, tensor, sample_rate, **kwargs):
    logging.info(f"Monkeypatch saving audio: {uri} (shape={tensor.shape})")
    # soundfile expects [time, channels]
    if tensor.ndim == 2:
        data = tensor.T.cpu().numpy()
    else:
        data = tensor.cpu().numpy()
    sf.write(uri, data, sample_rate)

logging.info("Monkeypatching torchaudio.load/save to use soundfile (bypassing broken torchcodec)")
torchaudio.load = _monkeypatch_torchaudio_load
torchaudio.save = _monkeypatch_torchaudio_save
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] vox-actor — %(message)s",
)
logger = logging.getLogger("vox-actor")

# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------
_rocm_available = torch.cuda.is_available()
_device = "cuda" if _rocm_available else "cpu"
logger.info(f"PyTorch backend: {_device} | ROCm/HIP: {_rocm_available}")
if _rocm_available:
    logger.info(f"GPU: {torch.cuda.get_device_name(0)}")

# ---------------------------------------------------------------------------
# CosyVoice 3 — lazy-loaded on first request, kept warm
# ---------------------------------------------------------------------------
MODEL_DIR = os.getenv("COSYVOICE_MODEL_DIR", "/models/Fun-CosyVoice3-0.5B")
_cosyvoice = None  # cached model instance
_loaded = False


def _load_model():
    """Load CosyVoice 3 on first call; subsequent calls reuse cached instance."""
    global _cosyvoice, _loaded
    if _cosyvoice is not None:
        return _cosyvoice

    # Ensure model directory exists — download from HuggingFace if needed
    if not os.path.isdir(MODEL_DIR):
        logger.info(f"Downloading CosyVoice 3 model from HuggingFace to {MODEL_DIR}...")
        from huggingface_hub import snapshot_download
        os.makedirs(MODEL_DIR, exist_ok=True)
        snapshot_download(
            "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            local_dir=MODEL_DIR,
            local_dir_use_symlinks=False,
        )
        logger.info("CosyVoice 3 model downloaded.")

    logger.info(f"Loading CosyVoice 3 from {MODEL_DIR}...")
    from cosyvoice.cli.cosyvoice import AutoModel

    _cosyvoice = AutoModel(
        model_dir=MODEL_DIR,
        fp16=torch.cuda.is_available(),
        load_vllm=False,
    )
    _loaded = True
    logger.info(f"CosyVoice 3 loaded (model_dir={MODEL_DIR})")
    return _cosyvoice


def _evict_model():
    """Unload model from VRAM (JIT pattern — call after each request if desired)."""
    global _cosyvoice, _loaded
    if _cosyvoice is None:
        return
    logger.info("Evicting CosyVoice 3 from VRAM...")
    del _cosyvoice
    _cosyvoice = None
    _loaded = False
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("CosyVoice 3 eviction complete.")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="vox-actor-cosyvoice3")


@app.get("/")
async def root():
    return {
        "service": "vox-actor",
        "engine": "cosyvoice3",
        "status": "running",
        "model_loaded": _loaded,
        "device": _device,
        "model_dir": MODEL_DIR,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "device": _device, "model_loaded": _loaded}


@app.post("/api/clear_cache")
async def clear_cache():
    """Purge GPU memory."""
    _evict_model()
    return {"status": "ok", "message": "GPU cache cleared"}


@app.post("/api/tts")
async def text_to_speech(
    text: str = Form(...),
    reference_audio: UploadFile = File(...),
    prompt_text: str = Form(""),
    emotion: str = Form("default"),
):
    """Zero-shot TTS via CosyVoice 3.

    Accepts:
      - text: the dialogue line to speak (required)
      - reference_audio: WAV file for voice cloning (required)
      - prompt_text: what words are spoken in the reference audio
        (optional; if empty, a generic default is used)
      - emotion: ignored (CosyVoice 3 handles emotion through reference)

    Returns: WAV audio bytes.
    """
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")

    model = _load_model()

    # Save reference audio to a temp WAV
    ref_fd, ref_path = tempfile.mkstemp(suffix=".wav")
    os.close(ref_fd)
    try:
        ref_bytes = await reference_audio.read()
        with open(ref_path, "wb") as f:
            f.write(ref_bytes)

        # If no prompt_text provided, use a fixed default
        if not prompt_text.strip():
            prompt_text = "You are a helpful assistant.<|endofprompt|>This is a voice sample for character speech."
        elif "<|endofprompt|>" not in prompt_text:
            # CosyVoice 3 requires this token to separate prompt from instruction/context
            prompt_text = f"You are a helpful assistant.<|endofprompt|>{prompt_text}"

        logger.info(
            f"CosyVoice 3 generating: text='{text[:60]}...' "
            f"prompt='{prompt_text[:60]}...' ref={len(ref_bytes)} bytes"
        )

        # Run inference (returns generator of dicts)
        result = None
        for i, j in enumerate(
            model.inference_zero_shot(
                tts_text=text,
                prompt_text=prompt_text,
                prompt_wav=ref_path,
                stream=False,
            )
        ):
            result = j  # take the last (and only, since stream=False) result

        if result is None or "tts_speech" not in result:
            raise HTTPException(status_code=500, detail="CosyVoice 3 returned no output")

        # Convert tensor to WAV
        audio_tensor = result["tts_speech"].cpu()
        sample_rate = model.sample_rate

        out_fd, out_path = tempfile.mkstemp(suffix=".wav")
        os.close(out_fd)
        try:
            torchaudio.save(out_path, audio_tensor.unsqueeze(0), sample_rate)
            logger.info(f"CosyVoice 3 done ({audio_tensor.shape[0]} samples @ {sample_rate} Hz)")
            return FileResponse(out_path, media_type="audio/wav")
        except Exception as e:
            logger.error(f"CosyVoice 3 save error: {e}")
            if os.path.exists(out_path):
                os.remove(out_path)
            raise HTTPException(status_code=500, detail=str(e))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CosyVoice 3 inference error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(ref_path):
            os.remove(ref_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5020)