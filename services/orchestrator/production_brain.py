from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
import httpx
import os
import logging
import asyncio
import json
import urllib.parse
import base64

# --- vox-conjurata Orchestrata Service ---
# Master Controller for STT -> LLM Enrichment -> TTS Pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - vox-conjurata - %(levelname)s - %(message)s"
)
logger = logging.getLogger("vox-conjurata")

# DEBUG: Check for multipart
try:
    import multipart
    logger.info("✅ python-multipart (imported as multipart) is correctly installed.")
except ImportError:
    logger.error("❌ python-multipart is NOT found in the python environment.")

app = FastAPI(title="vox-conjurata-orchestrator", version="1.1.0")

# Enable CORS for browser-based Foundry client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Internal Service Routing (Container Networking)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://vox-llm-core:11434")
STT_URL = os.getenv("STT_URL", "http://vox-voice:5000")
TTS_URL = os.getenv("TTS_URL", "http://vox-audio-generation:8000")

# --- Models ---

class DiagnosticLog(BaseModel):
    type: Optional[str] = "unknown"
    message: Optional[str] = "no message"
    source: Optional[str] = "unknown"
    lineno: Optional[int] = 0
    error: Optional[str] = "none"

class DialogueEnrichment(BaseModel):
    speaker: str
    role: str
    raw_text: str
    emotional_resonance: str
    vocal_delivery_prompt: str

# --- Storage ---
error_buffer: List[dict] = []

# --- Helper Functions ---

async def enrich_dialogue(speaker: str, role: str, text: str) -> DialogueEnrichment:
    """Fast enrichment using simplified prompt and lower temperature."""
    system_instruction = (
        "You are a cinematic VTT parser. Output a single compound emotion and a short vocal cue in JSON format."
    )
    
    payload = {
        "model": "qwen2.5:latest",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Speaker: {speaker}, Text: {text}"}
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 50
        }
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            response.raise_for_status()
            content = json.loads(response.json()["message"]["content"])
            return DialogueEnrichment(
                speaker=speaker,
                role=role,
                raw_text=text,
                emotional_resonance=content.get("emotional_resonance", "Measured Delivery"),
                vocal_delivery_prompt=content.get("vocal_delivery_prompt", "Standard pacing.")
            )
        except Exception as e:
            logger.error(f"Enrichment error: {e}")
            return DialogueEnrichment(speaker=speaker, role=role, raw_text=text, emotional_resonance="Neutral", vocal_delivery_prompt="Standard.")

# --- Endpoints ---

@app.get("/")
async def root():
    return {"service": "orchestrator", "status": "running", "version": "1.1.0"}

@app.post("/api/voice-conversion")
async def voice_conversion(request: Request):
    """
    MASTER PIPELINE:
    1. STT: Convert WebM to Text
    2. LLM: Analyze Emotion & Content
    3. TTS: Generate Audio (Base64 WebM)
    """
    try:
        # Manually parse the multipart form to avoid automatic validation failure
        form = await request.form()
        audio_file = form.get("audio_blob")
        metadata_str = form.get("metadata")

        if not audio_file or not metadata_str:
            raise HTTPException(status_code=400, detail="Missing audio_blob or metadata in form.")

        meta = json.loads(metadata_str)
        speaker_name = meta.get("activeSpeakerName", "Unknown")
        mic_type = meta.get("micType", "player") 
        
        logger.info(f"🚀 Pipeline started for {speaker_name} ({mic_type})")

        # 1. Forward to STT Service
        async with httpx.AsyncClient(timeout=30.0) as client:
            stt_resp = await client.post(
                f"{STT_URL}/v1/audio/transcriptions",
                files={"file": (audio_file.filename, await audio_file.read(), audio_file.content_type)},
                data={"model": "base", "language": "en"}
            )
            stt_resp.raise_for_status()
            transcription = stt_resp.json().get("text", "")

        if not transcription.strip():
            return {"status": "empty", "message": "No speech detected."}

        # 2. Enrich with LLM
        role = "NPC" if mic_type == "vox-conjurata-gm-puppet-mic" else ("DM" if mic_type == "vox-conjurata-gm-narrate-mic" else "Player")
        enriched = await enrich_dialogue(speaker_name, role, transcription)

        # 3. Generate Audio Data URI (Bypass Mixed Content Blocks)
        audio_url = ""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                tts_resp = await client.post(
                    f"{TTS_URL}/v1/audio/speech",
                    json={"text": transcription}
                )
                if tts_resp.status_code == 200:
                    audio_base64 = base64.b64encode(tts_resp.content).decode('utf-8')
                    # Set type to webm as requested
                    audio_url = f"data:audio/webm;base64,{audio_base64}"
                else:
                    logger.error(f"TTS service returned error: {tts_resp.status_code}")
        except Exception as e:
            logger.error(f"Failed to fetch TTS audio: {e}")

        # 4. Return to Client
        return {
            "status": "success",
            "transcription": transcription,
            "enrichment": enriched.model_dump(),
            "voxType": "narration" if mic_type == "vox-conjurata-gm-narrate-mic" else ("puppet" if mic_type == "vox-conjurata-gm-puppet-mic" else "player"),
            "audioUrl": audio_url
        }

    except Exception as e:
        logger.error(f"Pipeline failure: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/diagnostics/logs")
async def receive_logs(log: DiagnosticLog):
    error_buffer.append(log.model_dump())
    if len(error_buffer) > 10:
        error_buffer.pop(0)
    return {"status": "cached"}

@app.get("/api/v1/diagnostics/latest")
async def get_latest_error():
    return error_buffer[-1] if error_buffer else {"status": "nominal"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
