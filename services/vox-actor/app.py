"""
vox-actor — CosyVoice TTS Service
Platform: AMD ROCm (gfx1201 / RDNA3)
Engine:   CosyVoice-300M (zero-shot voice cloning)
Port:     5020
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
import logging
import os
import tempfile
import torch
import torchaudio
import hashlib
import gc
from modelscope import snapshot_download

# ── Logger must be defined BEFORE any module-level usage ──────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("vox-actor")

# ── ROCm device check ─────────────────────────────────────────────────────────
# On AMD ROCm hardware torch.cuda.* IS the HIP API — not NVIDIA CUDA.
# torch.cuda.is_available() returns True when a ROCm GPU is present.
_rocm_available = torch.cuda.is_available()
_device = "hip" if _rocm_available else "cpu"   # Canonical label for AMD
_torch_device = "cuda" if _rocm_available else "cpu"  # PyTorch internal name
logger.info(f"PyTorch backend: {_device} | ROCm/HIP available: {_rocm_available}")
if _rocm_available:
    logger.info(f"AMD GPU: {torch.cuda.get_device_name(0)}")

# ── CosyVoice import (deferred — library lives in cloned repo) ────────────────
CosyVoice = None
load_wav = None
try:
    from cosyvoice.cli.cosyvoice import CosyVoice
    from cosyvoice.utils.file_utils import load_wav
    logger.info("CosyVoice library loaded successfully.")
except ImportError as _import_err:
    logger.warning(
        f"CosyVoice library not found ({_import_err}). "
        "Service will run in placeholder mode until the model is available."
    )

# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="vox-actor",
    description="CosyVoice zero-shot voice-cloning TTS — AMD ROCm backend",
    version="1.0.0",
)

# Model configuration (override via compose env)
MODEL_DIR = os.getenv("COSYVOICE_MODEL_DIR", "/models/CosyVoice-300M")

# Singleton engine instance
_cosyvoice_engine = None


def get_cosyvoice():
    """Lazy-load and cache the CosyVoice engine."""
    global _cosyvoice_engine

    if _cosyvoice_engine is not None:
        return _cosyvoice_engine

    if CosyVoice is None:
        raise HTTPException(
            status_code=503,
            detail="CosyVoice library not installed — check container build logs.",
        )

    if not os.path.exists(MODEL_DIR):
        logger.info(f"Downloading CosyVoice weights → {MODEL_DIR} ...")
        snapshot_download("iic/CosyVoice-300M", local_dir=MODEL_DIR)

    logger.info(f"Initialising CosyVoice engine from {MODEL_DIR} ...")
    _cosyvoice_engine = CosyVoice(MODEL_DIR)
    logger.info("CosyVoice engine ready.")
    return _cosyvoice_engine


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "service": "vox-actor",
        "engine": "CosyVoice-300M",
        "backend": "AMD ROCm / HIP",
        "device": _device,
        "rocm_available": _rocm_available,
        "status": "ready" if _cosyvoice_engine else "idle",
    }


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "device": _device, "rocm": _rocm_available})


@app.post("/api/clear_cache")
async def clear_cache():
    global _cosyvoice_engine
    logger.info("Purging Vox-Actor Engine cache and VRAM...")
    _cosyvoice_engine = None
    
    # 1. Clear CUDA/ROCm cache layers if GPU runtime is pinned
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception as e:
        logger.warning(f"Failed to clear GPU memory cache: {e}")
    
    # 2. Force Python engine to wipe unreferenced weight tensors from memory
    gc.collect()
    logger.info("[-] Vox-Actor Engine cache successfully purged.")
    return JSONResponse({
        "status": "success",
        "message": "Vox-Actor Engine cache successfully purged."
    })


@app.post("/api/tts")
async def text_to_speech(
    text: str = Form(...),
    reference_audio: UploadFile = File(...),
    prompt_text: str = Form(default="A clear speaking voice."),
):
    """
    Zero-shot voice cloning.

    Parameters
    ----------
    text            : Text to synthesise.
    reference_audio : Short WAV clip of the target speaker (3–15 s, 16 kHz).
    prompt_text     : Transcript of the reference clip (improves fidelity).
    """
    if not text.strip():
        raise HTTPException(status_code=400, detail="'text' field must not be empty.")

    logger.info(f"TTS request — {len(text)} chars, ref: {reference_audio.filename!r}")

    ref_path = None
    output_path = None

    try:
        # 1. Save reference audio to a temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as ref_tmp:
            ref_tmp.write(await reference_audio.read())
            ref_path = ref_tmp.name

        # 2. Resample reference audio to 16 kHz (CosyVoice requirement)
        prompt_speech_16k = load_wav(ref_path, 16000)

        # 3. Infer
        engine = get_cosyvoice()
        output_iter = engine.inference_zero_shot(text, prompt_text, prompt_speech_16k)

        # 4. Collect generator output and concatenate tensors
        chunks = [chunk["tts_speech"] for chunk in output_iter]
        if not chunks:
            raise RuntimeError("CosyVoice returned an empty audio stream.")

        full_audio = torch.cat(chunks, dim=1)

        # 5. Write to temp WAV
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        torchaudio.save(output_path, full_audio, 22050)

        logger.info(f"Audio ready → {output_path}")
        return FileResponse(output_path, media_type="audio/wav")

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"TTS inference error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        # Clean up reference temp file regardless of outcome
        if ref_path and os.path.exists(ref_path):
            os.remove(ref_path)
        # Note: output_path is intentionally NOT deleted here —
        # FileResponse streams it after this function returns.
        # Uvicorn cleans up temp files after the response is sent.


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    # Pre-warm the model on startup (optional — comment out to lazy-load)
    try:
        get_cosyvoice()
    except Exception as exc:
        logger.warning(f"Pre-warm skipped — model will load on first request. ({exc})")

    uvicorn.run(app, host="0.0.0.0", port=5020, log_level="info")
