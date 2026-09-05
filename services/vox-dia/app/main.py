"""vox-dia — Nari Labs Dia-1.6B on ROCm.
Engine (encoder+decoder) int8 weight-only by default; Descript DAC stays
unquantized (fp32). One model instance, serialized generation.
POST /generate {text, seed?, max_tokens?, cfg_scale?, temperature?, top_p?}
  -> WAV (44.1 kHz, 16-bit, normalized < 0.95 so PCM never clamps).
"""
import gc
import io
import os
import time
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import soundfile as sf
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import Response

import torchaudio


def _load_audio_sf(path, channels_first=True):
    data, sr = sf.read(path, dtype="float32")
    wav = torch.from_numpy(data.T if data.ndim > 1 else data[None, :])
    return wav, sr


torchaudio.load = _load_audio_sf  # no torchcodec dependency

app = FastAPI(title="vox-dia")


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


def _quantize_kernel(t, in_ndim):
    in_numel = 1
    for s in t.shape[:in_ndim]:
        in_numel *= s
    n_out = 1
    for s in t.shape[in_ndim:]:
        n_out *= s
    mat = t.reshape(in_numel, n_out)
    scale = mat.abs().amax(dim=0, keepdim=True).clamp(min=1e-8) / 127.0
    q = (mat / scale).round().clamp(-128, 127).to(torch.int8).reshape(t.shape)
    scale_view = scale.reshape((1,) * in_ndim + tuple(t.shape[in_ndim:]))
    return q, scale_view


def quantize_engine(model):
    import types
    from dia.layers import _normalize_axes

    n_lin = n_dg = total = 0
    for name, child in list(model.named_modules()):
        if name == "":
            continue
        parent = model
        parts = name.split(".")
        for p in parts[:-1]:
            parent = parent._modules[p]
        if isinstance(child, nn.Linear):
            parent._modules[parts[-1]] = Int8Linear(child)
            n_lin += 1
            total += child.weight.numel()
        elif child.__class__.__name__ == "DenseGeneral":
            in_ndim = len(child.in_shapes)
            t = child.weight.detach()
            q, sv = _quantize_kernel(t, in_ndim)
            child.register_buffer("w8", q)
            child.register_buffer("_scale_view", sv)
            del child._parameters["weight"]

            def qfwd(self, inputs):
                w = (self.w8.to(torch.float32) * self._scale_view).to(inputs.dtype)
                norm_axis = _normalize_axes(self.axis, inputs.ndim)
                ker_axes = tuple(range(len(norm_axis)))
                return torch.tensordot(inputs.to(w.dtype), w, dims=(norm_axis, ker_axes)).to(inputs.dtype)

            child.forward = types.MethodType(qfwd, child)
            n_dg += 1
            total += t.numel()
    gc.collect()
    return n_lin, n_dg, total


# ---------------- model load ----------------
INT8 = os.getenv("DIA_INT8", "1") != "0"
DIA = None
_gen_lock = None
INT8_STATS = None


@app.on_event("startup")
def _load():
    global DIA, _gen_lock, INT8_STATS
    import asyncio
    _gen_lock = asyncio.Lock()
    t0 = time.time()
    from dia.model import Dia
    dia = Dia.from_pretrained(
        "nari-labs/Dia-1.6B-0626",
        compute_dtype="float16",
        device=torch.device("cuda"),
    )
    print(f"[vox-dia] model loaded in {time.time()-t0:.0f}s", flush=True)
    if INT8:
        n_lin, n_dg, total = quantize_engine(dia.model)
        INT8_STATS = (n_lin, n_dg, total)
        print(f"[vox-dia] engine int8: {n_lin} Linear + {n_dg} DenseGeneral "
              f"({total/1e9:.2f}B params) — DAC fp32 untouched", flush=True)
    DIA = dia
    if torch.cuda.is_available():
        print(f"[vox-dia] VRAM used: {torch.cuda.memory_allocated()/2**30:.2f} GiB "
              f"of {torch.cuda.get_device_properties(0).total_memory/2**30:.1f} GiB", flush=True)


class GenReq(BaseModel):
    text: str
    seed: str = ""
    max_tokens: int = 1024
    cfg_scale: float = 3.0
    temperature: float = 1.2
    top_p: float = 0.95
    cfg_filter_top_k: int = 45


@app.get("/health")
def health():
    return {
        "model": "Dia-1.6B-0626",
        "loaded": DIA is not None,
        "int8": INT8,
        "int8_stats": INT8_STATS,
        "vram_gib": round(torch.cuda.memory_allocated() / 2**30, 2) if torch.cuda.is_available() else None,
    }


@app.post("/generate")
async def generate(req: GenReq):
    async with _gen_lock:
        t0 = time.time()
        audio = DIA.generate(
            req.text,
            audio_prompt=req.seed or None,
            max_tokens=req.max_tokens,
            use_torch_compile=False,
            verbose=False,
            cfg_scale=req.cfg_scale,
            temperature=req.temperature,
            top_p=req.top_p,
            cfg_filter_top_k=req.cfg_filter_top_k,
        )
        dur = len(audio) / 44100
        peak = float(np.abs(audio).max())
        if peak > 0.95:
            audio = audio * (0.95 / peak)
        buf = io.BytesIO()
        sf.write(buf, audio, 44100, format="WAV", subtype="PCM_16")
        print(f"[vox-dia] {dur:.2f}s clip in {time.time()-t0:.1f}s "
              f"({dur/(time.time()-t0):.2f}x realtime)", flush=True)
        return Response(content=buf.getvalue(), media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8014)
