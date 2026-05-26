from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import logging
import os
import tempfile
import edge_tts
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-designer")

app = FastAPI(title="vox-designer-parler-tts")

@app.get("/")
async def root():
    return {"service": "vox-designer", "status": "running"}

@app.post("/generate")
async def generate_seed(payload: dict):
    text = payload.get("text", "")
    if not text:
        raise HTTPException(status_code=400, detail="No acoustic description provided.")

    logger.info(f"Generating 10s voice seed for: '{text[:50]}...'")

    # High-Fidelity Simulation using Edge-TTS
    # This ensures the user hears a "casting" voice during testing
    fd, output_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    
    try:
        # We choose a diverse voice to act as a "seed"
        communicate = edge_tts.Communicate(text, "en-GB-ThomasNeural")
        await communicate.save(output_path)
        return FileResponse(output_path, media_type="audio/wav")
    except Exception as e:
        logger.error(f"Forge error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5010)
