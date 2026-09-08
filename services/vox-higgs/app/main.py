"""vox-higgs — Boson AI Higgs TTS 2 (higgs-tts-2-3b-base) on ROCm.
Llama backbone + DualFFN audio adapter; LLM/backbone linears INT8 by
default (audio codec embeddings/decoder heads stay high precision).
Transformers chat-template API: system/scene/user + per-speaker reference
audio (voice seeds) in the scene role -> WAV 24 kHz.
"""
import gc
import io
import os
import time
import re
import subprocess
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel

app = FastAPI(title="vox-higgs")

SKIP_SUBSTR = [s for s in os.getenv("HIGGS_SKIP_QUANT", "audio_codebook,audio_decoder,audio_encoder,audio_lm_head").split(",") if s]
SEED_DIR = "/app/voice_seeds"
MODEL_ID = os.getenv("HIGGS_MODEL", "/app/weights/hub/models--bosonai--higgs-tts-2-3b-base")

MODEL = None
PROC = None
_gen_lock = None
QSTATS = None

# ---- RAM seed holder (owner 2026-09-07: hold the working chapter's seeds
# ---- in RAM so every scene reuses decoded reference audio instead of
# ---- re-fetching + re-decoding per request).  Seeds decode to 24 kHz mono
# ---- float32 (the processor's sampling_rate) once, then serve every scene.
SEED_PCM: dict[str, np.ndarray] = {}
SEED_CACHE_MAX = 256


def _decode_seed_pcm(name: str) -> np.ndarray | None:
    """Decode a seed wav to 24k mono float32 via ffmpeg (no extra deps)."""
    p = os.path.join(SEED_DIR, os.path.basename(name))
    if not os.path.isfile(p):
        return None
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", p,
             "-ac", "1", "-ar", "24000", "-f", "f32le", "-"],
            check=True, capture_output=True,
        )
        arr = np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)
        return arr if arr.size else None
    except (subprocess.CalledProcessError, OSError):
        return None


def seed_pcm(name: str) -> np.ndarray | None:
    """RAM-cached PCM for a seed (lazy decode on first use; bounded)."""
    base = os.path.basename(name)
    arr = SEED_PCM.get(base)
    if arr is None:
        arr = _decode_seed_pcm(base)
        if arr is not None:
            SEED_PCM[base] = arr
            while len(SEED_PCM) > SEED_CACHE_MAX:  # FIFO bound
                SEED_PCM.pop(next(iter(SEED_PCM)))
    return arr


class WarmReq(BaseModel):
    paths: list[str]           # seed file names or full paths (basename used)


@app.post("/seeds/warm")
async def seeds_warm(req: WarmReq):
    """Preload the working set of seeds into RAM (call once per chapter)."""
    loaded = missing = 0
    for p in req.paths:
        base = os.path.basename(p)
        if seed_pcm(base) is not None:
            loaded += 1
        else:
            missing += 1
    return {"warm": loaded, "missing": missing,
            "ram_seeds": len(SEED_PCM)}


@app.get("/seeds/cache")
def seeds_cache():
    total = sum(a.nbytes for a in SEED_PCM.values())
    return {"cached": sorted(SEED_PCM), "count": len(SEED_PCM),
            "ram_mib": round(total / 2**20, 1)}


@app.post("/seeds/evict")
async def seeds_evict(req: WarmReq):
    """Drop seeds from RAM (chapter switch). Empty paths = clear all."""
    if not req.paths:
        n = len(SEED_PCM)
        SEED_PCM.clear()
        return {"evicted": n}
    n = 0
    for p in req.paths:
        n += SEED_PCM.pop(os.path.basename(p), None) is not None
    return {"evicted": n}


class Int8Linear(nn.Module):
    def __init__(self, lin: nn.Linear):
        super().__init__()
        w = lin.weight.detach()
        s = w.abs().amax(dim=1, keepdim=True).clamp(min=1e-8) / 127.0
        self.register_buffer("w8", (w / s).round().clamp(-128, 127).to(torch.int8))
        self.register_buffer("scale", s.to(torch.float32))
        self.bias = lin.bias

    def forward(self, x):
        w = (self.w8.to(torch.float32) * self.scale).to(x.dtype)
        return F.linear(x, w, self.bias)


def quantize_engine(model, skip=()):
    n = skipped = 0
    for name, child in list(model.named_modules()):
        if name == "" or not isinstance(child, nn.Linear):
            continue
        if any(s in name for s in skip):
            skipped += 1
            continue
        parent = model
        parts = name.split(".")
        for p in parts[:-1]:
            parent = parent._modules[p]
        parent._modules[parts[-1]] = Int8Linear(child)
        n += 1
    gc.collect()
    return n, skipped


def _patch_seed_load_audio():
    """Route /seeds/<name> reference fetches through the RAM seed holder.

    The Higgs processor resolves scene 'audio' content with
    transformers.processing_utils.load_audio; when the URL points at our own
    /seeds endpoint and the seed is cached (warm or lazy), return the PCM
    array straight from RAM — no HTTP round trip, no per-scene decode.
    """
    import transformers.processing_utils as _pu
    if getattr(_pu, "_cinematome_seed_holder_patched", False):
        return
    _orig = _pu.load_audio

    def _load_audio_ram(audio, sampling_rate=16000, timeout=None, backend="auto"):
        if isinstance(audio, str) and "/seeds/" in audio:
            arr = seed_pcm(os.path.basename(audio.split("/seeds/", 1)[1]))
            if arr is not None:
                return arr
        return _orig(audio, sampling_rate=sampling_rate, timeout=timeout,
                     backend=backend)

    _pu.load_audio = _load_audio_ram
    _pu._cinematome_seed_holder_patched = True
    print("[vox-higgs] seed RAM holder active (load_audio patched)", flush=True)


@app.on_event("startup")
def _load():
    global MODEL, PROC, _gen_lock, QSTATS
    import asyncio
    _gen_lock = asyncio.Lock()
    from transformers import AutoProcessor, HiggsAudioV2ForConditionalGeneration
    _patch_seed_load_audio()
    t0 = time.time()
    PROC = AutoProcessor.from_pretrained(MODEL_ID)
    MODEL = HiggsAudioV2ForConditionalGeneration.from_pretrained(
        MODEL_ID, torch_dtype=torch.bfloat16, device_map=None)
    MODEL = MODEL.to("cuda").eval()
    print(f"[vox-higgs] model loaded in {time.time()-t0:.0f}s — bf16", flush=True)
    # top-level structure log for quant scope tuning
    print("[vox-higgs] top modules:", [n for n, _ in list(MODEL.named_children())][:12], flush=True)
    if os.getenv("HIGGS_INT8", "1") != "0":
        n, skipped = quantize_engine(MODEL, skip=SKIP_SUBSTR)
        QSTATS = (n, skipped, SKIP_SUBSTR)
        print(f"[vox-higgs] INT8: {n} Linear quantized, {skipped} skipped "
              f"(keep-high-precision: {SKIP_SUBSTR})", flush=True)
    if torch.cuda.is_available():
        print(f"[vox-higgs] VRAM used: {torch.cuda.memory_allocated()/2**30:.2f} GiB "
              f"of {torch.cuda.get_device_properties(0).total_memory/2**30:.1f} GiB", flush=True)


class GenReq(BaseModel):
    scene: str = "Audio is recorded from a quiet room."
    refs: dict[str, str] = {}      # {"SPEAKER0": "/app/voice_seeds/x.wav", ...}
    text: str                       # dialogue with [SPEAKER0]/[SPEAKER1] tags
    max_new_tokens: int = 2200
    do_sample: bool = False


@app.get("/health")
def health():
    return {
        "model": "Higgs TTS 2 (3B base)", "loaded": MODEL is not None,
        "int8": QSTATS, "vram_gib": round(torch.cuda.memory_allocated()/2**30, 2),
    }


@app.get("/seeds/{name}")
def seed_file(name: str):
    p = os.path.join(SEED_DIR, os.path.basename(name))
    if not os.path.exists(p):
        raise HTTPException(404, "seed not found")
    return FileResponse(p, media_type="audio/wav")


@app.post("/generate")
async def generate(req: GenReq):
    async with _gen_lock:
        t0 = time.time()
        conv = [
            {"role": "system", "content": [{"type": "text", "text":
                "You are an AI assistant designed to convert text into speech. "
                "If the user's message includes a [SPEAKER*] tag, do not read out the tag and "
                "generate speech for the following text, using the specified voice. "
                "If no speaker tag is present, select a suitable voice on your own."}]},
            {"role": "scene", "content": [{"type": "text", "text": req.scene}]},
        ]
        for tag, path in req.refs.items():
            name = os.path.basename(path)
            conv[1]["content"].append({"type": "text", "text": f"{tag}:"})
            conv[1]["content"].append({"type": "audio",
                                       "url": f"http://127.0.0.1:8024/seeds/{name}"})
        conv.append({"role": "user", "content": [{"type": "text", "text": req.text}]})

        inputs = PROC.apply_chat_template(
            conv, add_generation_prompt=True, tokenize=True, return_dict=True,
            sampling_rate=24000, return_tensors="pt").to(MODEL.device)
        with torch.no_grad():
            out = MODEL.generate(**inputs, max_new_tokens=req.max_new_tokens,
                                 do_sample=req.do_sample)
        wav = PROC.batch_decode(out)[0]
        dur = wav.shape[-1] / 24000
        peak = float(np.abs(wav).max()) if hasattr(wav, "numpy") else 1.0
        if peak > 0.95:
            wav = wav / peak * 0.95
        buf = io.BytesIO()
        PROC.save_audio(buf, wav)
        print(f"[vox-higgs] {dur:.2f}s clip in {time.time()-t0:.1f}s", flush=True)
        return Response(content=buf.getvalue(), media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8024)
