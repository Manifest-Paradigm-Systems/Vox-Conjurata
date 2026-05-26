from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
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
from pathlib import Path

# --- vox-conjurata Orchestrata Service ---
# Master Controller for Hybrid Monster/Humanoid acting pipeline

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

app = FastAPI(title="vox-conjurata-orchestrator", version="2.1.0")

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
TTS_DESIGNER_URL = os.getenv("TTS_DESIGNER_URL", "http://vox-designer:5010")
TTS_ACTOR_URL = os.getenv("TTS_ACTOR_URL", "http://vox-actor:5020")
TTS_MONSTER_URL = os.getenv("TTS_MONSTER_URL", "http://vox-monster-fish:7860")
TTS_FALLBACK_URL = os.getenv("TTS_FALLBACK_URL", "http://vox-audio-generation:8000")

# Cache for Voice Seeds
VOICE_SEEDS_DIR = Path("./voice_seeds")
VOICE_SEEDS_DIR.mkdir(exist_ok=True)

# --- Endpoints ---

@app.get("/")
async def root():
    return {
        "service": "orchestrator",
        "status": "running",
        "version": "2.1.0",
        "pipeline": "Hybrid Monster/Humanoid (CosyVoice/FishSpeech)"
    }

# --- Models ---

class DiagnosticLog(BaseModel):
    type: Optional[str] = "unknown"
    message: Optional[str] = "no message"
    source: Optional[str] = "unknown"
    lineno: Optional[int] = 0
    error: Optional[str] = "none"

class ActorMetadata(BaseModel):
    actorId: str
    name: str
    lore: str
    stats: dict
    artPath: str
    isMonster: Optional[bool] = False

class DialogueEnrichment(BaseModel):
    speaker: str
    role: str
    raw_text: str
    emotional_resonance: str
    vocal_delivery_prompt: str
    instruct_text: str 
    monster_text: str

# --- Storage ---
error_buffer: List[dict] = []

# --- Helper Functions ---

async def generate_vocal_profile(actor_data: ActorMetadata) -> str:
    """Uses Qwen 2.5 to generate a descriptive acoustic prompt for Parler-TTS."""
    system_instruction = (
        "You are an expert casting director and acoustic engineer. "
        "Analyze the character and output a single-sentence acoustic description for a voice synthesizer. "
        "Include age, gender, raspiness, pitch, inflections, and room acoustics."
    )
    payload = {
        "model": "qwen2.5:latest",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Character: {actor_data.name}\nLore: {actor_data.lore}"}
        ],
        "stream": False
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            return response.json()["message"]["content"].strip()
        except Exception as e:
            logger.error(f"Profile error: {e}")
            return "A clear, neutral speaking voice."

async def forge_voice_seed(actor_id: str, acoustic_description: str) -> str:
    """Calls Parler-TTS (vox-designer) to create a unique 10s voice print."""
    seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed.wav"
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(f"{TTS_DESIGNER_URL}/generate", json={"text": acoustic_description})
            if response.status_code == 200:
                with open(seed_path, "wb") as f: f.write(response.content)
                return str(seed_path)
            return ""
        except Exception as e:
            logger.error(f"Seed forge error: {e}"); return ""

async def enrich_and_instruct(speaker: str, role: str, text: str) -> DialogueEnrichment:
    """Enriches dialogue with Qwen and formats it for both CosyVoice and Fish Speech."""
    system_instruction = (
        "You are a cinematic dialogue director. Analyze the text for emotional subtext. "
        "Output JSON with 'emotional_resonance', 'vocal_delivery_prompt', "
        "'instruct_tag' (e.g. [Terrified, breathy whisper]), "
        "and 'monster_tag' (e.g. [screaming], [low voice], [echo])."
    )
    payload = {
        "model": "qwen2.5:latest",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Speaker: {speaker}, Text: {text}"}
        ],
        "stream": False, "format": "json"
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            res = json.loads(response.json()["message"]["content"])
            return DialogueEnrichment(
                speaker=speaker, role=role, raw_text=text,
                emotional_resonance=res.get("emotional_resonance", "Measured"),
                vocal_delivery_prompt=res.get("vocal_delivery_prompt", "Standard."),
                instruct_text=f"{res.get('instruct_tag', '[Neutral]')}<|endofprompt|>{text}",
                monster_text=f"{res.get('monster_tag', '[low voice]')} <|endofprompt|> {text}"
            )
        except Exception as e:
            logger.error(f"Instruction error: {e}")
            return DialogueEnrichment(
                speaker=speaker, role=role, raw_text=text, 
                emotional_resonance="Neutral", vocal_delivery_prompt="Standard.",
                instruct_text=f"[Neutral]<|endofprompt|>{text}",
                monster_text=f"[low voice] <|endofprompt|> {text}"
            )

# --- Endpoints ---

@app.post("/api/ingest-actor")
async def ingest_actor(data: ActorMetadata):
    seed_path = VOICE_SEEDS_DIR / f"{data.actorId}_seed.wav"
    if seed_path.exists(): return {"status": "cached"}
    profile = await generate_vocal_profile(data)
    path = await forge_voice_seed(data.actorId, profile)
    return {"status": "created", "path": path} if path else {"status": "error"}

@app.post("/api/voice-conversion")
async def voice_conversion(request: Request):
    """
    MASTER PIPELINE:
    1. STT: Faster-Whisper.
    2. Route: CosyVoice (Humanoid) OR Fish Speech (Monster).
    3. Fallback: Edge-TTS.
    """
    try:
        form = await request.form()
        audio_file = form.get("audio_blob")
        metadata_str = form.get("metadata")
        meta = json.loads(metadata_str)
        speaker_name = meta.get("activeSpeakerName", "Unknown")
        actor_id = meta.get("actorId", "narrator")
        mic_type = meta.get("micType", "player")
        is_monster = meta.get("isMonster", False)

        # 1. Transcribe
        async with httpx.AsyncClient(timeout=30.0) as client:
            stt_resp = await client.post(
                f"{STT_URL}/v1/audio/transcriptions",
                files={"file": (audio_file.filename, await audio_file.read(), audio_file.content_type)},
                data={"model": "tiny.en", "language": "en"}
            )
            transcription = stt_resp.json().get("text", "")

        if not transcription.strip(): return {"status": "empty"}

        # 2. Enrich
        role = "NPC" if mic_type == "vox-conjurata-gm-puppet-mic" else "Player"
        enriched = await enrich_and_instruct(speaker_name, role, transcription)

        # 3. ACT (Routing Fork with Fallbacks)
        audio_url = ""
        act_resp = None
        engine_used = "None"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            if is_monster:
                logger.info(f"🐉 Routing to Fish Speech...")
                try:
                    fish_payload = {"text": enriched.monster_text, "model_variant": "baicai1145/s2-pro-w4a16", "speed": 0.95}
                    act_resp = await client.post(f"{TTS_MONSTER_URL}/v1/tts", json=fish_payload)
                    if act_resp.status_code == 200: engine_used = "Fish Speech"
                    else: act_resp = None
                except Exception: pass
            
            if not act_resp: # Humanoid or Monster Fallback
                seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed.wav"
                if not seed_path.exists():
                    await forge_voice_seed(actor_id, f"A clear voice for {speaker_name}.")
                
                try:
                    if seed_path.exists():
                        with open(seed_path, "rb") as f:
                            act_resp = await client.post(
                                f"{TTS_ACTOR_URL}/api/tts",
                                data={"text": enriched.instruct_text},
                                files={"reference_audio": (seed_path.name, f, "audio/wav")}
                            )
                        if act_resp.status_code == 200: engine_used = "CosyVoice"
                        else: act_resp = None
                except Exception: pass

            # ULTIMATE FALLBACK: Edge-TTS
            if not act_resp:
                logger.info("🔊 Using Ultimate Fallback: Edge-TTS")
                try:
                    act_resp = await client.post(f"{TTS_FALLBACK_URL}/v1/audio/speech", json={"text": transcription})
                    if act_resp.status_code == 200: engine_used = "Edge-TTS"
                except Exception: pass

            if act_resp and act_resp.status_code == 200:
                audio_base64 = base64.b64encode(act_resp.content).decode('utf-8')
                audio_url = f"data:audio/webm;base64,{audio_base64}"

        return {
            "status": "success", "transcription": transcription, "enrichment": enriched.model_dump(),
            "voxType": "narration" if mic_type == "vox-conjurata-gm-narrate-mic" else ("puppet" if mic_type == "vox-conjurata-gm-puppet-mic" else "player"),
            "audio_data": audio_url, "engine": engine_used
        }

    except Exception as e:
        logger.error(f"Pipeline failure: {e}"); raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/diagnostics/logs")
async def receive_logs(log: DiagnosticLog):
    error_buffer.append(log.model_dump()); return {"status": "cached"}

@app.get("/api/v1/diagnostics/latest")
async def get_latest_error():
    return error_buffer[-1] if error_buffer else {"status": "nominal"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
