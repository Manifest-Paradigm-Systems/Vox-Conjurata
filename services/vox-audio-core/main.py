from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel
from typing import Optional, Generator
import os
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

        # Execute high-fidelity cloning from the fixed master anchor
        clean_audio = vox_engine.generate(
            text=final_text,
            reference_wav_path=seed_path,
            cfg_value=2.0,
            inference_timesteps=4  # Aggressively optimized for real-time delivery
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

        def wav_chunk_generator() -> Generator[bytes, None, None]:
            # Emit the WAV header first so the browser knows the format
            yield encode_wav_header(sample_rate)

            for chunk in vox_engine.generate_streaming(
                text=final_text,
                reference_wav_path=final_seed,
                cfg_value=2.0,
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
