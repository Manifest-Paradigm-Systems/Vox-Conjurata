"""
vox-actor — OpenVoice V2 TTS Service
Platform: AMD ROCm (gfx1201 / RDNA3)
Engine:   OpenVoice V2 (zero-shot voice cloning with 9 emotional styles)
Port:     5020
"""

import os
import torch
import tempfile
import logging
import re
import gc
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("vox-actor")

_rocm_available = torch.cuda.is_available()
_device = "cuda" if _rocm_available else "cpu"
logger.info(f"PyTorch backend: {_device} | ROCm/HIP available: {_rocm_available}")
if _rocm_available:
    logger.info(f"AMD GPU: {torch.cuda.get_device_name(0)}")

# Imports from openvoice
try:
    from openvoice import se_extractor
    from openvoice.api import BaseSpeakerTTS, ToneColorConverter
    logger.info("OpenVoice library loaded successfully.")
except ImportError as err:
    logger.error(f"OpenVoice library not found: {err}")
    raise err

# Checkpoint paths
CKPT_BASE = os.getenv("OPENVOICE_BASE_DIR", "/models/checkpoints/base_speakers/EN")
CKPT_CONVERTER = os.getenv("OPENVOICE_CONVERTER_DIR", "/models/checkpoints_v2/converter")

# Load models on startup
logger.info(f"Loading BaseSpeakerTTS from {CKPT_BASE}...")
base_speaker_tts = BaseSpeakerTTS(f"{CKPT_BASE}/config.json", device=_device)
base_speaker_tts.load_ckpt(f"{CKPT_BASE}/checkpoint.pth")

logger.info(f"Loading ToneColorConverter from {CKPT_CONVERTER}...")
tone_color_converter = ToneColorConverter(f"{CKPT_CONVERTER}/config.json", device=_device)
tone_color_converter.load_ckpt(f"{CKPT_CONVERTER}/checkpoint.pth")

# Preload source SE embeddings
logger.info("Loading source speaker embeddings...")
source_se_default = torch.load(f"{CKPT_BASE}/en_default_se.pth", map_location=_device)
source_se_style = torch.load(f"{CKPT_BASE}/en_style_se.pth", map_location=_device)

# Load MeloTTS on startup for British/other V2 accents
melo_tts_en = None
try:
    from melo.api import TTS
    logger.info("Loading MeloTTS English model for base speaker accents...")
    melo_tts_en = TTS(language='EN', device=_device)
except Exception as e:
    logger.error(f"Failed to load MeloTTS engine: {e}")

app = FastAPI(
    title="vox-actor",
    description="OpenVoice V2 zero-shot voice-cloning TTS — AMD ROCm backend",
    version="2.0.0",
)

SUPPORTED_EMOTIONS = ["default", "whispering", "shouting", "excited", "cheerful", "terrified", "angry", "sad", "friendly"]

@app.get("/")
async def root():
    return {
        "service": "vox-actor",
        "engine": "OpenVoice V2",
        "backend": "AMD ROCm / HIP",
        "device": _device,
        "rocm_available": _rocm_available,
        "status": "ready"
    }

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "device": _device, "rocm": _rocm_available})

@app.post("/api/clear_cache")
async def clear_cache():
    logger.info("Purging Vox-Actor GPU memory cache...")
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception as e:
        logger.warning(f"Failed to clear GPU memory cache: {e}")
    gc.collect()
    return JSONResponse({
        "status": "success",
        "message": "Vox-Actor cache successfully purged."
    })

def map_emotion(tag: str) -> str:
    tag = tag.lower().strip()
    if not tag or tag == "default" or tag == "neutral":
        return "default"
    if "whisper" in tag:
        return "whispering"
    if "shout" in tag or "scream" in tag or "yell" in tag:
        return "shouting"
    if "excit" in tag:
        return "excited"
    if "cheer" in tag or "happy" in tag or "joy" in tag:
        return "cheerful"
    if "terror" in tag or "fear" in tag or "scar" in tag or "panic" in tag or "fright" in tag:
        return "terrified"
    if "angr" in tag or "enrag" in tag or "furi" in tag or "mad" in tag or "growl" in tag or "annoy" in tag:
        return "angry"
    if "sad" in tag or "sorrow" in tag or "cry" in tag or "mourn" in tag or "grief" in tag:
        return "sad"
    if "friend" in tag or "kind" in tag or "warm" in tag or "love" in tag:
        return "friendly"
    return "default"

@app.post("/api/tts")
async def text_to_speech(
    text: str = Form(...),
    reference_audio: UploadFile = File(...),
    prompt_text: str = Form(default="A clear speaking voice."),
    emotion: str = Form(default="default"),
    accent: str = Form(default="en-us")
):
    if not text.strip():
        raise HTTPException(status_code=400, detail="'text' field must not be empty.")

    logger.info(f"TTS request received: {len(text)} chars, initial emotion: {emotion}, accent: {accent}")

    # Parse inline emotion brackets [emotion] or parentheses (emotion) (supporting spaces like "Terrified Whisper")
    match = re.match(r'^[\[(]([a-zA-Z\s]+)[\])]\s*(.*)', text, re.IGNORECASE)
    if match:
        parsed_emotion = match.group(1).strip()
        mapped = map_emotion(parsed_emotion)
        emotion = mapped
        text = match.group(2)
        logger.info(f"Parsed emotion '{parsed_emotion}' -> mapped to '{emotion}'")

    emotion = emotion.lower().strip()
    if emotion not in SUPPORTED_EMOTIONS:
        emotion = "default"

    # Map speed to the style (whispering sounds better slightly slower)
    speed = 0.9 if emotion == "whispering" else 1.0

    ref_path = None
    src_path = None
    output_path = None
    processed_dir = None

    accent = accent.lower().strip()
    is_british = accent in ['en-br', 'en-gb', 'british', 'uk', 'en_br']

    try:
        # 1. Save reference audio to a temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as ref_tmp:
            ref_tmp.write(await reference_audio.read())
            ref_path = ref_tmp.name

        # Create temporary paths for intermediate generation
        fd_src, src_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd_src)
        
        fd_out, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd_out)

        # Create a directory for processed VAD files
        processed_dir = tempfile.mkdtemp()

        # 2. Run base speaker TTS & 4. Select appropriate source SE
        source_se = None
        if is_british and melo_tts_en is not None:
            logger.info(f"Synthesizing base speech with British accent using MeloTTS V2: '{text[:50]}...'")
            speaker_id = melo_tts_en.hps.data.spk2id['EN-BR']
            melo_tts_en.tts_to_file(text, speaker_id, src_path, speed=speed)
            
            # Load the V2 EN-BR source speaker embedding
            source_se_path = f"{CKPT_CONVERTER.replace('/converter', '')}/base_speakers/ses/en-br.pth"
            source_se = torch.load(source_se_path, map_location=_device)
        else:
            logger.info(f"Running base speaker TTS for emotion: {emotion}, speed: {speed}")
            base_speaker_tts.tts(text, src_path, speaker=emotion, language='English', speed=speed)
            source_se = source_se_default if emotion == "default" else source_se_style

        # 3. Extract target speaker SE
        logger.info("Extracting target speaker embedding...")
        try:
            target_se, audio_name = se_extractor.get_se(
                ref_path, 
                tone_color_converter, 
                target_dir=processed_dir, 
                vad=True
            )
        except Exception as vad_err:
            logger.warning(f"VAD SE extraction failed ({vad_err}). Falling back to direct extraction from reference audio...")
            from openvoice.se_extractor import hash_numpy_array
            audio_name = f"{os.path.basename(ref_path).rsplit('.', 1)[0]}_v2_{hash_numpy_array(ref_path)}"
            se_path = os.path.join(processed_dir, audio_name, 'se.pth')
            os.makedirs(os.path.dirname(se_path), exist_ok=True)
            target_se = tone_color_converter.extract_se([ref_path], se_save_path=se_path)

        # 5. Run tone color converter
        logger.info("Converting tone color...")
        tone_color_converter.convert(
            audio_src_path=src_path,
            src_se=source_se,
            tgt_se=target_se,
            output_path=output_path,
            message="@MyShell"
        )

        logger.info(f"Audio conversion successful. Output path: {output_path}")
        return FileResponse(output_path, media_type="audio/wav")

    except Exception as exc:
        logger.error(f"TTS inference error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        # Cleanup
        if ref_path and os.path.exists(ref_path):
            try: os.remove(ref_path)
            except Exception: pass
        if src_path and os.path.exists(src_path):
            try: os.remove(src_path)
            except Exception: pass
        if processed_dir and os.path.exists(processed_dir):
            import shutil
            try: shutil.rmtree(processed_dir)
            except Exception: pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5020, log_level="info")
