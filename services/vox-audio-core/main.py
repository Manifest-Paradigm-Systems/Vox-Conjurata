from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, Generator
import os
import time
import json
import soundfile as sf
import numpy as np
import io
import struct
import torch
import gc
import asyncio
from voxcpm import VoxCPM
from pedalboard import Pedalboard, PitchShift, Distortion, Chorus, Reverb, HighpassFilter
import logging

logging.basicConfig(level=logging.INFO)
from torchao.quantization import quantize_, Int8WeightOnlyConfig
from voxcpm.core import next_and_close
import re

logger = logging.getLogger("vox-audio-core")

app = FastAPI(title="Vox Conjurata Core Audio Engine")

# Serialize seed generation to prevent GPU OOM from concurrent model inference
_seed_lock = asyncio.Lock()

last_used_time = time.time()

def update_last_used():
    global last_used_time
    last_used_time = time.time()

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(vram_flusher_loop())

async def vram_flusher_loop():
    global last_used_time
    logger.info("🧹 VRAM Flusher background loop started.")
    while True:
        await asyncio.sleep(15)
        if time.time() - last_used_time > 60:
            if torch.cuda.is_available():
                before = torch.cuda.memory_reserved()
                torch.cuda.empty_cache()
                gc.collect()
                after = torch.cuda.memory_reserved()
                if before > after:
                    logger.info(f"🧹 VRAM Flusher: Cleaned PyTorch cache. Freed {(before - after)/1024**2:.2f} MB. Reserved: {after/1024**2:.2f} MB")

# Enable TF32 for much faster matrix multiplication on supported GPUs
torch.set_float32_matmul_precision('high')

# Initialize the 2B Tokenizer-Free Diffusion Model (bfloat16)
# Set load_denoiser=False to keep VRAM at exactly 4.2 GB
logger.info("Loading VoxCPM2 model...")
# Load in eager mode to prevent long Inductor compilation overhead of quantized weights
vox_engine = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False, optimize=False)
logger.info("Model loaded.")

SEED_DIR = os.getenv("SEED_DIR", "/app/seeds/")
os.makedirs(SEED_DIR, exist_ok=True)

_prompt_cache = {}

def get_or_create_prompt_cache(seed_path: str):
    if seed_path not in _prompt_cache:
        logger.info(f"Building prompt cache for: {seed_path}")
        prompt_cache = vox_engine.tts_model.build_prompt_cache(reference_wav_path=seed_path)
        _prompt_cache[seed_path] = prompt_cache
    return _prompt_cache[seed_path]

def _compute_max_len(dialogue_text: str) -> int:
    """
    Compute a tight upper-bound on audio patch generation length based on the
    spoken word count alone (excluding any control instruction prefix).

    VoxCPM2 produces ~4-6 audio patches per spoken word at 8 words/chunk.  We
    use a multiplier of 6 (measured empirically) plus a fixed 18-patch buffer
    so the model can finish naturally but cannot run to its 2000-step default.

    Examples for 8-word chunks:
      8 words * 6 + 18 = 66  steps  (~6s at ~11 it/s)
      4 words * 6 + 18 = 42  steps  (~4s)
    """
    word_count = max(1, len(dialogue_text.split()))
    return max(22, word_count * 6 + 18)


def generate_with_cache(
    text: str,
    seed_path: str,
    dialogue_text: str = "",
    inference_timesteps: int = 8,
    cfg_value: float = 2.0,
    max_retries: int = 2
) -> np.ndarray:
    t_start = time.time()

    text = (dialogue_text or text).replace("\n", " ")
    text = re.sub(r"\s+", " ", text)

    arr = None
    for attempt in range(max_retries + 1):
        # Vary cfg_value slightly on retry to escape NaN path
        retry_cfg = cfg_value + (attempt * 0.5)
        t_gen_start = time.time()
        wav = vox_engine.generate(
            text=text,
            cfg_value=retry_cfg,
            inference_timesteps=inference_timesteps,
            retry_badcase=True,
            retry_badcase_max_times=1,       # Model needs ≥1 for internal var init
            retry_badcase_ratio_threshold=50.0  # Very high to prevent false positives on short TTS
        )
        t_gen = time.time() - t_gen_start
        arr = wav

        nan_count = np.isnan(arr).sum()
        # Valid audio if <5% NaN samples (tolerate sparse NaN)
        if nan_count <= max(1, arr.size // 20):
            if nan_count > 0:
                arr = np.nan_to_num(arr)
            logger.info(f"⏱️  [vox-audio-core] Generate OK (attempt {attempt+1}): {t_gen:.4f}s | {text[:60]}")
            break
        else:
            logger.warning(f"⚠️ Attempt {attempt+1}/{max_retries+1} NaN ({nan_count}/{arr.size}) — clearing cache and retrying with cfg={retry_cfg:.1f}")
            # Clear GPU cache to recover from corrupted state
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                gc.collect()

    if arr is None or np.isnan(arr).sum() > max(1, arr.size // 20):
        logger.error(f"❌ All {max_retries+1} attempts failed (NaN). Raising error.")
        raise RuntimeError(f"Voice generation failed: all {max_retries+1} attempts produced NaN (ROCm instability)")

    logger.info(f"⏱️  [vox-audio-core] Total: {time.time() - t_start:.4f}s")
    return arr

def generate_stream_with_cache(
    text: str,
    seed_path: str,
    dialogue_text: str = "",
    inference_timesteps: int = 8,
    cfg_value: float = 2.0
) -> Generator[np.ndarray, None, None]:
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)

    # Streaming via _generate with streaming=True
    # Retry once if initial chunks are NaN
    for attempt in range(2):
        gen = vox_engine._generate(
            text=text,
            inference_timesteps=inference_timesteps,
            cfg_value=cfg_value,
            retry_badcase=True,
            retry_badcase_max_times=1,
            retry_badcase_ratio_threshold=50.0,
            streaming=True
        )
        chunk_count = 0
        nan_count = 0
        try:
            for wav in gen:
                arr = wav.squeeze(0).cpu().numpy()
                chunk_count += 1
                if np.isnan(arr).sum() > max(1, arr.size // 100):
                    nan_count += 1
                    logger.warning("⚠️ Streaming chunk dropped (NaN)")
                    continue
                yield arr
        finally:
            gen.close()
        # If all chunks were NaN and we haven't exhausted retries, try once more
        if chunk_count > 0 and nan_count == chunk_count and attempt == 0:
            logger.warning("⚠️ All streaming chunks NaN — retrying...")
            time.sleep(0.5)
            continue
        break

class NPCIdentityRequest(BaseModel):
    npc_id: str
    voice_description: str

class DialogueRequest(BaseModel):
    npc_id: str
    dialogue_text: str
    dsp_presets: dict
    control_instruction: Optional[str] = None


def build_voxcpm_text(dialogue_text: str, control_instruction: Optional[str]) -> str:
    """
    VoxCPM2 consumes acting instructions via a (instruction)text prefix.
    This mirrors the CLI's build_final_text() helper.
    When no instruction is given, the text is used as-is.
    """
    if control_instruction and control_instruction.strip():
        return f"({control_instruction.strip()}){dialogue_text}"
    return dialogue_text


def apply_dsp(audio: np.ndarray, sample_rate: int, fx: dict) -> np.ndarray:
    """Assemble and run C++ Pedalboard DSP effects. Returns processed audio."""
    rack = []
    if fx.get("pitch_shift", 0) != 0:
        rack.append(PitchShift(semitones=fx["pitch_shift"]))
    if fx.get("distortion_db", 0) > 0:
        rack.append(Distortion(drive_db=fx["distortion_db"]))
    if fx.get("highpass_hz", 0) > 0:
        rack.append(HighpassFilter(cutoff_frequency_hz=fx["highpass_hz"]))
    if fx.get("chorus_depth", 0) > 0:
        rack.append(Chorus(rate_hz=fx.get("chorus_rate", 1.5), depth=fx["chorus_depth"]))
    if fx.get("reverb_size", 0) > 0:
        rack.append(Reverb(room_size=fx["reverb_size"], wet_level=0.3, dry_level=0.7))
    if rack:
        return Pedalboard(rack)(audio, sample_rate)
    return audio


def encode_wav_header(sample_rate: int, num_channels: int = 1, bits_per_sample: int = 32) -> bytes:
    """
    Returns a WAV header with an unknown data chunk size (0xFFFFFFFF).
    Required for streaming mode where total length is unknown upfront.
    """
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    # Use 0xFFFFFFFF for unknown chunk sizes (streaming convention)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 0xFFFFFFFF,          # RIFF chunk (unknown total size)
        b"WAVE",
        b"fmt ", 18,                  # fmt subchunk size
        3,                            # PCM Float audio format
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,              # 32-bit float
        b"data", 0xFFFFFFFF,         # data chunk (unknown size)
    )
    return header

@app.post("/initialize")
async def initialize_npc(request: NPCIdentityRequest):
    """
    PHASE 1: THE BIRTH CLIP.
    Executes once per NPC to create a deterministic phonetic anchor.
    Idempotent — returns immediately if seed already exists for this NPC.
    Serialized globally via _seed_lock to prevent GPU OOM from concurrent generation.
    """
    update_last_used()
    seed_path = os.path.join(SEED_DIR, f"{request.npc_id}.wav")

    # Idempotency seed already exists skip generation
    if os.path.exists(seed_path):
        logger.info(f"Seed already exists for NPC {request.npc_id} — skipping")
        return {"status": "success", "seed_path": seed_path, "cached": True}

    async with _seed_lock:
        # Double-check after acquiring lock a concurrent caller may have just finished
        if os.path.exists(seed_path):
            logger.info(f"Seed already exists for NPC {request.npc_id} (after lock) — skipping")
            return {"status": "success", "seed_path": seed_path, "cached": True}

        try:
            # Use VoxCPM2 Voice Design block notation
            birth_prompt = f"({request.voice_description}) System voice alignment sequence active. Timbre matrix locked."

            logger.info(f"Generating birth clip for NPC: {request.npc_id}")
            raw_wav = vox_engine.generate(
                text=birth_prompt,
                cfg_value=2.5,          # Forces model adherence to text criteria
                inference_timesteps=8
            )

            # NaN guard abort if the birth clip is unusable
            if np.isnan(raw_wav).sum() > max(1, raw_wav.size // 100):
                logger.error(f"Birth clip for {request.npc_id} is {np.isnan(raw_wav).sum()}/{raw_wav.size} NaN aborting")
                raise HTTPException(status_code=500, detail="Voice generation failed (NaN output retry later)")

            sf.write(seed_path, raw_wav, vox_engine.tts_model.sample_rate)
            return {"status": "success", "seed_path": seed_path, "cached": False}
        except Exception as e:
            logger.error(f"Failed to initialize NPC {request.npc_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate")
async def generate_audio(request: DialogueRequest):
    """
    PHASE 2: LIVE RUNTIME.
    Processes dynamic sentences using the locked seed file,
    then applies mathematical DSP filters for custom monstrous textures.
    """
    try:
        update_last_used()
        # Check standard seed path
        seed_path = os.path.join(SEED_DIR, f"{request.npc_id}.wav")
        # Check palette fallback for system archetypes and narrator
        palette_path = os.path.join(SEED_DIR, "_palette", f"{request.npc_id}.wav")

        final_seed = None
        if os.path.exists(seed_path):
            final_seed = seed_path
        elif os.path.exists(palette_path):
            final_seed = palette_path

        if not final_seed:
            raise HTTPException(status_code=404, detail=f"Seed for NPC {request.npc_id} not found. Checked: {seed_path} and {palette_path}")

        seed_path = final_seed # For the rest of the function

        sample_rate = vox_engine.tts_model.sample_rate  # Native 48000Hz

        # Build the final text with control instruction prefix: (instruction)text
        final_text = build_voxcpm_text(request.dialogue_text, request.control_instruction)
        logger.info(f"Generating dialogue for NPC: {request.npc_id} | text: '{final_text[:80]}...'")

        # Execute high-fidelity cloning using cached prompt and no retry.
        # Pass dialogue_text separately so max_len is computed from spoken words only.
        clean_audio = generate_with_cache(
            text=final_text,
            seed_path=seed_path,
            dialogue_text=request.dialogue_text,
            inference_timesteps=6
        )

        logger.info(f"📊 [vox-audio-core] clean_audio: NaN count={np.isnan(clean_audio).sum()}, min={np.min(clean_audio)}, max={np.max(clean_audio)}")

        # Apply C++ Pedalboard DSP (monster textures, pitch shift, etc.)
        output_audio = apply_dsp(clean_audio, sample_rate, request.dsp_presets)

        # Normalize volume: boost quiet direct-generate output to usable levels
        peak = np.max(np.abs(output_audio))
        if peak > 0 and peak < 0.5:
            gain = min(0.95 / peak, 10.0)  # target -0.4 dBFS, cap at 10x
            output_audio = output_audio * gain
            logger.info(f"📈 [vox-audio-core] Volume normalized: peak {peak:.4f} → {np.max(np.abs(output_audio)):.4f} (gain {gain:.1f}x)")

        logger.info(f"📊 [vox-audio-core] output_audio: NaN count={np.isnan(output_audio).sum()}, min={np.min(output_audio)}, max={np.max(output_audio)}")

        # Return native WAV — no transcoding overhead
        buffer = io.BytesIO()
        sf.write(buffer, output_audio, sample_rate, format='WAV')
        

        
        return Response(content=buffer.getvalue(), media_type="audio/wav")

    except Exception as e:
        logger.error(f"Failed to generate audio for NPC {request.npc_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate_stream")
async def generate_audio_stream(request: DialogueRequest):
    """
    STREAMING MODE: Yields audio as float32 WAV chunks the moment
    VoxCPM2 produces them. Time-to-first-audio < 3 seconds.
    The browser can play these chunks using a MediaSource + AudioContext.
    """
    try:
        update_last_used()
        seed_path = os.path.join(SEED_DIR, f"{request.npc_id}.wav")
        palette_path = os.path.join(SEED_DIR, "_palette", f"{request.npc_id}.wav")

        final_seed = None
        if os.path.exists(seed_path):
            final_seed = seed_path
        elif os.path.exists(palette_path):
            final_seed = palette_path

        if not final_seed:
            raise HTTPException(status_code=404, detail=f"Seed for NPC {request.npc_id} not found.")

        sample_rate = vox_engine.tts_model.sample_rate
        final_text = build_voxcpm_text(request.dialogue_text, request.control_instruction)
        logger.info(f"STREAM: Generating dialogue for NPC: {request.npc_id} | text: '{final_text[:80]}...'")
        # Capture dialogue_text for the closure
        _dialogue_text = request.dialogue_text

        def wav_chunk_generator() -> Generator[bytes, None, None]:
            # Emit the WAV header first so the browser knows the format
            yield encode_wav_header(sample_rate)

            for chunk in generate_stream_with_cache(
                text=final_text,
                seed_path=final_seed,
                dialogue_text=_dialogue_text,
                inference_timesteps=4,
            ):
                # Apply DSP on each chunk (Pedalboard is ~1ms per chunk)
                processed = apply_dsp(chunk, sample_rate, request.dsp_presets)
                # Pack as raw float32 little-endian PCM samples
                yield processed.astype(np.float32).tobytes()

        return StreamingResponse(
            wav_chunk_generator(),
            media_type="audio/wav",
            headers={"X-Accel-Buffering": "no"},  # Disable proxy buffering
        )

    except Exception as e:
        logger.error(f"Failed to stream audio for NPC {request.npc_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cache/warm")
async def warm_cache(request: NPCIdentityRequest):
    """Warm prompt cache for a specific NPC."""
    try:
        update_last_used()
        seed_path = os.path.join(SEED_DIR, f"{request.npc_id}.wav")
        palette_path = os.path.join(SEED_DIR, "_palette", f"{request.npc_id}.wav")

        final_seed = None
        if os.path.exists(seed_path):
            final_seed = seed_path
        elif os.path.exists(palette_path):
            final_seed = palette_path

        if not final_seed:
            raise HTTPException(status_code=404, detail=f"Seed for NPC {request.npc_id} not found.")

        get_or_create_prompt_cache(final_seed)
        

        
        return {"status": "success", "message": f"Cache warmed for {request.npc_id}"}
    except Exception as e:
        logger.error(f"Failed to warm cache for NPC {request.npc_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
