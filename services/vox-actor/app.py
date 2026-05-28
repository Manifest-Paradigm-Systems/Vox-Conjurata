from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
import logging
import os
import tempfile
import torch
import torchaudio
import hashlib
from modelscope import snapshot_download

# Attempt to import CosyVoice - assumes it will be installed via requirements.txt
try:
    from cosyvoice.cli.cosyvoice import CosyVoice
    from cosyvoice.utils.file_utils import load_wav
except ImportError:
    # Fallback/Placeholder if installation is still in progress
    CosyVoice = None
    logger.warning("CosyVoice library not found. Service will run in placeholder mode.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-actor")

app = FastAPI(title="vox-actor-cosyvoice-genuine")

# Model configuration
MODEL_DIR = os.getenv("COSYVOICE_MODEL_DIR", "/models/CosyVoice-300M")

# Global engine instance
_cosyvoice = None

def get_cosyvoice():
    global _cosyvoice
    if _cosyvoice is None:
        if CosyVoice is None:
            raise HTTPException(status_code=503, detail="CosyVoice engine not initialized (library missing).")
        
        if not os.path.exists(MODEL_DIR):
            logger.info(f"Downloading CosyVoice weights to {MODEL_DIR}...")
            snapshot_download('iic/CosyVoice-300M', local_dir=MODEL_DIR)
            
        logger.info(f"Initializing CosyVoice from {MODEL_DIR}...")
        _cosyvoice = CosyVoice(MODEL_DIR)
    return _cosyvoice

@app.get("/")
async def root():
    return {
        "service": "vox-actor", 
        "engine": "CosyVoice-300M", 
        "status": "ready" if _cosyvoice else "loading"
    }

@app.post("/api/tts")
async def text_to_speech(
    text: str = Form(...),
    reference_audio: UploadFile = File(...)
):
    if not text:
        raise HTTPException(status_code=400, detail="No text provided.")

    logger.info(f"Acting: Genuine CosyVoice cloning for: '{text[:50]}...'")

    try:
        # 1. Save reference audio to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as ref_tmp:
            ref_tmp.write(await reference_audio.read())
            ref_path = ref_tmp.name

        # 2. Load and resample reference audio (CosyVoice wants 16k)
        prompt_speech_16k = load_wav(ref_path, 16000)
        
        # 3. Generate audio using zero-shot cloning
        cosyvoice = get_cosyvoice()
        
        # We assume the user wants zero-shot cloning
        # CosyVoice inference_zero_shot(tts_text, prompt_text, prompt_speech_16k)
        # We'll use a generic prompt text as we don't have the seed transcript here yet
        # But we could potentially pass it if the orchestrator sends it.
        prompt_text = "A clear speaking voice." 
        
        # Generate
        output = cosyvoice.inference_zero_shot(text, prompt_text, prompt_speech_16k)
        
        # 4. Save to output wav
        fd, output_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        
        # CosyVoice returns a generator of dicts
        combined_audio = []
        for result in output:
            combined_audio.append(result['tts_speech'])
        
        if not combined_audio:
            raise Exception("CosyVoice generated no audio data.")
            
        full_audio = torch.cat(combined_audio, dim=1)
        torchaudio.save(output_path, full_audio, 22050)
            
        os.remove(ref_path)
        return FileResponse(output_path, media_type="audio/wav")
        
    except Exception as e:
        logger.error(f"Acting error: {e}")
        if 'ref_path' in locals() and os.path.exists(ref_path):
            os.remove(ref_path)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Pre-warm the model
    try:
        get_cosyvoice()
    except Exception as e:
        logger.error(f"Failed to pre-warm CosyVoice: {e}")
    uvicorn.run(app, host="0.0.0.0", port=5020)
