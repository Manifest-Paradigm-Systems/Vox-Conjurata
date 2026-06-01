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
import re
import wave
import io
from pathlib import Path
import docker

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
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://vox-llm-core:8080")
STT_URL = os.getenv("STT_URL", "http://vox-voice:5000")
TTS_DESIGNER_URL = os.getenv("TTS_DESIGNER_URL", "http://vox-designer:5010")
TTS_ACTOR_URL = os.getenv("TTS_ACTOR_URL", "http://vox-actor:5020")
TTS_MONSTER_URL = os.getenv("TTS_MONSTER_URL", "http://vox-monster-fish:7860")
TTS_FALLBACK_URL = os.getenv("TTS_FALLBACK_URL", "http://vox-audio-generation-music:8000")
VISION_READER_URL = os.getenv("VISION_READER_URL", "http://vox-vision-reader:8000")
VISION_ENGINE_URL = os.getenv("VISION_ENGINE_URL", "http://vox-vision:7860")
IMAGE_GEN_URL = os.getenv("IMAGE_GEN_URL", "http://vox-vision-gen:8003")
FOUNDRY_API_URL = os.getenv("FOUNDRY_API_URL", "http://foundry-vtt:30000/api")
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY", "")

# Local paths for vision scanning (mapped volumes)
FOUNDRY_DATA_DIR = Path("/foundry_data")

# --- Vision Service Manager (Hot-Swap) ---

class VisionHotSwapManager:
    """Manages the mutual exclusivity of heavy vision containers to optimize VRAM."""
    def __init__(self):
        try:
            self.client = docker.from_env()
            logger.info("🐳 Container Manager: Docker/Podman socket connected.")
        except Exception as e:
            logger.error(f"❌ Container Manager: Failed to connect to socket: {e}")
            self.client = None
        
        self.lock = asyncio.Lock()
        self.hot_container = "vox-vision-gen"
        self.ondemand_containers = ["vox-vision-reader", "vox-vision"]

    async def swap_to(self, target_container: str):
        """Evicts the hot container and starts the target on-demand container."""
        if not self.client:
            logger.warning(f"⚠️ Container Manager: Bypass swap to {target_container} (No client)")
            return
            
        async with self.lock:
            try:
                logger.info(f"🔄 Swapping: Evicting {self.hot_container} -> Loading {target_container}")
                
                # 1. Stop the hot generator to free ~5.5GB VRAM
                try:
                    gen = self.client.containers.get(self.hot_container)
                    if gen.status == "running":
                        gen.stop(timeout=2)
                        logger.info(f"🛑 Paused {self.hot_container}")
                except Exception as ex:
                    logger.warning(f"Failed to stop {self.hot_container}: {ex}")
                
                # 2. Start the requested analytical service
                target = self.client.containers.get(target_container)
                target.start()
                
                # 3. Wait for service to be ready (healthcheck fallback)
                await asyncio.sleep(3.0) 
                logger.info(f"🚀 Started {target_container}")
                
            except Exception as e:
                logger.error(f"❌ Swap-To Failed: {e}")

    async def restore_hot_state(self, current_container: str):
        """Stops the on-demand container and restores the default hot generator."""
        if not self.client: return
            
        async with self.lock:
            try:
                logger.info(f"🔄 Restoring: Stopping {current_container} -> Warming {self.hot_container}")
                
                # 1. Stop the on-demand task
                try:
                    current = self.client.containers.get(current_container)
                    current.stop(timeout=2)
                except Exception: pass
                
                # 2. Re-warm the generator
                gen = self.client.containers.get(self.hot_container)
                gen.start()
                logger.info(f"🔥 {self.hot_container} is back online.")
                
            except Exception as e:
                logger.error(f"❌ Restore Failed: {e}")

hotswap_manager = VisionHotSwapManager()

# Cache for Voice Seeds
VOICE_SEEDS_DIR = Path("./voice_seeds")
VOICE_SEEDS_DIR.mkdir(exist_ok=True)

CONFIG_PATH = Path("./settings/voice_routing_config.json")

# --- Helper Functions ---

def split_into_sentences(text: str) -> List[str]:
    # Split by punctuation followed by space, or by newline
    raw_sentences = re.split(r'(?<=[.!?])\s+|\n+', text)
    return [s.strip() for s in raw_sentences if s.strip()]

def concatenate_wavs(wav_bytes_list: List[bytes]) -> Optional[bytes]:
    if not wav_bytes_list:
        return None
    # Filter out empty or None responses
    valid_wavs = [w for w in wav_bytes_list if w]
    if not valid_wavs:
        return None
    if len(valid_wavs) == 1:
        return valid_wavs[0]
        
    try:
        first_wav = wave.open(io.BytesIO(valid_wavs[0]), 'rb')
        params = first_wav.getparams()
        
        output_io = io.BytesIO()
        out_wav = wave.open(output_io, 'wb')
        out_wav.setparams(params)
        
        for w_bytes in valid_wavs:
            w_file = wave.open(io.BytesIO(w_bytes), 'rb')
            out_wav.writeframes(w_file.readframes(w_file.getnframes()))
            w_file.close()
            
        out_wav.close()
        first_wav.close()
        return output_io.getvalue()
    except Exception as e:
        logger.error(f"Error concatenating WAVs: {e}")
        return valid_wavs[0]


def standardize_speech_text(text: str, engine_type: str, emotion: str) -> str:
    """Maps and formats emotional tags and sound effects to engine-specific syntax."""
    import re
    
    # 1. Strip EXISTING tags to avoid double-processing and standardization
    # This removes [neutral], (happy), "Mood: sad", etc.
    clean_text = re.sub(r'\[.*?\]|\(.*?\)|\w+:\s*', '', text).strip()
    
    # 2. Sound Effect Parser (*gasp* -> <gasp> for CosyVoice)
    if engine_type == "cosyvoice":
        # Translate *action* into <action>
        clean_text = re.sub(r'\*(.*?)\*', r'<>', clean_text)
    else:
        # Strip SFX for engines that don't support acoustic generation
        clean_text = re.sub(r'\*.*?\*', '', clean_text)

    # 3. Engine-Specific Syntax Mapping
    if engine_type == "fish-speech":
        # Monster: strict square brackets [emotion] at start
        return f"[{emotion.lower()}] {clean_text}"
    elif engine_type == "cosyvoice":
        # Humanoid: inline parentheses (emotion) at start
        return f"({emotion.lower()}) {clean_text}"
    else:
        # Fallback/Edge-TTS: Just the clean text
        return clean_text

def get_vram_used_gb() -> float:
    """Reads current GPU VRAM utilization from Host Linux sysfs dynamically."""
    try:
        for path in Path("/sys/class/drm").glob("card*/device/mem_info_vram_used"):
            try:
                with open(path, "r") as f:
                    used_bytes = int(f.read().strip())
                    return used_bytes / (1024 ** 3)
            except Exception:
                continue
    except Exception:
        pass
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
        seeds = list(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.wav"))
        seed_path = seeds[0] if seeds else None
        
        if not seed_path:
            logger.info(f"[VOICE-ROUTING] No seed found for {actor_id}. Forging new seed...")
            seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_male.wav"
            await forge_voice_seed(actor_id, f"A clear speaking voice for {actor_id}.", "male")
        
        if seed_path and seed_path.exists():
            logger.info(f"[VOICE-ROUTING] Using voice seed: {seed_path.name}")
            try:
                # Opportunity 1: Send dummy file for Jarvis to avoid 15MB disk read and HTTP upload
                is_jarvis = "jarvis" in seed_path.name.lower() or actor_id.lower() == "jarvis"
                
                if is_jarvis:
                    logger.info("[VOICE-ROUTING] Jarvis detected — sending dummy reference file (fast-path)")
                    files = {"reference_audio": (seed_path.name, b"dummy", "audio/wav")}
                    f_handle = None
                else:
                    f_handle = open(seed_path, "rb")
                    files = {"reference_audio": (seed_path.name, f_handle, "audio/wav")}

                try:
                    resp = await client.post(
                        f"{TTS_ACTOR_URL}/api/tts",
                        data={"text": text},
                        files=files
                    )
                finally:
                    if f_handle:
                        f_handle.close()

                if resp.status_code == 200:
                    return resp.content
                else:
                    logger.error(f"[VOICE-ROUTING] CosyVoice service returned error {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"[VOICE-ROUTING] CosyVoice inference failed: {e}")
        else:
            logger.error(f"[VOICE-ROUTING] Failed to locate or create seed for {actor_id} at {seed_path}")
        return None

class FishSpeechEngine(SpeechEngine):
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient) -> Optional[bytes]:
        seeds = list(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.wav"))
        seed_path = seeds[0] if seeds else None
        
        # Fish Speech 1.5 prefers a reference audio for in-context learning
        if not seed_path:
            seed_path = VOICE_SEEDS_DIR / "narrator_seed_male.wav"

        try:
            # Prepare references in the format Fish Speech API expects (Base64 encoded)
            import base64
            references = []
            if seed_path and seed_path.exists():
                # Look for matching transcript
                text_path = seed_path.with_suffix(".txt")
                ref_text = ""
                if text_path.exists():
                    with open(text_path, "r") as f:
                        ref_text = f.read().strip()
                else:
                    # Fallback text if no .txt file found
                    ref_text = "A clear speaking voice."

                logger.info(f"[VOICE-ROUTING] Fish Speech using reference: {seed_path.name} with transcript: '{ref_text[:30]}...'")
                
                with open(seed_path, "rb") as f:
                    audio_b64 = base64.b64encode(f.read()).decode("utf-8")
                    references.append({
                        "audio": audio_b64,
                        "text": ref_text
                    })

            payload = {
                "text": text,
                "references": references,
                "format": "wav",
                "normalize": True,
                "latency": "normal"
            }
            
            resp = await client.post(f"{TTS_MONSTER_URL}/v1/tts", json=payload)
            if resp.status_code == 200:
                return resp.content
            else:
                logger.error(f"[VOICE-ROUTING] Fish Speech service returned error {resp.status_code}")
        except Exception as e:
            logger.error(f"[VOICE-ROUTING] Fish Speech inference failed: {e}")
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
        # Edge-TTS SUPPRESSED: never route to Edge-TTS regardless of VRAM or config.
        if vram_triggered:
            logger.warning("[VRAM] Threshold triggered but Edge-TTS is suppressed. Using local engine.")

        tier_routing = config.get("tier_routing", {})

        if is_monster:
            if tier_routing.get("monster_engine") == "edge-tts":
                logger.warning("[ROUTING] Config requests edge-tts for monster — suppressed, using Fish Speech.")
            return self.fishspeech
        else:
            if tier_routing.get("humanoid_engine") == "edge-tts":
                logger.warning("[ROUTING] Config requests edge-tts for humanoid — suppressed, using CosyVoice.")

            if stats:
                race = stats.get("race", "").lower()
                level = stats.get("level", 0)
                if race in ["undead", "fiend", "aberration", "dragon"] or level > 5:
                    return self.fishspeech

            return self.cosyvoice

pipeline_factory = SpeechPipelineFactory()

async def generate_vocal_profile(actor_data: ActorMetadata) -> dict:
    """Uses Qwen 2.5 via vLLM completions endpoint to generate a descriptive acoustic prompt and gender."""
    system_instruction = (
        "You are an expert casting director and acoustic engineer. "
        "Analyze the character name and biography. Output a JSON object with: "
        "'gender' (strictly 'male' or 'female') and "
        "'description' (a single-sentence acoustic description including age, raspiness, pitch, inflections, and room acoustics)."
    )
    payload = {
        "model": "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Character Name: {actor_data.name}\nBiography/Lore: {actor_data.lore}"}
        ],
        "temperature": 0.3,
        "max_tokens": 256,
        "response_format": {"type": "json_object"}
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            res = json.loads(response.json()["choices"][0]["message"]["content"])
            return {
                "gender": res.get("gender", "male").lower().strip(),
                "description": res.get("description", "A clear, neutral speaking voice.")
            }
        except Exception as e:
            logger.error(f"Profile error: {e}")
            desc_lower = (actor_data.name + " " + actor_data.lore).lower()
            is_female = any(w in desc_lower for w in ["female", "woman", "girl", "lady", "queen", "goddess", "mother", "sister", "wife", "she", "her", "herself"])
            gender = "female" if is_female else "male"
            return {
                "gender": gender,
                "description": "A clear, neutral speaking voice."
            }

async def forge_voice_seed(actor_id: str, acoustic_description: str, gender: str = "male") -> str:
    """Calls Parler-TTS (vox-designer) to create a unique 10s voice print."""
    seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_{gender}.wav"
    text_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_{gender}.txt"
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            response = await client.post(f"{TTS_DESIGNER_URL}/generate", json={"text": acoustic_description})
            if response.status_code == 200:
                with open(seed_path, "wb") as f: f.write(response.content)
                with open(text_path, "w") as f: f.write(acoustic_description)
                logger.info(f"[VOICE-SEED] Forged seed and saved transcript for {actor_id}")
                return str(seed_path)
            return ""
        except Exception as e:
            logger.error(f"Seed forge error: {e}"); return ""

async def enrich_and_instruct(speaker: str, role: str, text: str) -> DialogueEnrichment:
    """Enriches dialogue with Qwen via vLLM endpoint and formats tags for specific TTS engines."""
    system_instruction = (
        "You are a cinematic dialogue director. Analyze the text for emotional subtext. "
        "Output JSON with 'emotional_resonance', 'vocal_delivery_prompt', "
        "and 'emotion_tag' (a single descriptive tag like 'Enraged Growl', 'Terrified Whisper', or 'Neutral')."
    )
    payload = {
        "model": "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1",
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
            
            emotion = res.get("emotion_tag", "Neutral").strip()
            
            # Apply engine-specific syntax mapping and SFX parsing
            monster_text = standardize_speech_text(text, "fish-speech", emotion)
            instruct_text = standardize_speech_text(text, "cosyvoice", emotion)
            
            return DialogueEnrichment(
                speaker=speaker, role=role, raw_text=text,
                emotional_resonance=str(res.get("emotional_resonance", emotion)),
                vocal_delivery_prompt=res.get("vocal_delivery_prompt", f"Deliver as {emotion}."),
                instruct_text=instruct_text,
                monster_text=monster_text
            )
        except Exception as e:
            logger.error(f"Instruction error: {e}")
            return DialogueEnrichment(
                speaker=speaker, role=role, raw_text=text, 
                emotional_resonance="Neutral", vocal_delivery_prompt="Standard.",
                instruct_text=f"Neutral <|endofprompt|> {text}",
                monster_text=f"[neutral] {text}"
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
    """Edge-TTS suppressed: returns empty list. Narrator voice is driven by CosyVoice seed."""
    return []

@app.post("/api/ingest-actor")
async def ingest_actor(data: ActorMetadata, force_refresh: bool = False):
    """Ingest an actor and forge their voice seed.
    
    Set force_refresh=true to bypass the seed cache and regenerate even if a
    seed file already exists on disk. Required after a cache purge.
    """
    existing_seeds = list(VOICE_SEEDS_DIR.glob(f"{data.actorId}_seed_*.wav"))
    if existing_seeds and not force_refresh:
        logger.info(f"[INGEST] Cache hit for {data.actorId} ({data.name}), returning cached seed.")
        return {"status": "cached", "seeds": [s.name for s in existing_seeds]}

    if existing_seeds and force_refresh:
        logger.info(f"[INGEST] force_refresh=True — purging {len(existing_seeds)} stale seed(s) for {data.actorId}")
        for stale in existing_seeds:
            stale.unlink(missing_ok=True)
            stale.with_suffix(".txt").unlink(missing_ok=True)

    profile_data = await generate_vocal_profile(data)
    profile_desc = profile_data.get("description", "A clear, neutral speaking voice.")
    gender = profile_data.get("gender", "male").lower().strip()
    if gender not in ["male", "female"]:
        gender = "male"

    path = await forge_voice_seed(data.actorId, profile_desc, gender)
    return {"status": "created", "path": path} if path else {"status": "error"}

class CachePurgeRequest(BaseModel):
    actor_ids: Optional[List[str]] = Field(
        default=None,
        description="List of actorIds to purge. Omit or pass empty list to purge ALL non-narrator seeds."
    )
    preserve_narrator: bool = Field(
        default=True,
        description="Keep narrator_seed_male.wav and narrator_seed_male.txt intact."
    )

@app.post("/api/v1/cache/purge")
async def purge_voice_cache(req: CachePurgeRequest = CachePurgeRequest()):
    """Force-purge stale voice seed files so actors will have fresh seeds generated
    on their next /api/ingest-actor call.

    - Omit body (or send {}) to nuke all non-narrator seeds.
    - Supply actor_ids list to target specific tokens only.
    - preserve_narrator=false to also wipe the narrator fallback seed.
    """
    purged: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    # Resolve target files
    if req.actor_ids:
        candidates = []
        for actor_id in req.actor_ids:
            candidates.extend(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.wav"))
            candidates.extend(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.txt"))
    else:
        # All seeds except narrator if preserve_narrator is set
        candidates = list(VOICE_SEEDS_DIR.glob("*_seed_*.*"))

    for file in candidates:
        if req.preserve_narrator and file.name.startswith("narrator_"):
            skipped.append(file.name)
            continue
        try:
            file.unlink(missing_ok=True)
            purged.append(file.name)
            logger.info(f"[CACHE-PURGE] Removed: {file.name}")
        except Exception as exc:
            errors.append(f"{file.name}: {exc}")
            logger.error(f"[CACHE-PURGE] Failed to remove {file.name}: {exc}")

    logger.info(f"[CACHE-PURGE] Done — purged={len(purged)}, skipped={len(skipped)}, errors={len(errors)}")
    return {
        "status": "ok" if not errors else "partial",
        "purged_count": len(purged),
        "purged": purged,
        "skipped": skipped,
        "errors": errors,
        "message": (
            f"Purged {len(purged)} seed file(s). "
            f"Re-ingest actors to generate fresh voice profiles."
        )
    }

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
        engine_name = "Unknown"

        async with httpx.AsyncClient(timeout=120.0) as client:
            target_text = enriched.monster_text if is_monster else enriched.instruct_text

            if isinstance(engine, FishSpeechEngine):
                engine_name = "Fish Speech"
            elif isinstance(engine, CosyVoiceEngine):
                engine_name = "CosyVoice"

            # Opportunity 2: Split text into sentences and process concurrently, then concatenate
            sentences = split_into_sentences(target_text)
            logger.info(f"[VOICE-ROUTING] Dialogue text split into {len(sentences)} sentences: {sentences}")
            
            if len(sentences) <= 1:
                res_content = await engine.generate(target_text, actor_id, client)
            else:
                logger.info(f"[VOICE-ROUTING] Running concurrent synthesis for {len(sentences)} sentences...")
                tasks = [engine.generate(s, actor_id, client) for s in sentences]
                results = await asyncio.gather(*tasks)
                
                # Check if all sentences failed
                if not any(results):
                    res_content = None
                else:
                    res_content = concatenate_wavs(results)

            if res_content is None:
                # Edge-TTS SUPPRESSED: do not fall back to cloud TTS.
                logger.error(f"🚨 [PIPELINE-CRITICAL] {engine_name} failed for {actor_id}. Edge-TTS suppressed — returning empty response.")

            if res_content:
                # Detect audio format using magic bytes
                mime_type = "audio/wav"
                if res_content.startswith(b"RIFF"):
                    mime_type = "audio/wav"
                elif res_content.startswith(b"\x1a\x45\xdf\xa3"):
                    mime_type = "audio/webm"
                elif res_content.startswith(b"ID3") or res_content.startswith(b"\xff\xfb") or res_content.startswith(b"\xff\xf3") or res_content.startswith(b"\xff\xf2"):
                    mime_type = "audio/mpeg"
                
                audio_base64 = base64.b64encode(res_content).decode('utf-8')
                audio_data = f"data:{mime_type};base64,{audio_base64}"

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
        "model": "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1",
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

@app.get("/api/v1/diagnostics/history")
async def get_diagnostics_history():
    return error_buffer


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
