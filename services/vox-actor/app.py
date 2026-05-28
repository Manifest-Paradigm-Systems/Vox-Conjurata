from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
import logging
import os
import tempfile
import edge_tts
import asyncio
import hashlib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-actor")

app = FastAPI(title="vox-actor-cosyvoice")

FEMALE_VOICES = [
    "en-US-JennyNeural",
    "en-US-AvaNeural",
    "en-US-EmmaNeural",
    "en-US-MichelleNeural",
    "en-GB-SoniaNeural",
    "en-GB-LibbyNeural",
    "en-AU-NatashaNeural",
    "en-CA-ClaraNeural"
]

MALE_VOICES = [
    "en-US-ChristopherNeural",
    "en-US-GuyNeural",
    "en-US-EricNeural",
    "en-US-RogerNeural",
    "en-US-AndrewNeural",
    "en-US-BrianNeural",
    "en-GB-RyanNeural",
    "en-GB-ThomasNeural",
    "en-AU-WilliamNeural",
    "en-CA-LiamNeural"
]

@app.get("/")
async def root():
    return {"service": "vox-actor", "status": "running"}

@app.post("/api/tts")
async def text_to_speech(
    text: str = Form(...),
    reference_audio: UploadFile = File(...)
):
    if not text:
        raise HTTPException(status_code=400, detail="No text provided.")

    logger.info(f"Acting: Generating speech for: '{text[:50]}...' using reference {reference_audio.filename}")

    # Determine gender and actor index from filename
    filename = reference_audio.filename.lower()
    is_female = "female" in filename
    actor_id = reference_audio.filename.split("_seed")[0]
    
    # Hash actor ID to pick consistent voice
    idx = int(hashlib.md5(actor_id.encode('utf-8')).hexdigest(), 16)
    if is_female:
        voice = FEMALE_VOICES[idx % len(FEMALE_VOICES)]
    else:
        voice = MALE_VOICES[idx % len(MALE_VOICES)]

    # Parse emotion and clean text
    parts = text.split("<|endofprompt|>")
    clean_text = parts[-1].strip()
    emotion = parts[0].strip().lower() if len(parts) > 1 else "neutral"

    # Default adjustments
    rate = "+0%"
    pitch = "+0Hz"
    volume = "+0%"

    # Map emotions for expressiveness
    if "enraged" in emotion or "angry" in emotion or "growl" in emotion:
        rate = "-5%"
        pitch = "-3Hz"
        volume = "+15%"
    elif "whisper" in emotion or "terrified" in emotion or "soft" in emotion or "fearful" in emotion:
        rate = "+5%"
        pitch = "-4Hz"
        volume = "-40%"
    elif "excited" in emotion or "happy" in emotion or "joyful" in emotion:
        rate = "+12%"
        pitch = "+6Hz"
        volume = "+10%"
    elif "sad" in emotion or "depressed" in emotion or "mournful" in emotion:
        rate = "-15%"
        pitch = "-5Hz"
        volume = "-10%"

    logger.info(f"Using voice {voice} with rate={rate}, pitch={pitch}, volume={volume} for emotion={emotion}")

    fd, output_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    
    try:
        communicate = edge_tts.Communicate(clean_text, voice, rate=rate, pitch=pitch, volume=volume)
        await communicate.save(output_path)
        return FileResponse(output_path, media_type="audio/wav")
    except Exception as e:
        logger.error(f"Acting error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5020)
