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
import edge_tts
from pathlib import Path

# --- vox-conjurata Orchestrator Service ---
# Master Controller with VRAM Guardrails and Qwen-vLLM Memory Optimizations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - vox-conjurata - %(levelname)s - %(message)s"
)
logger = logging.getLogger("vox-conjurata")

try:
    import multipart
    logger.info("✅ python-multipart (imported as multipart) is correctly installed.")
except ImportError:
    logger.error("❌ python-multipart is NOT found in the python environment.")

app = FastAPI(title="vox-conjurata-orchestrator", version="2.2.0")

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
TTS_FALLBACK_URL = os.getenv("TTS_FALLBACK_URL", "http://vox-audio-generation-music:8000")
FOUNDRY_API_URL = os.getenv("FOUNDRY_API_URL", "http://foundry-vtt:30000/api")
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY", "")

# Cache for Voice Seeds
VOICE_SEEDS_DIR = Path("./voice_seeds")
VOICE_SEEDS_DIR.mkdir(exist_ok=True)

CONFIG_PATH = Path("./settings/voice_routing_config.json")

# --- Helper Functions ---

def get_vram_used_gb() -> float:
    """Reads current GPU VRAM utilization from Host Linux sysfs (ROCm device card0)."""
    try:
        with open("/sys/class/drm/card0/device/mem_info_vram_used", "r") as f:
            used_bytes = int(f.read().strip())
            return used_bytes / (1024 ** 3)
    except Exception:
        return 0.0

def load_routing_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read settings config file: {e}")
    return {}

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

class DialogueEndRequest(BaseModel):
    npcName: str
    transcript: str

# --- Storage ---
error_buffer: List[dict] = []

# --- Voice Generation Engines (Modular Factory Pattern) ---

class SpeechEngine:
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient) -> Optional[bytes]:
        raise NotImplementedError()

class CosyVoiceEngine(SpeechEngine):
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient) -> Optional[bytes]:
        seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed.wav"
        if not seed_path.exists():
            await forge_voice_seed(actor_id, f"A clear speaking voice for {actor_id}.")
        
        if seed_path.exists():
            try:
                with open(seed_path, "rb") as f:
                    resp = await client.post(
                        f"{TTS_ACTOR_URL}/api/tts",
                        data={"text": text},
                        files={"reference_audio": (seed_path.name, f, "audio/wav")}
                    )
                if resp.status_code == 200:
                    return resp.content
            except Exception as e:
                logger.error(f"CosyVoice inference failed: {e}")
        return None

class FishSpeechEngine(SpeechEngine):
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient) -> Optional[bytes]:
        try:
            payload = {
                "text": text,
                "model_variant": "baicai1145/s2-pro-w4a16",
                "speed": 0.95
            }
            resp = await client.post(f"{TTS_MONSTER_URL}/v1/tts", json=payload)
            if resp.status_code == 200:
                return resp.content
        except Exception as e:
            logger.error(f"Fish Speech inference failed: {e}")
        return None

class EdgeTTSEngine(SpeechEngine):
    def __init__(self, voice_name: str = "en-US-ChristopherNeural", rate: str = "+0%"):
        self.voice_name = voice_name
        self.rate = rate

    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient) -> Optional[bytes]:
        try:
            communicate = edge_tts.Communicate(text, self.voice_name, rate=self.rate)
            data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    data += chunk["data"]
            return data if len(data) > 0 else None
        except Exception as e:
            logger.error(f"Edge-TTS synthesis failed: {e}")
            return None

class SpeechPipelineFactory:
    def __init__(self):
        self.cosyvoice = CosyVoiceEngine()
        self.fishspeech = FishSpeechEngine()

    def get_engine(self, is_monster: bool, stats: dict, config: dict, vram_triggered: bool) -> SpeechEngine:
        if vram_triggered:
            fallback_voice = config.get("narrator_preferences", {}).get("default_voice", "en-US-ChristopherNeural")
            rate = config.get("narrator_preferences", {}).get("rate_adjustment", "+0%")
            logger.info(f"Using isolated Edge-TTS Voice Factory due to VRAM threshold trigger ({fallback_voice}).")
            return EdgeTTSEngine(voice_name=fallback_voice, rate=rate)

        tier_routing = config.get("tier_routing", {})
        
        if is_monster:
            if tier_routing.get("monster_engine") == "edge-tts":
                fallback_voice = config.get("narrator_preferences", {}).get("default_voice", "en-US-ChristopherNeural")
                return EdgeTTSEngine(voice_name=fallback_voice)
            return self.fishspeech
        else:
            if tier_routing.get("humanoid_engine") == "edge-tts":
                fallback_voice = config.get("narrator_preferences", {}).get("default_voice", "en-US-ChristopherNeural")
                return EdgeTTSEngine(voice_name=fallback_voice)
            
            if stats:
                race = stats.get("race", "").lower()
                level = stats.get("level", 0)
                if race in ["undead", "fiend", "aberration", "dragon"] or level > 5:
                    return self.fishspeech

            return self.cosyvoice

pipeline_factory = SpeechPipelineFactory()

async def generate_vocal_profile(actor_data: ActorMetadata) -> str:
    """Uses Qwen 2.5 via vLLM completions endpoint to generate a descriptive acoustic prompt."""
    system_instruction = (
        "You are an expert casting director and acoustic engineer. "
        "Analyze the character and output a single-sentence acoustic description for a voice synthesizer. "
        "Include age, gender, raspiness, pitch, inflections, and room acoustics."
    )
    payload = {
        "model": "Qwen/Qwen2.5-14B-Instruct",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Character: {actor_data.name}\nLore: {actor_data.lore}"}
        ],
        "temperature": 0.3,
        "max_tokens": 128
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            return response.json()["choices"][0]["message"]["content"].strip()
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
    """Enriches dialogue with Qwen via vLLM endpoint and formats tags."""
    system_instruction = (
        "You are a cinematic dialogue director. Analyze the text for emotional subtext. "
        "Output JSON with 'emotional_resonance', 'vocal_delivery_prompt', "
        "'instruct_tag' (e.g. [Terrified, breathy whisper]), "
        "and 'monster_tag' (e.g. [screaming], [low voice], [echo])."
    )
    payload = {
        "model": "Qwen/Qwen2.5-14B-Instruct",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Speaker: {speaker}, Text: {text}"}
        ],
        "temperature": 0.3,
        "max_tokens": 256,
        "response_format": {"type": "json_object"}
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            res = json.loads(response.json()["choices"][0]["message"]["content"])
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

async def log_to_foundry(npc_name: str, summary: str) -> bool:
    """Fires a secure POST request to Foundry VTT REST API to trigger a macro."""
    payload = {
        "macroName": "LogNPCSession",
        "args": [{"npcName": npc_name, "summary": summary}]
    }
    headers = {
        "Content-Type": "application/json"
    }
    if FOUNDRY_API_KEY:
        headers["Authorization"] = f"Bearer {FOUNDRY_API_KEY}"
        
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                f"{FOUNDRY_API_URL}/macros/execute", 
                json=payload, 
                headers=headers
            )
            response.raise_for_status()
            logger.info(f"Successfully logged session for {npc_name} to Foundry.")
            return True
        except Exception as e:
            logger.error(f"Error calling Foundry API: {e}")
            return False

# --- Endpoints ---

@app.get("/")
async def root():
    return {
        "service": "orchestrator",
        "status": "running",
        "version": "2.2.0",
        "pipeline": "Factory Dynamic voice generation with vLLM Qwen endpoints and VRAM failover"
    }

@app.get("/api/v1/narrators/voices")
async def get_narrator_voices():
    """Fetches all free Microsoft English neural voice names live."""
    try:
        voices = await edge_tts.list_voices()
        english_voices = [v["ShortName"] for v in voices if v["Locale"].startswith("en-")]
        return english_voices
    except Exception as e:
        logger.error(f"Failed to fetch edge-tts voice profiles: {e}")
        return ["en-US-ChristopherNeural", "en-GB-RyanNeural"]

@app.post("/api/ingest-actor")
async def ingest_actor(data: ActorMetadata):
    seed_path = VOICE_SEEDS_DIR / f"{data.actorId}_seed.wav"
    if seed_path.exists(): return {"status": "cached"}
    profile = await generate_vocal_profile(data)
    path = await forge_voice_seed(data.actorId, profile)
    return {"status": "created", "path": path} if path else {"status": "error"}

@app.post("/api/voice-conversion")
async def voice_conversion(request: Request):
    try:
        form = await request.form()
        audio_file = form.get("audio_blob")
        metadata_str = form.get("metadata")
        meta = json.loads(metadata_str)
        speaker_name = meta.get("activeSpeakerName", "Unknown")
        actor_id = meta.get("actorId", "narrator")
        mic_type = meta.get("micType", "player")
        is_monster = meta.get("isMonster", False)

        # 1. Transcribe Audio
        async with httpx.AsyncClient(timeout=30.0) as client:
            stt_resp = await client.post(
                f"{STT_URL}/v1/audio/transcriptions",
                files={"file": (audio_file.filename, await audio_file.read(), audio_file.content_type)},
                data={"model": "tiny.en", "language": "en"}
            )
            transcription = stt_resp.json().get("text", "")

        if not transcription.strip(): return {"status": "empty"}

        # 2. Enrich Text
        role = "NPC" if mic_type == "vox-conjurata-gm-puppet-mic" else "Player"
        enriched = await enrich_and_instruct(speaker_name, role, transcription)

        # 3. Determine VRAM Status & Route
        vram_used = get_vram_used_gb()
        config = load_routing_config()
        vram_threshold = config.get("system_settings", {}).get("vram_threshold_gb", 18.0)
        vram_triggered = vram_used > vram_threshold

        engine = pipeline_factory.get_engine(
            is_monster=is_monster,
            stats=meta.get("stats", {}),
            config=config,
            vram_triggered=vram_triggered
        )

        audio_data = None
        engine_name = "Edge-TTS"
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            target_text = enriched.monster_text if is_monster else enriched.instruct_text
            
            if isinstance(engine, FishSpeechEngine):
                engine_name = "Fish Speech"
            elif isinstance(engine, CosyVoiceEngine):
                engine_name = "CosyVoice"
            
            res_content = await engine.generate(target_text, actor_id, client)
            
            if res_content is None and not isinstance(engine, EdgeTTSEngine):
                logger.warn(f"Engine {engine_name} failed. Falling back to Edge-TTS Cloud.")
                engine_name = "Edge-TTS (Fallback)"
                fallback_voice = config.get("narrator_preferences", {}).get("default_voice", "en-US-ChristopherNeural")
                rate = config.get("narrator_preferences", {}).get("rate_adjustment", "+0%")
                edge_engine = EdgeTTSEngine(voice_name=fallback_voice, rate=rate)
                res_content = await edge_engine.generate(transcription, actor_id, client)

            if res_content:
                audio_base64 = base64.b64encode(res_content).decode('utf-8')
                audio_data = f"data:audio/webm;base64,{audio_base64}"

        return {
            "status": "success", "transcription": transcription, "enrichment": enriched.model_dump(),
            "voxType": "narration" if mic_type == "vox-conjurata-gm-narrate-mic" else ("puppet" if mic_type == "vox-conjurata-gm-puppet-mic" else "player"),
            "audio_data": audio_data, "engine": engine_name
        }

    except Exception as e:
        logger.error(f"Pipeline failure: {e}"); raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/dialogue/end")
async def end_dialogue(request: DialogueEndRequest):
    """Endpoint triggered when a dialogue session ends, compiling summaries using vLLM."""
    logger.info(f"Received dialogue end request for NPC: {request.npcName}")
    
    # 1. Active Chat History Truncation: Slice conversational array to last 20 messages
    lines = [l.strip() for l in request.transcript.split("\n") if l.strip()]
    truncated_lines = lines[-20:]
    truncated_transcript = "\n".join(truncated_lines)
    
    # 2. Generate summary from local vLLM API
    prompt = (
        "Summarize the following conversation transcript in a condensed markdown format. "
        "Assume preceding timeline is documented in structured journals.\n\n"
        f"{truncated_transcript}"
    )
    payload = {
        "model": "Qwen/Qwen2.5-14B-Instruct",
        "prompt": prompt,
        "max_tokens": 512,
        "temperature": 0.5
    }
    
    summary = "Summary generation failed."
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/v1/completions", json=payload)
            if response.status_code == 200:
                summary = response.json()["choices"][0]["text"].strip()
        except Exception as e:
            logger.error(f"Error calling vLLM: {e}")
            raise HTTPException(status_code=502, detail=f"vLLM integration error: {str(e)}")
            
    # 3. Log macro trigger to Foundry VTT
    foundry_success = await log_to_foundry(request.npcName, summary)
    
    if not foundry_success:
        return {
            "status": "partial_success",
            "npcName": request.npcName,
            "summary": summary,
            "warning": "Model processed summary, but backend failed to update Foundry VTT data layers."
        }
    
    return {
        "status": "success",
        "npcName": request.npcName,
        "summary": summary
    }

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
