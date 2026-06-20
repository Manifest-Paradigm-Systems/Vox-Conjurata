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
from voxcpm import VoxCPM
from pedalboard import Pedalboard, PitchShift, Distortion, Chorus, Reverb, HighpassFilter
import logging

logging.basicConfig(level=logging.INFO)
from torchao.quantization import quantize_, Int8WeightOnlyConfig
from voxcpm.core import next_and_close
import re

logger = logging.getLogger("vox-audio-core")

app = FastAPI(title="Vox Conjurata Core Audio Engine")

# Enable TF32 for much faster matrix multiplication on supported GPUs
torch.set_float32_matmul_precision('high')

# Initialize the 2B Tokenizer-Free Diffusion Model (bfloat16)
# Set load_denoiser=False to keep VRAM at exactly 4.2 GB
logger.info("Loading VoxCPM2 model...")
# Load in eager mode to prevent long Inductor compilation overhead of quantized weights
vox_engine = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False, optimize=False)
logger.info("Quantizing VoxCPM2 to INT8 Weight-Only...")
quantize_(vox_engine.tts_model, Int8WeightOnlyConfig())
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

    VoxCPM2 produces ~4-8 audio patches per spoken word.  We use a multiplier
    of 9 (generous) plus a fixed 20-patch head-room so the model has room to
    speak naturally while being prevented from running to its 2000-step default
    when the stop head fails to fire.
    """
    word_count = max(1, len(dialogue_text.split()))
    return max(25, word_count * 9 + 20)


def generate_with_cache(
    text: str,
    seed_path: str,
    dialogue_text: str = "",
    inference_timesteps: int = 4,
    cfg_value: float = 2.0
) -> np.ndarray:
    t_start = time.time()
    prompt_cache = get_or_create_prompt_cache(seed_path)
    t_cache = time.time() - t_start
    logger.info(f"⏱️  [vox-audio-core] Cache fetch: {t_cache:.4f}s")
    
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)

    # Compute max_len from the raw dialogue words to prevent runaway generation
    # when the control-instruction prefix inflates target_text_length.
    safe_max_len = _compute_max_len(dialogue_text or text)
    logger.info(f"⏱️  [vox-audio-core] max_len cap: {safe_max_len} (from '{(dialogue_text or text)[:60]}')")
    
    t_gen_start = time.time()
    gen = vox_engine.tts_model._generate_with_prompt_cache(
        target_text=text,
        prompt_cache=prompt_cache,
        max_len=safe_max_len,
        inference_timesteps=inference_timesteps,
        cfg_value=cfg_value,
        retry_badcase=False
    )
    wav, _, _ = next_and_close(gen)
    t_gen = time.time() - t_gen_start
    logger.info(f"⏱️  [vox-audio-core] TTS model generate: {t_gen:.4f}s")
    logger.info(f"⏱️  [vox-audio-core] Total generate_with_cache: {time.time() - t_start:.4f}s")
    return wav.squeeze(0).cpu().numpy()

def generate_stream_with_cache(
    text: str,
    seed_path: str,
    dialogue_text: str = "",
    inference_timesteps: int = 4,
    cfg_value: float = 2.0
) -> Generator[np.ndarray, None, None]:
    prompt_cache = get_or_create_prompt_cache(seed_path)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)

    # Compute max_len from the raw dialogue words to prevent runaway generation
    safe_max_len = _compute_max_len(dialogue_text or text)
    logger.info(f"⏱️  [vox-audio-core] STREAM max_len cap: {safe_max_len}")
    
    gen = vox_engine.tts_model._generate_with_prompt_cache(
        target_text=text,
        prompt_cache=prompt_cache,
        max_len=safe_max_len,
        inference_timesteps=inference_timesteps,
        cfg_value=cfg_value,
        retry_badcase=False,
        streaming=True
    )
    try:
        for wav, _, _ in gen:
            yield wav.squeeze(0).cpu().numpy()
    finally:
        gen.close()

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
    Executes once when an NPC is created to prevent voice drifting across sessions.
    Saves a 5-second deterministic phonetic anchor to disk.
    """
    try:
        seed_path = os.path.join(SEED_DIR, f"{request.npc_id}.wav")

        # Use VoxCPM2 Voice Design block notation
        birth_prompt = f"({request.voice_description}) System voice alignment sequence active. Timbre matrix locked."

        logger.info(f"Generating birth clip for NPC: {request.npc_id}")
        raw_wav = vox_engine.generate(
            text=birth_prompt,
            cfg_value=2.5,          # Forces model adherence to text criteria
            inference_timesteps=12
        )

        sf.write(seed_path, raw_wav, vox_engine.tts_model.sample_rate)
        return {"status": "success", "seed_path": seed_path}
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
            inference_timesteps=4
        )

        # Apply C++ Pedalboard DSP (monster textures, pitch shift, etc.)
        output_audio = apply_dsp(clean_audio, sample_rate, request.dsp_presets)

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
