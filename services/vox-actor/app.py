"""
vox-actor — CosyVoice 3 TTS Service
Platform: AMD ROCm (gfx1201 / RDNA3)
Engine:   CosyVoice 3 (zero-shot voice cloning via Qwen2.5-0.5B + DiT)
Port:     5020

Performance optimisations:
  - Model loaded on first request; kept warm for subsequent calls.
  - MIOpen kernel warm-up on startup via dummy inference.
  - In-memory BytesIO buffers for audio I/O (diskless pipeline).
"""

import sys
import os
import io
import torch
import torchaudio
import soundfile as sf
import tempfile
import logging
import gc
import time
import asyncio
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Response
from fastapi.responses import FileResponse, JSONResponse

os.environ["MIOPEN_FIND_MODE"] = "2"

# Add CosyVoice package path (cloned at build time from GitHub)
_cosyvoice_dir = os.getenv("COSYVOICE_PACKAGE_DIR", "/app/CosyVoice")
_matcha_dir = os.path.join(_cosyvoice_dir, "third_party", "Matcha-TTS")
for _p in [_cosyvoice_dir, _matcha_dir]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Monkeypatch: torchaudio in 2026 forces torchcodec, which is broken on ROCm
# ---------------------------------------------------------------------------
def _monkeypatch_torchaudio_load(uri, **kwargs):
    # Simple soundfile-based fallback
    if isinstance(uri, io.BytesIO):
        uri.seek(0)
    data, samplerate = sf.read(uri, dtype='float32')
    tensor = torch.from_numpy(data)
    if tensor.ndim == 1:
        tensor = tensor.unsqueeze(0)
    else:
        tensor = tensor.T
    return tensor, samplerate

def _monkeypatch_torchaudio_save(uri, tensor, sample_rate, **kwargs):
    # soundfile expects [time, channels]
    if tensor.ndim > 2:
        tensor = tensor.squeeze()
        if tensor.ndim == 1:
            tensor = tensor.unsqueeze(0)
            
    if tensor.ndim == 2:
        data = tensor.T.cpu().numpy()
    else:
        data = tensor.cpu().numpy()
        
    # If uri is a file-like object (BytesIO), soundfile needs an explicit format
    if hasattr(uri, 'write'):
        sf.write(uri, data, sample_rate, format='WAV')
    else:
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


async def _prewarm_model():
    """Run a dummy inference to warm up GPU kernels and trigger any necessary downloads."""
    try:
        model = _load_model()
        logger.info("🔥 Pre-warming CosyVoice 3 models...")
        
        # Create a dummy 1-second silence WAV for pre-warming
        dummy_wav = io.BytesIO()
        import numpy as np
        sf.write(dummy_wav, np.zeros(22050), 22050, format='WAV')
        dummy_wav.seek(0)
        
        # Run dummy zero-shot inference
        for _ in model.inference_zero_shot(
            tts_text="Pre-warming engine.",
            prompt_text="A clear speaking voice.<|endofprompt|>Dummy sample.",
            prompt_wav=dummy_wav,
            stream=False
        ):
            pass
        logger.info("✅ CosyVoice 3 pre-warming complete.")
    except Exception as e:
        logger.warning(f"⚠️ Pre-warming failed (expected on cold start/no models): {e}")


def _evict_model():
    """Unload model from VRAM."""
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

@app.on_event("startup")
async def startup_event():
    # Pre-warm in background to not block startup
    asyncio.create_task(_prewarm_model())

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
    _evict_model()
    return {"status": "ok", "message": "GPU cache cleared"}


@app.post("/api/tts")
async def text_to_speech(
    text: str = Form(...),
    reference_audio: UploadFile = File(...),
    prompt_text: str = Form(""),
    emotion: str = Form("default"),
    mode: str = Form("zero_shot"),
):
    """TTS via CosyVoice 3 with in-memory audio processing."""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")

    model = _load_model()

    try:
        ref_bytes = await reference_audio.read()
        ref_buf = io.BytesIO(ref_bytes)

        if not prompt_text.strip():
            prompt_text = "You are a helpful assistant.<|endofprompt|>This is a voice sample for character speech."
        elif "<|endofprompt|>" not in prompt_text:
            if emotion and emotion != "default":
                prompt_text = f"Deliver the following speech with a {emotion} tone.<|endofprompt|>{prompt_text}"
            else:
                prompt_text = f"Deliver in the speaker's natural voice.<|endofprompt|>{prompt_text}"

        logger.info(f"CosyVoice 3 ({mode}): text='{text[:60]}...' ref={len(ref_bytes)} bytes")

        if mode == "instruct2":
            result = _run_instruct2(model, text, prompt_text, ref_buf)
        else:
            result = _run_zero_shot(model, text, prompt_text, ref_buf)

        if result is None or "tts_speech" not in result:
            raise HTTPException(status_code=500, detail="CosyVoice 3 returned no output")

        audio_tensor = result["tts_speech"].cpu()
        sample_rate = model.sample_rate

        # Return as Response from memory
        out_buf = io.BytesIO()
        torchaudio.save(out_buf, audio_tensor, sample_rate)
        
        logger.info(f"CosyVoice 3 done ({audio_tensor.shape} samples @ {sample_rate} Hz)")
        return Response(content=out_buf.getvalue(), media_type="audio/wav")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CosyVoice 3 inference error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/voice-design")
async def voice_design(
    text: str = Form(...),
    instruct_text: str = Form(...),
    reference_audio: UploadFile = File(...),
):
    if not text.strip():
        raise HTTPException(status_code=400, detail="Empty text")
    if not instruct_text.strip():
        raise HTTPException(status_code=400, detail="Empty instruct_text")

    model = _load_model()

    try:
        ref_bytes = await reference_audio.read()
        ref_buf = io.BytesIO(ref_bytes)

        if "<|endofprompt|>" not in instruct_text:
            instruct_text = f"{instruct_text}<|endofprompt|>This is a voice design sample."

        result = _run_instruct2(model, text, instruct_text, ref_buf)

        if result is None or "tts_speech" not in result:
            raise HTTPException(status_code=500, detail="CosyVoice 3 voice-design returned no output")

        audio_tensor = result["tts_speech"].cpu()
        sample_rate = model.sample_rate

        out_buf = io.BytesIO()
        torchaudio.save(out_buf, audio_tensor, sample_rate)
        
        logger.info(f"CosyVoice 3 voice-design done ({audio_tensor.shape} samples @ {sample_rate} Hz)")
        return Response(content=out_buf.getvalue(), media_type="audio/wav")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CosyVoice 3 voice-design error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _run_zero_shot(model, tts_text: str, prompt_text: str, prompt_wav: Any) -> dict | None:
    result = None
    for i, j in enumerate(
        model.inference_zero_shot(
            tts_text=tts_text,
            prompt_text=prompt_text,
            prompt_wav=prompt_wav,
            stream=False,
        )
    ):
        result = j
    return result


def _run_instruct2(model, tts_text: str, instruct_text: str, prompt_wav: Any) -> dict | None:
    if not hasattr(model, "inference_instruct2"):
        return _run_zero_shot(model, tts_text, instruct_text, prompt_wav)

    result = None
    for i, j in enumerate(
        model.inference_instruct2(
            tts_text=tts_text,
            instruct_text=instruct_text,
            prompt_wav=prompt_wav,
            stream=False,
        )
    ):
        result = j
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5020)
