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
import time
import re
import wave
import io
from pathlib import Path
import subprocess

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
IMAGE_GEN_URL = os.getenv("IMAGE_GEN_URL", "http://vox-vision-gen:8003")
FOUNDRY_API_URL = os.getenv("FOUNDRY_API_URL", "http://foundry-vtt:30000/api")
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY", "")
TTS_SFX_URL = os.getenv("TTS_SFX_URL", "http://vox-audio-generation-sfx:8001")
W_OKADA_URL = os.getenv("W_OKADA_URL", "http://127.0.0.1:18888")

# Local paths for vision scanning (mapped volumes)
FOUNDRY_DATA_DIR = Path("/foundry_data")
SFX_DIR = Path("/sfx_out")

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
    
    # 1. Strip ALL tags to avoid spoken descriptions (e.g. "[angry]")
    # This removes [neutral], (happy), "Mood: sad", etc.
    clean_text = re.sub(r'\[.*?\]|\(.*?\)|\w+:\s*', '', text).strip()
    
    # 2. Sound Effect Parser (*gasp* -> <gasp> for CosyVoice)
    if engine_type == "cosyvoice":
        # Translate *action* into <action>
        clean_text = re.sub(r'\*(.*?)\*', r'<\1>', clean_text)
    else:
        # Strip SFX for engines that don't support acoustic generation
        clean_text = re.sub(r'\*.*?\*', '', clean_text)

    # Return only the cleaned text to avoid engine 'reading' the instruction
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
    customDescription: Optional[str] = ""

class DialogueEnrichment(BaseModel):
    speaker: str
    role: str
    raw_text: str
    emotional_resonance: str
    vocal_delivery_prompt: str
    instruct_text: str 
    monster_text: str
    emotion_tag: str = "neutral"

class DialogueEndRequest(BaseModel):
    npcName: str
    transcript: str

class BattlemapScanRequest(BaseModel):
    imagePath: str
    sceneId: str


# --- Helpers for battlemap scanning ---

def _extract_scan_contract(raw_text: str, scene_id: str) -> dict:
    """Parse the vision model's free-text response into a structured contract.

    Returns a dict with keys ``image``, ``walls``, ``lights``, ``sound_sources``.
    On any parse failure returns an empty contract — the caller always gets
    something valid, never throws.
    """
    contract: dict = {
        "image": {},
        "walls": [],
        "lights": [],
        "sound_sources": [],
    }

    if not raw_text:
        logger.warning(f"🗺️ Scan [{scene_id}]: Empty response from vision reader")
        return contract

    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*\n?", "", raw_text, flags=re.IGNORECASE)
    text = re.sub(r"\n?```\s*$", "", text)

    # Extract the first JSON object
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        logger.warning(f"🗺️ Scan [{scene_id}]: No JSON object found; raw={raw_text[:300]}")
        return contract

    try:
        parsed: dict = json.loads(m.group())
    except json.JSONDecodeError as exc:
        logger.warning(f"🗺️ Scan [{scene_id}]: JSON parse error {exc}; raw={raw_text[:300]}")
        return contract

    # --- image ---
    contract["image"] = {"width": int(parsed.get("image", {}).get("width", 1024))}

    # --- walls ---
    for w in parsed.get("walls", []):
        c = w.get("c", [])
        if not isinstance(c, list) or len(c) != 4:
            continue
        try:
            c_clean = [float(v) for v in c]
        except (TypeError, ValueError):
            continue
        if any(v < 0.0 or v > 1.0 for v in c_clean):
            continue
        contract["walls"].append({
            "c": c_clean,
            "door": int(w.get("door", 0)),
            "ds": int(w.get("ds", 0)),
        })

    # --- lights ---
    for li in parsed.get("lights", []):
        try:
            contract["lights"].append({
                "x": min(1.0, max(0.0, float(li["x"]))),
                "y": min(1.0, max(0.0, float(li["y"]))),
                "dim": float(li.get("dim", 6)),
                "bright": float(li.get("bright", 3)),
                "color": str(li.get("color", "#ffaa55")),
                "animation": li.get("animation") or None,
            })
        except (TypeError, ValueError, KeyError):
            continue

    # --- sound sources ---
    for s in parsed.get("sound_sources", []):
        try:
            contract["sound_sources"].append({
                "x": min(1.0, max(0.0, float(s["x"]))),
                "y": min(1.0, max(0.0, float(s["y"]))),
                "radius_units": float(s.get("radius_units", 8)),
                "sfx_description": str(s.get("sfx_description", "Ambient background")),
                "duration_seconds": float(s.get("duration_seconds", 5.0)),
            })
        except (TypeError, ValueError, KeyError):
            continue

    logger.info(
        f"🗺️ Scan [{scene_id}]: parsed "
        f"{len(contract['walls'])} walls, "
        f"{len(contract['lights'])} lights, "
        f"{len(contract['sound_sources'])} sound sources"
    )
    return contract

# --- Storage ---
error_buffer: List[dict] = []

@app.post("/api/scan-battlemap")
async def scan_battlemap(req: BattlemapScanRequest):
    """Triggers vox-vision-reader to analyze a battlemap for walls, doors, and lights."""
    logger.info(f"🗺️ Battlemap Scan requested for Scene: {req.sceneId}")
    
    full_path = FOUNDRY_DATA_DIR / req.imagePath
    if not full_path.exists():
        logger.error(f"🗺️ Battlemap Scan: File not found at {full_path}")
        # Try without leading slash if absolute fails
        if req.imagePath.startswith("/"):
            full_path = FOUNDRY_DATA_DIR / req.imagePath.lstrip("/")
            
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"Battlemap image not found at {req.imagePath}")

    try:
        # vox-vision-reader (MiniCPM-V) is the image-understanding service.

        # Encode the battlemap as a data URI for the OpenAI-style vision API.
        with open(full_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode('utf-8')
        data_uri = f"data:image/png;base64,{img_b64}"

        # MiniCPM-V via llama-cpp-python speaks the OpenAI chat-completions API.
        vision_payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analyze this tabletop battlemap image. "
                                "Identify walls (including any door walls), lights, and sound-emitting objects.\n\n"
                                "Respond ONLY with a valid JSON object. Use EXACTLY these keys:\n"
                                "{\n"
                                '  "image": {"width": <pixel_width>},\n'
                                '  "walls": [{"c": [x0, y0, x1, y1], "door": 0|1}],\n'
                                '  "lights": [{"x": fx, "y": fy, "dim": 1-10, "bright": 1-5, '
                                '"color": "#hex", "animation": "torch"|"pulse"|null}],\n'
                                '  "sound_sources": [{"x": fx, "y": fy, "radius_units": 1-20, '
                                '"sfx_description": "<vivid 5-15 word ambient sound description>", '
                                '"duration_seconds": 2-10}]\n'
                                "}\n\n"
                                "IMPORTANT:\n"
                                "- All coords (c, x, y) MUST be NORMALIZED 0.0-1.0 (fraction of image width/height).\n"
                                "- Set door=1 for walls that are doors, door=0 for ordinary walls.\n"
                                "- For lights, dim > bright; choose a sensible hex color.\n"
                                "- For each sound source, write a vivid sfx_description that "
                                "could be fed to an audio generation model (e.g. 'crackling "
                                "campfire, dry logs popping').\n"
                                "- radius_units is relative loudness 1-20.\n"
                                "- duration_seconds is the ideal clip length 2-10.\n"
                                "- If no walls/lights/sounds exist, use empty arrays."
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            "temperature": 0.1,
        }

        async with httpx.AsyncClient(timeout=300.0) as vision_client:
            resp = await vision_client.post(f"{VISION_READER_URL}/v1/chat/completions", json=vision_payload)
            if resp.status_code != 200:
                logger.error(f"🗺️ Battlemap Scan: Vision reader returned {resp.status_code}")
                return {"status": "error", "message": f"Vision reader returned {resp.status_code}"}

            analysis_data = resp.json()
            raw_text = (
                analysis_data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

        # --- Robust JSON extraction ---
        contract = _extract_scan_contract(raw_text, req.sceneId)

        # --- Sequential SFX generation for each sound source ---
        sound_entries = []
        if contract.get("sound_sources"):
            async with httpx.AsyncClient(timeout=300.0) as sfx_client:
                for idx, src in enumerate(contract["sound_sources"]):
                    dur = max(1.0, min(12.0, float(src.get("duration_seconds", 5.0))))
                    desc = src.get("sfx_description", "Ambient background")
                    logger.info(f"🔊 Generating SFX {idx+1}/{len(contract['sound_sources'])}: {desc[:60]}...")
                    try:
                        r = await sfx_client.post(
                            f"{TTS_SFX_URL}/generate",
                            json={"prompt": desc, "duration_seconds": dur},
                        )
                        if r.status_code == 200:
                            # Transcode WAV → Opus OGG for ~90% smaller files
                            import subprocess as _sp
                            proc = _sp.run(
                                ["ffmpeg", "-i", "pipe:0", "-c:a", "libopus",
                                 "-b:a", "64k", "-f", "ogg", "pipe:1"],
                                input=r.content, capture_output=True, timeout=30,
                            )
                            if proc.returncode != 0:
                                logger.warning(f"   ⚠️ ffmpeg transcoding failed: {proc.stderr.decode(errors='replace')[:200]}")
                                continue
                            fname = f"scan-{req.sceneId}-{idx}.ogg"
                            (SFX_DIR / fname).write_bytes(proc.stdout)
                            src["audio_path"] = f"audio/sfx/{fname}"
                            sound_entries.append(src)
                            logger.info(f"   ✅ Saved {fname}")
                        else:
                            logger.warning(f"   ⚠️ SFX gen returned {r.status_code}")
                    except Exception as e:
                        logger.error(f"   ❌ SFX gen failed: {e}")
            contract["sound_sources"] = sound_entries

        logger.info(f"🗺️ Battlemap Scan Complete for {req.sceneId}")
        return {"status": "success", "data": contract}

    except Exception as e:
        logger.error(f"🗺️ Battlemap Scan Error: {e}")
        return {"status": "error", "message": str(e)}

# --- Voice Generation Engines (Modular Factory Pattern) ---

class SpeechEngine:
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient, emotion: str = "default") -> Optional[bytes]:
        raise NotImplementedError()

class CosyVoiceEngine(SpeechEngine):
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient, emotion: str = "default") -> Optional[bytes]:
        seeds = list(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.wav"))
        seed_path = seeds[0] if seeds else None
        
        if not seed_path:
            logger.info(f"[VOICE-ROUTING] No seed found for {actor_id}. Forging new seed...")
            # We don't have full metadata here, so we use a generic but ID-specific prompt 
            # to ensure variety until a full ingest happens.
            seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_male.wav"
            await forge_voice_seed(actor_id, f"A unique, expressive voice for character {actor_id}.", "male")
        
        if seed_path and seed_path.exists():
            logger.info(f"[VOICE-ROUTING] Using voice seed: {seed_path.name}")
            try:
                # Character-specific prompt: the words spoken in the seed audio.
                # Look for matching transcript
                text_path = seed_path.with_suffix(".txt")
                ref_text = ""
                if text_path.exists():
                    with open(text_path, "r") as f:
                        ref_text = f.read().strip()
                
                # CosyVoice 3 requires a real reference audio for zero-shot cloning.
                f_handle = open(seed_path, "rb")
                files = {"reference_audio": (seed_path.name, f_handle, "audio/wav")}

                try:
                    resp = await client.post(
                        f"{TTS_ACTOR_URL}/api/tts",
                        data={
                            "text": text,
                            "prompt_text": ref_text,
                            "emotion": emotion
                        },
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
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient, emotion: str = "default") -> Optional[bytes]:
        seeds = list(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.wav"))
        seed_path = seeds[0] if seeds else None
        
        if not seed_path:
            logger.info(f"[VOICE-ROUTING] Fish Speech: No seed found for {actor_id}. Forging new seed...")
            seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_male.wav"
            await forge_voice_seed(actor_id, f"A deep, gravelly voice for character {actor_id}.", "male")

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
        # CosyVoice 3 is the only engine fast enough for 2s latency.
        # We use it for everything, but Fish Speech is kept for manual overrides.
        return self.cosyvoice

pipeline_factory = SpeechPipelineFactory()

def load_routing_config() -> dict:
    """Loads routing and system configuration from local JSON."""
    if not CONFIG_PATH.exists():
        return {
            "tier_routing": {"humanoid_engine": "cosyvoice", "monster_engine": "cosyvoice"},
            "system_settings": {"vram_threshold_gb": 26.0}
        }
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"system_settings": {"vram_threshold_gb": 26.0}}

async def generate_vocal_profile(actor_data: ActorMetadata, visual_description: str = "") -> dict:
    """Uses Qwen 2.5 via vLLM completions endpoint to generate a descriptive acoustic prompt and gender."""
    system_instruction = (
        "You are an expert casting director and acoustic engineer. "
        "Analyze the character name, biography, and physical appearance. Output a JSON object with: "
        "'gender' (strictly 'male' or 'female') and "
        "'description' (a single-sentence acoustic description including age, raspiness, pitch, inflections, and room acoustics)."
    )
    
    appearance_info = f"\nPhysical Appearance: {visual_description}" if visual_description else ""
    
    payload = {
        "model": "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Character Name: {actor_data.name}\nBiography/Lore: {actor_data.lore}{appearance_info}"}
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
            
            # Better fallback description to avoid 'flat' voices
            pitch = "high-pitched" if is_female else "low-pitched"
            vocal_trait = "raspy" if "monster" in desc_lower or "warrior" in desc_lower else "clear"
            fallback_desc = f"A {vocal_trait}, {pitch} {gender} voice with natural inflections and a professional tone."
            
            return {
                "gender": gender,
                "description": fallback_desc
            }

async def forge_voice_seed(actor_id: str, acoustic_description: str, gender: str = "male", is_monster: bool = False) -> str:
    """Creates a unique 10s voice print. Uses Fish Speech for monsters to get beastly textures."""
    seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_{gender}.wav"
    text_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_{gender}.txt"
    
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            seed_text = "Hello, I am a character in this world, and this is my unique voice."
            if is_monster:
                logger.info(f"[VOICE-SEED] Using Fish Speech for monster seed: {actor_id}")
                narrator_wav = VOICE_SEEDS_DIR / "narrator_seed_male.wav"
                narrator_b64 = base64.b64encode(narrator_wav.read_bytes()).decode("utf-8") if narrator_wav.exists() else ""
                
                payload = {
                    "text": seed_text, # Monsters now speak the anchor phrase for their seed
                    "references": [{"audio": narrator_b64, "text": "A clear speaking voice."}] if narrator_b64 else [],
                    "format": "wav"
                }
                response = await client.post(f"{TTS_MONSTER_URL}/v1/tts", json=payload)
            else:
                logger.info(f"[VOICE-SEED] Using Parler-TTS for humanoid seed: {actor_id}")
                # We pass the acoustic_description as the STYLE, and seed_text as the DIALOGUE
                response = await client.post(f"{TTS_DESIGNER_URL}/generate", json={
                    "text": acoustic_description,
                    "prompt_text": seed_text
                })

            if response.status_code == 200:
                with open(seed_path, "wb") as f: f.write(response.content)
                # CRITICAL: Save the actual SPOKEN text, not the acoustic description
                with open(text_path, "w") as f: f.write(seed_text)
                logger.info(f"[VOICE-SEED] Forged { 'monster' if is_monster else 'humanoid' } seed for {actor_id}")
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
                monster_text=monster_text,
                emotion_tag=emotion
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

async def get_visual_description(image_path_relative: str) -> str:
    """Uses MiniCPM-V-2.6 (vox-vision-reader) to describe the character image."""
    full_path = FOUNDRY_DATA_DIR / image_path_relative
    if not full_path.exists():
        logger.warning(f"🖼️ Visual Scan: Image not found at {full_path}")
        return ""

    try:
        await hotswap_manager.swap_to("vox-vision-reader")
        
        # Prepare the image payload for llama-cpp-python vision endpoint
        # MiniCPM-V doesn't support WebP, so convert to PNG if needed
        import base64
        import io
        raw_bytes = full_path.read_bytes()
        imghdr_lower = full_path.suffix.lower()
        mime_type = "image/png"
        
        if imghdr_lower in (".webp", ".jpg", ".jpeg"):
            try:
                from PIL import Image as _PIL
                pil_img = _PIL.open(full_path)
                buf = io.BytesIO()
                pil_img.convert("RGB").save(buf, format="PNG")
                raw_bytes = buf.getvalue()
                logger.info(f"🖼️ Converted {full_path.suffix} → PNG for vision reader")
                mime_type = "image/png"
            except ImportError:
                logger.warning("🖼️ PIL not available, sending raw bytes (may fail)")
                mime_type = "image/webp" if imghdr_lower == ".webp" else "image/jpeg"
        elif imghdr_lower == ".png":
            mime_type = "image/png"
        else:
            mime_type = f"image/{imghdr_lower.replace('.', '')}"

        base64_image = base64.b64encode(raw_bytes).decode('utf-8')
            
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe the physical appearance of this creature or person in detail. Focus on race, species, age, gender, and distinguishing features like raspiness or vocal potential indicators."},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                    ]
                }
            ],
            "max_tokens": 300
        }
        
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{VISION_READER_URL}/v1/chat/completions", json=payload)
            if resp.status_code == 200:
                desc = resp.json()["choices"][0]["message"]["content"]
                logger.info(f"🖼️ Visual Scan Success: {desc[:100]}...")
                return desc
            else:
                logger.error(f"🖼️ Visual Scan Failed: {resp.status_code}")
                return ""
    except Exception as e:
        logger.error(f"🖼️ Visual Scan Error: {e}")
        return ""
    finally:
        await hotswap_manager.restore_hot_state("vox-vision-reader")

@app.post("/api/ingest-actor")
async def ingest_actor(data: ActorMetadata, force_refresh: bool = False):
    """Ingest an actor and forge their voice seed.
    
    If no seed exists, it triggers a visual scan via vox-vision-reader
    to inform the vocal profile generation.
    """
    existing_seeds = list(VOICE_SEEDS_DIR.glob(f"{data.actorId}_seed_*.wav"))
    
    visual_desc = ""
    
    if (not existing_seeds) or force_refresh:
        # Perform Visual Analysis ONLY if we are creating a new seed
        logger.info(f"[INGEST] No seed found for {data.name}. Triggering visual analysis...")
        visual_desc = await get_visual_description(data.artPath)

    if existing_seeds and not force_refresh:
        logger.info(f"[INGEST] Cache hit for {data.actorId} ({data.name}), returning cached seed.")
        return {"status": "cached", "seeds": [s.name for s in existing_seeds]}

    if existing_seeds and force_refresh:
        logger.info(f"[INGEST] force_refresh=True — purging {len(existing_seeds)} stale seed(s) for {data.actorId}")
        for stale in existing_seeds:
            stale.unlink(missing_ok=True)
            stale.with_suffix(".txt").unlink(missing_ok=True)

    # GM Override Check: If the GM provided a manual description, skip visual scan/lore logic
    if data.customDescription:
        logger.info(f"[INGEST] GM Override provided for {data.name}: {data.customDescription}")
        profile_desc = data.customDescription
        is_female = any(w in profile_desc.lower() for w in ["female", "woman", "lady", "she", "her"])
        gender = "female" if is_female else "male"
    else:
        profile_data = await generate_vocal_profile(data, visual_desc)
        profile_desc = profile_data.get("description", "A clear, neutral speaking voice.")
        gender = profile_data.get("gender", "male").lower().strip()

    if gender not in ["male", "female"]:
        gender = "male"

    path = await forge_voice_seed(data.actorId, profile_desc, gender, is_monster=data.isMonster)
    return {"status": "created", "path": path, "visual_description": visual_desc} if path else {"status": "error"}

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
    start_time_total = time.time()
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
        stt_start = time.time()
        async with httpx.AsyncClient(timeout=300.0) as client:
            stt_resp = await client.post(
                f"{STT_URL}/v1/audio/transcriptions",
                files={"file": (audio_file.filename, await audio_file.read(), audio_file.content_type)},
                data={"model": "tiny.en", "language": "en"}
            )
            transcription = stt_resp.json().get("text", "")
        logger.info(f"[PERF] STT took {time.time() - stt_start:.2f}s")

        if not transcription.strip(): return {"status": "empty"}

        # 2. Enrich Text
        enrich_start = time.time()
        role = "NPC" if mic_type == "vox-conjurata-gm-puppet-mic" else "Player"
        enriched = await enrich_and_instruct(speaker_name, role, transcription)
        logger.info(f"[PERF] Enrichment took {time.time() - enrich_start:.2f}s")

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

        async with httpx.AsyncClient(timeout=300.0) as client:
            target_text = enriched.monster_text if is_monster else enriched.instruct_text

            if isinstance(engine, FishSpeechEngine):
                engine_name = "Fish Speech"
            elif isinstance(engine, CosyVoiceEngine):
                engine_name = "CosyVoice"

            # Opportunity 2: Split text into sentences and process concurrently, then concatenate
            gen_start = time.time()
            sentences = split_into_sentences(target_text)
            logger.info(f"[VOICE-ROUTING] Dialogue text split into {len(sentences)} sentences: {sentences}")
            
            if len(sentences) <= 1:
                res_content = await engine.generate(target_text, actor_id, client, emotion=enriched.emotion_tag)
            else:
                logger.info(f"[VOICE-ROUTING] Running concurrent synthesis for {len(sentences)} sentences...")
                tasks = [engine.generate(s, actor_id, client, emotion=enriched.emotion_tag) for s in sentences]
                results = await asyncio.gather(*tasks)
                
                # Check if all sentences failed
                if not any(results):
                    res_content = None
                else:
                    res_content = concatenate_wavs(results)
            logger.info(f"[PERF] TTS Generation took {time.time() - gen_start:.2f}s")

            if res_content is None:
                # Edge-TTS SUPPRESSED: do not fall back to cloud TTS.
                logger.error(f"🚨 [PIPELINE-CRITICAL] {engine_name} failed for {actor_id}. Edge-TTS suppressed — returning empty response.")

            if res_content:
                transcode_start = time.time()
                # Transcode WAV → Opus OGG for ~90% smaller streaming to Foundry
                # (Seed .wav files used for voice cloning are NOT affected — this is
                # only runtime TTS output returned to the Foundry game client.)
                if res_content.startswith(b"RIFF"):
                    try:
                        # Boost volume by 3.0 (approx 9.5dB) during transcoding
                        proc = subprocess.run(
                            ["ffmpeg", "-i", "pipe:0", "-filter:a", "volume=3.0", 
                             "-c:a", "libopus", "-b:a", "64k", "-f", "ogg", "pipe:1"],
                            input=res_content, capture_output=True, timeout=30,
                        )
                        if proc.returncode == 0:
                            res_content = proc.stdout
                        else:
                            logger.warning(f"Opus transcode failed (ffmpeg rc={proc.returncode}), sending raw WAV")
                    except Exception as ex:
                        logger.warning(f"Opus transcode error: {ex}, sending raw WAV")
                logger.info(f"[PERF] Transcoding took {time.time() - transcode_start:.2f}s")

                mime_type = "audio/ogg"
                if not res_content.startswith(b"OggS"):
                    # Fallback: something went wrong with transcode, detect original
                    mime_type = "audio/wav" if res_content.startswith(b"RIFF") else "audio/webm"

                audio_base64 = base64.b64encode(res_content).decode('utf-8')
                audio_data = f"data:{mime_type};base64,{audio_base64}"

        logger.info(f"[PERF] TOTAL Pipeline took {time.time() - start_time_total:.2f}s")
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

@app.get("/api/status")
async def get_system_status():
    """Returns basic system telemetry and health status."""
    vram_used = get_vram_used_gb()
    return {
        "status": "nominal",
        "vram_used_gb": vram_used,
        "vram_total_gb": 32.0,
        "vision_reader": "hot",
        "vision_gen": "hot"
    }

@app.post("/api/v1/diagnostics/logs")
async def receive_logs(log: DiagnosticLog):
    error_buffer.append(log.model_dump())
    if len(error_buffer) > 10:
        error_buffer.pop(0)
    return {"status": "cached"}

@app.post("/api/voice-changer/update")
async def update_voice_changer(request: Request):
    """Proxies settings updates to the local W-Okada server."""
    try:
        payload = await request.json()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{W_OKADA_URL}/api/voice-changer/update_settings", json=payload)
            if resp.status_code == 200:
                return resp.json()
            else:
                logger.error(f"W-Okada error: {resp.status_code} - {resp.text}")
                raise HTTPException(status_code=resp.status_code, detail="W-Okada server error")
    except Exception as e:
        logger.error(f"Voice Changer Update failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/diagnostics/latest")
async def get_latest_error():
    return error_buffer[-1] if error_buffer else {"status": "nominal"}

@app.get("/api/v1/diagnostics/history")
async def get_diagnostics_history():
    return error_buffer


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
