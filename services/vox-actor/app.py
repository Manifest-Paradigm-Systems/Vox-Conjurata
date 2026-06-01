"""
vox-actor — OpenVoice V2 TTS Service
Platform: AMD ROCm (gfx1201 / RDNA3)
Engine:   OpenVoice V2 (zero-shot voice cloning with 9 emotional styles)
Port:     5020

Performance optimisations:
  - Jarvis seed speaker embedding pre-extracted at startup and held as a
    persistent HIP tensor — SE extraction skipped entirely on every request.
  - EN-BR source SE pre-loaded at startup instead of per-request disk reads.
  - Silero VAD model pre-downloaded and cached at startup.
  - MeloTTS EN-BR speaker_id resolved once at startup.
"""

import os
import shutil
import torch
import tempfile
import logging
import re
import gc
import time
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("vox-actor")

# ---------------------------------------------------------------------------
# Device — ROCm/HIP surfaces through PyTorch's CUDA compatibility layer
# ---------------------------------------------------------------------------
_rocm_available = torch.cuda.is_available()
_device = "cuda" if _rocm_available else "cpu"
logger.info(f"PyTorch backend: {_device} | ROCm/HIP available: {_rocm_available}")
if _rocm_available:
    logger.info(f"AMD GPU: {torch.cuda.get_device_name(0)}")

# ---------------------------------------------------------------------------
# OpenVoice imports
# ---------------------------------------------------------------------------
try:
    from openvoice import se_extractor
    from openvoice.api import BaseSpeakerTTS, ToneColorConverter, OpenVoiceBaseClass
    
    # Monkeypatch ToneColorConverter.__init__ to support enable_watermark=False without TypeError
    def _patched_init(self, *args, **kwargs):
        enable_watermark = kwargs.pop('enable_watermark', True)
        OpenVoiceBaseClass.__init__(self, *args, **kwargs)
        if enable_watermark:
            import wavmark
            self.watermark_model = wavmark.load_model().to(self.device)
        else:
            self.watermark_model = None
        self.version = getattr(self.hps, '_version_', "v1")
    
    ToneColorConverter.__init__ = _patched_init
    logger.info("OpenVoice library loaded and ToneColorConverter patched successfully.")
except ImportError as err:
    logger.error(f"OpenVoice library not found: {err}")
    raise err


# ---------------------------------------------------------------------------
# Checkpoint paths
# ---------------------------------------------------------------------------
CKPT_BASE      = os.getenv("OPENVOICE_BASE_DIR",      "/models/checkpoints/base_speakers/EN")
CKPT_CONVERTER = os.getenv("OPENVOICE_CONVERTER_DIR", "/models/checkpoints_v2/converter")
CKPT_V2_BASE   = CKPT_CONVERTER.replace("/converter", "")   # /models/checkpoints_v2

# Jarvis seed voice — fixed reference used for every request
JARVIS_SEED_PATH = os.getenv(
    "JARVIS_SEED_PATH",
    "/models/jarvis_assets/1_Jarvis_seed_(Vocals).wav"
)

# ---------------------------------------------------------------------------
# Load OpenVoice models onto GPU at startup
# ---------------------------------------------------------------------------
logger.info(f"Loading BaseSpeakerTTS from {CKPT_BASE}...")
base_speaker_tts = BaseSpeakerTTS(f"{CKPT_BASE}/config.json", device=_device)
base_speaker_tts.load_ckpt(f"{CKPT_BASE}/checkpoint.pth")

logger.info(f"Loading ToneColorConverter from {CKPT_CONVERTER}...")
tone_color_converter = ToneColorConverter(f"{CKPT_CONVERTER}/config.json", device=_device, enable_watermark=False)
tone_color_converter.load_ckpt(f"{CKPT_CONVERTER}/checkpoint.pth")

# ---------------------------------------------------------------------------
# Pre-load V1 source speaker embeddings (already on GPU from startup)
# ---------------------------------------------------------------------------
logger.info("Loading V1 source speaker embeddings...")
source_se_default = torch.load(f"{CKPT_BASE}/en_default_se.pth", map_location=_device)
source_se_style   = torch.load(f"{CKPT_BASE}/en_style_se.pth",   map_location=_device)

# ---------------------------------------------------------------------------
# Pre-load V2 EN-BR source speaker embedding (eliminates per-request disk read)
# ---------------------------------------------------------------------------
source_se_enbr = None
_enbr_se_path = f"{CKPT_V2_BASE}/base_speakers/ses/en-br.pth"
try:
    logger.info(f"Pre-loading EN-BR source SE from {_enbr_se_path}...")
    source_se_enbr = torch.load(_enbr_se_path, map_location=_device)
    logger.info("EN-BR source SE loaded onto GPU.")
except Exception as e:
    logger.error(f"Failed to load EN-BR source SE: {e}")

# ---------------------------------------------------------------------------
# Load MeloTTS English model for British accent base synthesis
# ---------------------------------------------------------------------------
melo_tts_en      = None
melo_enbr_spk_id = None
try:
    from melo.api import TTS as MeloTTS
    logger.info("Loading MeloTTS English model for base speaker accents...")
    melo_tts_en = MeloTTS(language="EN", device=_device)
    melo_enbr_spk_id = melo_tts_en.hps.data.spk2id["EN-BR"]
    logger.info(f"MeloTTS loaded. EN-BR speaker_id = {melo_enbr_spk_id}")
except Exception as e:
    logger.error(f"Failed to load MeloTTS engine: {e}")

# ---------------------------------------------------------------------------
# Pre-warm Silero VAD (downloads from GitHub on first call — do it now)
# ---------------------------------------------------------------------------
logger.info("Pre-warming Silero VAD model...")
try:
    torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        trust_repo=True,
        force_reload=False,
    )
    logger.info("Silero VAD warmed up and cached.")
except Exception as e:
    logger.warning(f"Silero VAD pre-warm failed (will retry on first request): {e}")

# ---------------------------------------------------------------------------
# ROCm/MIOpen kernel warm-up
#
# On AMD ROCm, MIOpen JIT-compiles GPU kernels on the very first forward pass
# (~6-18s). Running a dummy inference at startup burns that cost once so every
# real request thereafter runs at full GPU speed (~0.4s for MeloTTS).
# ---------------------------------------------------------------------------
if melo_tts_en is not None and melo_enbr_spk_id is not None:
    logger.info("ROCm kernel warm-up: running dummy MeloTTS inference...")
    try:
        import tempfile as _tf
        _warmup_fd, _warmup_path = _tf.mkstemp(suffix=".wav")
        os.close(_warmup_fd)
        melo_tts_en.tts_to_file("Warming up.", melo_enbr_spk_id, _warmup_path, speed=1.0)
        os.unlink(_warmup_path)
        logger.info("MeloTTS ROCm kernels compiled and warmed up. ✓")
    except Exception as e:
        logger.warning(f"MeloTTS warm-up failed (first real request will be slower): {e}")

# ---------------------------------------------------------------------------
# Pre-extract Jarvis seed speaker embedding — held as persistent HIP tensor
#
# This is the single biggest latency win: SE extraction (VAD + ToneColorConverter
# inference on the seed audio) takes ~10-15s and was running on every request.
# We do it once here and reuse the tensor for the lifetime of the process.
# ---------------------------------------------------------------------------
_jarvis_se_cache = None   # persistent HIP tensor

def _extract_jarvis_se():
    """Extract and cache the Jarvis seed SE at startup."""
    global _jarvis_se_cache

    if not os.path.exists(JARVIS_SEED_PATH):
        logger.warning(
            f"Jarvis seed file not found at {JARVIS_SEED_PATH}. "
            "SE will be extracted per-request from uploaded audio."
        )
        return

    se_cache_dir = "/tmp/jarvis_se_cache"
    os.makedirs(se_cache_dir, exist_ok=True)

    logger.info(f"Pre-extracting Jarvis seed SE from {JARVIS_SEED_PATH}...")
    try:
        target_se, _ = se_extractor.get_se(
            JARVIS_SEED_PATH,
            tone_color_converter,
            target_dir=se_cache_dir,
            vad=True,
        )
        _jarvis_se_cache = target_se
        logger.info("Jarvis seed SE extracted and cached on GPU. ✓")
    except Exception as e:
        logger.error(f"Startup SE extraction failed: {e}. Falling back to per-request extraction.")
        _jarvis_se_cache = None

_extract_jarvis_se()

# ---------------------------------------------------------------------------
# ToneColorConverter ROCm kernel warm-up
#
# Compile voice conversion kernels before the first user request by running a
# dummy conversion at startup.
# ---------------------------------------------------------------------------
if _jarvis_se_cache is not None and melo_tts_en is not None and source_se_enbr is not None:
    logger.info("ROCm kernel warm-up: running dummy ToneColorConverter conversion...")
    try:
        import tempfile as _tf
        _dummy_src_fd, _dummy_src_path = _tf.mkstemp(suffix=".wav")
        os.close(_dummy_src_fd)
        melo_tts_en.tts_to_file("Warm up.", melo_enbr_spk_id, _dummy_src_path, speed=1.0)
        
        _dummy_out_fd, _dummy_out_path = _tf.mkstemp(suffix=".wav")
        os.close(_dummy_out_fd)
        
        tone_color_converter.convert(
            audio_src_path=_dummy_src_path,
            src_se=source_se_enbr,
            tgt_se=_jarvis_se_cache,
            output_path=_dummy_out_path,
            message="@MyShell",
        )
        os.unlink(_dummy_src_path)
        os.unlink(_dummy_out_path)
        logger.info("ToneColorConverter ROCm kernels compiled and warmed up. ✓")
    except Exception as e:
        logger.warning(f"ToneColorConverter warm-up failed: {e}")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="vox-actor",
    description="OpenVoice V2 zero-shot voice-cloning TTS — AMD ROCm backend",
    version="2.1.0",
)

SUPPORTED_EMOTIONS = [
    "default", "whispering", "shouting", "excited", "cheerful",
    "terrified", "angry", "sad", "friendly"
]


@app.get("/")
async def root():
    return {
        "service":       "vox-actor",
        "engine":        "OpenVoice V2",
        "backend":       "AMD ROCm / HIP",
        "device":        _device,
        "rocm_available": _rocm_available,
        "seed_se_cached": _jarvis_se_cache is not None,
        "melo_loaded":   melo_tts_en is not None,
        "status":        "ready",
    }


@app.get("/health")
async def health():
    return JSONResponse({
        "status":         "ok",
        "device":         _device,
        "rocm":           _rocm_available,
        "seed_se_cached": _jarvis_se_cache is not None,
    })


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
        "status":  "success",
        "message": "Vox-Actor cache successfully purged.",
    })


def map_emotion(tag: str) -> str:
    tag = tag.lower().strip()
    if not tag or tag in ("default", "neutral"):
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
def text_to_speech(
    text:            str         = Form(...),
    reference_audio: UploadFile  = File(...),
    prompt_text:     str         = Form(default="A clear speaking voice."),
    emotion:         str         = Form(default="default"),
    accent:          str         = Form(default="en-us"),
):
    if not text.strip():
        raise HTTPException(status_code=400, detail="'text' field must not be empty.")

    logger.info(f"TTS request: {len(text)} chars | emotion={emotion} | accent={accent}")

    # Parse inline [emotion] or (emotion) tag at start of text
    match = re.match(r'^[\[(]([a-zA-Z\s]+)[\])]\s*(.*)', text, re.IGNORECASE)
    if match:
        parsed_emotion = match.group(1).strip()
        emotion = map_emotion(parsed_emotion)
        text    = match.group(2)
        logger.info(f"Parsed emotion '{parsed_emotion}' -> '{emotion}'")

    emotion = emotion.lower().strip()
    if emotion not in SUPPORTED_EMOTIONS:
        emotion = "default"

    speed      = 0.9 if emotion == "whispering" else 1.0
    accent     = accent.lower().strip()
    is_british = accent in ("en-br", "en-gb", "british", "uk", "en_br")

    ref_path     = None
    src_path     = None
    output_path  = None
    tmp_se_dir   = None
    use_tmp_seed = False   # True only when the cached SE is unavailable

    try:
        # ------------------------------------------------------------------
        # 1. Save uploaded reference audio (may be ignored if seed SE cached)
        # ------------------------------------------------------------------
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as ref_tmp:
            ref_tmp.write(reference_audio.file.read())
            ref_path = ref_tmp.name

        # Intermediate and output temp files
        fd_src, src_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd_src)
        fd_out, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd_out)

        # ------------------------------------------------------------------
        # 2. Base TTS synthesis + source SE selection
        # ------------------------------------------------------------------
        t_start = time.time()
        if is_british and melo_tts_en is not None:
            logger.info(f"MeloTTS V2 EN-BR synthesis: '{text[:60]}'")
            melo_tts_en.tts_to_file(text, melo_enbr_spk_id, src_path, speed=speed)
            source_se = source_se_enbr  # pre-loaded HIP tensor
        else:
            logger.info(f"V1 base TTS: emotion={emotion} speed={speed}")
            base_speaker_tts.tts(text, src_path, speaker=emotion, language="English", speed=speed)
            source_se = source_se_default if emotion == "default" else source_se_style
        t_base = time.time() - t_start

        # ------------------------------------------------------------------
        # 3. Target speaker SE — use pre-cached Jarvis SE when available
        # ------------------------------------------------------------------
        filename = getattr(reference_audio, "filename", "") or ""
        is_jarvis_req = "jarvis" in filename.lower()

        if is_jarvis_req and _jarvis_se_cache is not None:
            # Fast path: reuse the startup-extracted HIP tensor
            target_se = _jarvis_se_cache
            logger.info("Using pre-cached Jarvis seed SE (fast path). ✓")
        else:
            # Slow fallback: extract SE from the uploaded reference audio
            logger.info(f"Pre-cached SE unavailable or not Jarvis request (filename={filename}) — extracting from uploaded audio...")
            use_tmp_seed = True
            tmp_se_dir = tempfile.mkdtemp()
            try:
                target_se, _ = se_extractor.get_se(
                    ref_path,
                    tone_color_converter,
                    target_dir=tmp_se_dir,
                    vad=True,
                )
            except Exception as vad_err:
                logger.warning(f"VAD SE extraction failed ({vad_err}). Direct extraction fallback...")
                from openvoice.se_extractor import hash_numpy_array
                audio_name = (
                    f"{os.path.basename(ref_path).rsplit('.', 1)[0]}"
                    f"_v2_{hash_numpy_array(ref_path)}"
                )
                se_path = os.path.join(tmp_se_dir, audio_name, "se.pth")
                os.makedirs(os.path.dirname(se_path), exist_ok=True)
                target_se = tone_color_converter.extract_se([ref_path], se_save_path=se_path)
        t_se = time.time() - t_start - t_base

        # ------------------------------------------------------------------
        # 4. Tone colour conversion
        # ------------------------------------------------------------------
        t_conv_start = time.time()
        logger.info("Converting tone colour...")
        tone_color_converter.convert(
            audio_src_path=src_path,
            src_se=source_se,
            tgt_se=target_se,
            output_path=output_path,
            message="@MyShell",
        )
        t_conv = time.time() - t_conv_start

        logger.info(
            f"Audio synthesis profiling: Total={time.time() - t_start:.4f}s "
            f"| BaseTTS={t_base:.4f}s | SE={t_se:.4f}s | Conv={t_conv:.4f}s"
        )
        return FileResponse(output_path, media_type="audio/wav")

    except Exception as exc:
        logger.error(f"TTS inference error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        if ref_path and os.path.exists(ref_path):
            try: os.remove(ref_path)
            except Exception: pass
        if src_path and os.path.exists(src_path):
            try: os.remove(src_path)
            except Exception: pass
        if use_tmp_seed and tmp_se_dir and os.path.exists(tmp_se_dir):
            try: shutil.rmtree(tmp_se_dir)
            except Exception: pass
        # output_path is intentionally NOT deleted — FileResponse streams it


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5020, log_level="info")
