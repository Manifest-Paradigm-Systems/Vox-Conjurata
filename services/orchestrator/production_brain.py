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
TTS_ACTOR_URL = os.getenv("TTS_ACTOR_URL", "http://vox-actor:5020")
TTS_MONSTER_URL = os.getenv("TTS_MONSTER_URL", "http://vox-monster-fish:7860")
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
VOICE_REGISTRY_PATH = Path("./settings/voice_registry.json")
PALETTE_DIR = VOICE_SEEDS_DIR / "_palette"

# ---------------------------------------------------------------------------
# Voice Registry — persists character_id → seed mappings across restarts
# ---------------------------------------------------------------------------

def load_voice_registry() -> dict:
    if VOICE_REGISTRY_PATH.exists():
        try:
            with open(VOICE_REGISTRY_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read voice registry: {e}")
    return {}


def save_voice_registry(registry: dict) -> None:
    VOICE_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(VOICE_REGISTRY_PATH, "w") as f:
        json.dump(registry, f, indent=2)


def register_character_voice(
    actor_id: str,
    engine: str,
    seed_path: str,
    voice_prompt: str = "",
    is_archetype: bool = False,
    archetype_key: str = "",
) -> None:
    """Record a character's voice seed in the persistent registry."""
    registry = load_voice_registry()
    registry[actor_id] = {
        "engine": engine,
        "seed_path": seed_path,
        "voice_prompt": voice_prompt,
        "is_archetype": is_archetype,
        "archetype_key": archetype_key,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_voice_registry(registry)
    logger.info(f"[REGISTRY] {actor_id} → {seed_path} (engine={engine}, archetype={is_archetype})")


def resolve_seed_path(actor_id: str) -> str:
    """Return the seed WAV path for a character, checking registry first.

    Falls back to filesystem glob if the character is not in the registry.
    """
    registry = load_voice_registry()
    entry = registry.get(actor_id)
    if entry:
        seed_path = entry.get("seed_path", "")
        full = VOICE_SEEDS_DIR / seed_path
        if full.exists():
            return str(full)
        logger.warning(f"[REGISTRY] Stale entry for {actor_id}: {seed_path} not found")

    # Fallback: filesystem glob
    seeds = list(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.wav"))
    if seeds:
        return str(seeds[0])
    return ""


# ---------------------------------------------------------------------------
# Archetype Palette — generates base voice seeds via Fish Speech on first use
# ---------------------------------------------------------------------------

PALETTE_DEFINITIONS: dict[str, str] = {
    # Humanoids — accented archetypes (approx 10 seconds of speech)
    "human_male_british":     "[British accent, deep male, clear, composed, slightly raspy] In the heart of the city, where the shadows linger long after the sun has set, I wait for the signal. Every stone has a story.",
    "human_female_british":   "[British accent, bright female, warm, clear, composed, melodic] The rolling hills of the countryside are always so peaceful. I find that a brisk walk in the morning air does wonders for the spirit.",
    "elf_male_french":        "[French accent, elegant male, refined, smooth tenor, melodic, aristocratic, very clear] There is a certain grace in the way the light dances. It reminds me of the ancient melodies we once sang under the silver moon.",
    "elf_female_french":      "[French accent, graceful female, silvery soprano, refined, melodic, aristocratic, ethereal] My ancestors watched these stars long before the first towers were raised. Listen to the subtle whispers of the eternal home.",
    "dwarf_male_scottish":    "[Scottish accent, gruff male, deep, rugged, hearty, miner's voice, gravelly] There's no' much in this world as reliable as a well-forged axe. I've spent more years under the mountain than I have above it.",
    "dwarf_female_scottish":  "[Scottish accent, gruff female, warm, hearty, deep, miner's wife, raspy] Come in from the cold, then! There's a pot o' stew on the hearth and plenty o' room by the fire. You're welcome to share our table.",
    "halfling_male_irish":    "[Irish accent, cheerful male, light tenor, nimble, folk melody, bright] It's a grand day for a bit of an adventure, wouldn't you say? There's a trail just over that ridge that leads to the best tavern.",
    "halfling_female_irish":  "[Irish accent, bright female, light soprano, playful, folk melody, cheerful] If you're looking for a bit of luck, you've come to the right place! I've a pocket full of charms and a heart full of songs for you.",
    "barbarian_male_german":   "[German accent, deep male, harsh, guttural consonants, powerful, warrior, aggressive] My tribe has survived the harshest winters. We do not fear the storm, for we are the storm. Let us see who is worthy of the title warrior.",
    "barbarian_female_german": "[German accent, strong female, harsh consonants, warrior, deep, powerful, intense] The steel in my hand is an extension of my will. I have hunted the great beasts. Make sure your spirit is as sharp as your blade.",
    "elder_male_british":      "[British accent, old man, raspy, wise, low pitch, slow, deliberate, fragile] I have seen kingdoms rise and fall like the leaves in autumn. Time has a way of smoothing the sharpest edges and revealing the truth.",
    "elder_female_british":    "[British accent, old woman, raspy, warm, low pitch, slow, deliberate, gentle] The garden is looking particularly lovely this evening. Every flower is a memory of a day well spent, and though my bones ache, my heart is full.",
    # Monsters (approx 10 seconds of extreme textures)
    "monster_beast":   "[extreme guttural beastly growl, deep resonant animalistic low rumble, predatory, inhuman] The scent of fear is thick. I can hear the beating of your heart. You are far from your home, and the forest does not take kindly to intruders.",
    "monster_undead":  "[extreme hollow death rattle rasp, whispering ghostly heavy echo, ethereal, supernatural] Death is merely a change in perspective. I have walked the halls of silence for centuries, waiting for the warmth of a soul to flicker.",
    "monster_dragon":  "[extreme immense ancient deep rumble, commanding resonant heavy echo, distorted inhuman monstrous roar] You stand before a power that predates your civilization. My breath is a furnace that has consumed kings. Why should I not turn you to ash?",
    "monster_demon":   "[Arabic accent, extreme demonic multi-layered distortion, infernal guttural growl, low ancient, terrifying] The shadows of the abyss are infinite. I have tasted the sweetness of a soul consumed. Your petty desires are nothing to me, mortal.",
    "monster_goblin":  "[extreme high-pitched nasally screech, manic chittering rapid-fire, screechy, unhinged] Look what we found! A shiny toy for the pot! Don't let it run away! We take it back to the chief, he lets us keep the boots!",
}


def resolve_archetype(actor_data: "ActorMetadata", vocal_profile: dict) -> str:
    """Map character traits to the closest palette archetype key."""
    stats = actor_data.stats or {}
    race = stats.get("race", "").lower().strip()
    gender = vocal_profile.get("gender", "male").lower().strip()
    description = vocal_profile.get("description", "").lower()
    name_lore = (actor_data.name + " " + actor_data.lore).lower()
    is_elder = any(w in description or w in name_lore
                   for w in ["elder", "old ", "ancient", "aged", "venerable", "wizened"])

    if actor_data.isMonster:
        # Categorise by keywords in name/lore/race
        monster_text = name_lore + " " + race
        if any(w in monster_text for w in ["dragon", "wyrm", "drake", "wyvern"]):
            return "monster_dragon"
        if any(w in monster_text for w in
               ["skeleton", "zombie", "lich", "ghost", "spectre", "wraith", "banshee",
                "vampire", "revenant", "undead", "necromancer"]):
            return "monster_undead"
        if any(w in monster_text for w in
               ["demon", "devil", "fiend", "abyssal", "infernal", "pit lord"]):
            return "monster_demon"
        if any(w in monster_text for w in
               ["goblin", "kobold", "gremlin", "imp", "sprite"]):
            return "monster_goblin"
        return "monster_beast"

    # Humanoid — match race + gender + age
    if "elf" in race or "elven" in race or "elvish" in race:
        return f"elf_{gender}_french"
    if "dwarf" in race or "dwarven" in race:
        return f"dwarf_{gender}_scottish"
    if "halfling" in race or "hobbit" in race:
        return f"halfling_{gender}_irish"
    if "barbarian" in race or "barbarian" in name_lore:
        return f"barbarian_{gender}_german"

    if is_elder:
        return f"elder_{gender}_british"

    return f"human_{gender}_british"


# Concurrency Control
palette_locks: dict[str, asyncio.Lock] = {}
global_palette_master_lock = asyncio.Lock()
seed_forge_lock = asyncio.Lock() # Global lock for GPU-intensive forge operations

async def get_palette_lock(archetype_key: str) -> asyncio.Lock:
    async with global_palette_master_lock:
        if archetype_key not in palette_locks:
            palette_locks[archetype_key] = asyncio.Lock()
        return palette_locks[archetype_key]

async def ensure_palette_seed(archetype_key: str) -> str:
    """Ensure a palette archetype seed exists — generate via Fish Speech WITHOUT reference audio.
    This allows Fish Speech to generate a unique voice purely from the descriptive text prompt.

    Returns the absolute path to the palette seed WAV.
    """
    palette_path = PALETTE_DIR / f"{archetype_key}.wav"
    
    if palette_path.exists():
        return str(palette_path)

    lock = await get_palette_lock(archetype_key)
    async with lock:
        if palette_path.exists():
            return str(palette_path)

        prompt = PALETTE_DEFINITIONS.get(archetype_key)
        if not prompt:
            logger.error(f"[PALETTE] Unknown archetype key: {archetype_key}")
            return ""

        PALETTE_DIR.mkdir(parents=True, exist_ok=True)
        logger.info(f"[PALETTE] Generating foundation seed: {archetype_key} (via Fish Speech Text-to-Voice)")

        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                # Generate FOUNDATION purely from text description to ensure high diversity.
                # No reference audio (references: []) lets Fish Speech's LLM decide the voice.
                payload = {
                    "text": f"{prompt} Hello, I am a character in this world, and this is my unique voice.",
                    "references": [],
                    "format": "wav",
                    "normalize": True,
                    "temperature": 0.8,
                    "top_p": 0.8,
                }
                
                resp = await client.post(f"{TTS_MONSTER_URL}/v1/tts", json=payload)
                if resp.status_code == 200:
                    palette_path.write_bytes(resp.content)
                    transcript_path = palette_path.with_suffix(".txt")
                    transcript_path.write_text("Hello, I am a character in this world, and this is my unique voice.")
                    logger.info(f"[PALETTE] Created foundation {archetype_key}")
                    return str(palette_path)
                else:
                    logger.error(f"[PALETTE] Fish Speech failed foundation {archetype_key}: {resp.status_code}")
                    return ""
            except Exception as e:
                logger.error(f"[PALETTE] Foundation generation error for {archetype_key}: {e}")
                return ""


def is_named_character(actor_data: "ActorMetadata") -> bool:
    """Determine if a character is a 'named' entity deserving a unique seed.

    Returns False for generic tokens like 'Human Guard', 'Skeleton', etc.
    Returns True for proper names like 'Garrick the Rogue', 'Aldric'.
    """
    name = (actor_data.name or "").strip()
    if not name or name.lower() in ("unknown", "unnamed", "narrator", ""):
        return False
    # Generic patterns: single common noun, "Race Class" template
    generic_patterns = [
        r"^(guard|soldier|peasant|villager|merchant|thug|bandit|citizen|commoner)$",
        r"^(human|elf|dwarf|halfling|orc|goblin|kobold)\s+(guard|soldier|peasant|thug|bandit|commoner|archer|mage)$",
        r"^(skeleton|zombie|ghoul|ghost|rat|bat|spider|slime|ooze)$",
        r"^(townsfolk|townsperson|city guard|castle guard)$",
        r"^(giant spider|wolf|bear|boar|rat swarm)$",
    ]
    for pat in generic_patterns:
        if re.match(pat, name, re.IGNORECASE):
            return False
    # Has a proper name (capitalised, not purely descriptive)
    return True

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
    """Apply engine-specific formatting to dialogue text.

    CosyVoice: returns ONLY clean dialogue.
    Fish Speech: strips metadata and bracketed tags.
    """
    import re

    # Extremely aggressive stripping of all metadata prefixes and their contents
    # Matches patterns like "Instruction: ...", "Mood: ...", etc. and everything until a new sentence or line.
    patterns = [
        r'(?i)^(?:Mood|Emotion|Sentiment|Tone|Note|Instruction|Direction|Delivery|Background|Acoustics|Style|Voice|Speaker):\s*.*?(?=[A-Z][a-z]+|\n|$)',
        r'(?i)\s+(?:Mood|Emotion|Sentiment|Tone|Note|Instruction|Direction|Delivery|Background|Acoustics|Style|Voice|Speaker):\s*.*?(?=[A-Z][a-z]+|\n|$)',
        r'(?i)^(?:Mood|Emotion|Sentiment|Tone|Note|Instruction|Direction|Delivery|Background|Acoustics|Style|Voice|Speaker):\s*.*?[.!?]\s*',
        r'(?i)\s+(?:Mood|Emotion|Sentiment|Tone|Note|Instruction|Direction|Delivery|Background|Acoustics|Style|Voice|Speaker):\s*.*?[.!?]\s*'
    ]
    clean_text = text
    for pat in patterns:
        clean_text = re.sub(pat, ' ', clean_text)
    
    # Strip all bracketed/parenthetical instructions (e.g. [growl], (whispers))
    clean_text = re.sub(r'\[.*?\]', '', clean_text)
    clean_text = re.sub(r'\(.*?\)', '', clean_text)
    clean_text = re.sub(r'\*.*?\*', '', clean_text)
    
    # Final cleanup of leading/trailing non-word characters and extra whitespace
    clean_text = re.sub(r'^\W+', '', clean_text)
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()
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
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient, dsp_presets: dict = None) -> Optional[bytes]:
        raise NotImplementedError()

class VoxAudioCoreEngine(SpeechEngine):
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient, dsp_presets: dict = None) -> Optional[bytes]:
        """Calls the unified VoxCPM2 + Pedalboard service."""
        try:
            payload = {
                "npc_id": actor_id,
                "dialogue_text": text,
                "dsp_presets": dsp_presets or {}
            }
            # TTS_ACTOR_URL now points to vox-audio-core
            resp = await client.post(f"{TTS_ACTOR_URL}/generate", json=payload)
            if resp.status_code == 200:
                return resp.content
            else:
                logger.error(f"[VOICE-ROUTING] VoxAudioCore service returned error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"[VOICE-ROUTING] VoxAudioCore inference failed: {e}")
        return None

class SpeechPipelineFactory:
    def __init__(self):
        self.engine = VoxAudioCoreEngine()

    def get_engine(self, *args, **kwargs) -> SpeechEngine:
        """Always returns the unified VoxAudioCoreEngine."""
        return self.engine

pipeline_factory = SpeechPipelineFactory()

def load_routing_config() -> dict:
    """Loads routing and system configuration from local JSON."""
    if not CONFIG_PATH.exists():
        return {
            "tier_routing": {"humanoid_engine": "cosyvoice", "monster_engine": "fishspeech"},
            "system_settings": {"vram_threshold_gb": 26.0},
        }
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"tier_routing": {"humanoid_engine": "cosyvoice", "monster_engine": "fishspeech"},
                "system_settings": {"vram_threshold_gb": 26.0}}

async def generate_vocal_profile(actor_data: ActorMetadata, visual_description: str = "") -> dict:
    """Uses Qwen 2.5 via vLLM completions endpoint to generate a descriptive acoustic prompt and gender."""
    system_instruction = (
        "You are an expert cinematic casting director and master acoustic engineer for high-fantasy movies. "
        "Analyze the character name, biography, and physical appearance. "
        "Output a JSON object with 'gender' (strictly 'male' or 'female') and "
        "'description' (a VIVID, HIGHLY DRAMATIC, and COLORFUL 1-2 sentence acoustic description fit for D&D). "
        "Avoid generic terms. Focus on unique, exaggerated textures (e.g. 'vicious gravel-crushed baritone', 'silvery melodic soprano with ethereal resonance', "
        "'whiskey-soaked ancient rasp', 'shimmering supernatural resonance', 'harsh guttural chittering clicks'). "
        "Include room acoustics like 'echoing reverberant damp cave' or 'tight velvet-lined library'."
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
            if response.status_code != 200:
                logger.error(f"Profile generation: LLM returned {response.status_code}")
                raise RuntimeError(f"LLM returned {response.status_code}")
            content_str = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            try:
                res = json.loads(content_str)
            except json.JSONDecodeError as je:
                logger.error(f"Profile generation: invalid JSON from LLM — {content_str[:200]}")
                raise je
            return {
                "gender": res.get("gender", "male").lower().strip(),
                "description": res.get("description", "A clear, neutral speaking voice.")
            }
        except Exception as e:
            logger.error(f"Profile generation failed: {e}")
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

async def forge_voice_seed(
    actor_id: str,
    acoustic_description: str,
    gender: str = "male",
    is_monster: bool = False,
    is_named: bool = False,
    archetype_key: str = "",
) -> str | None:
    """Create and register a voice seed for a character.

    - Named characters: generate a unique seed cloned from the archetype palette.
    - Generic NPCs: register to use the shared archetype palette seed directly.

    Returns the seed path on success, None on failure.
    """
    seed_text = "Hello, I am a character in this world, and this is my unique voice."

    if not is_named:
        # Generic NPC — register to use the archetype palette seed
        if not archetype_key:
            logger.error(f"[VOICE-SEED] No archetype for {actor_id}")
            return None
        palette_path_str = await ensure_palette_seed(archetype_key)
        if not palette_path_str:
            return None
        
        # Trigger neural feature extraction for foundation (one-time)
        if not is_monster:
            async with httpx.AsyncClient(timeout=60.0) as client:
                try:
                    with open(palette_path_str, "rb") as f:
                        files = {"reference_audio": (Path(palette_path_str).name, f, "audio/wav")}
                        await client.post(
                            f"{TTS_ACTOR_URL}/api/extract-features",
                            data={"actorId": actor_id, "prompt_text": seed_text},
                            files=files
                        )
                except Exception as e:
                    logger.warning(f"[VOICE-SEED] Feature extraction failed for {actor_id}: {e}")

        register_character_voice(
            actor_id=actor_id,
            engine="fishspeech" if is_monster else "cosyvoice",
            seed_path=str(Path(palette_path_str).relative_to(VOICE_SEEDS_DIR)),
            voice_prompt=acoustic_description,
            is_archetype=True,
            archetype_key=archetype_key,
        )
        logger.info(f"[VOICE-SEED] {actor_id} registered as archetype {archetype_key}")
        return palette_path_str

    # Named character — generate a unique seed cloned from its archetype
    if not archetype_key:
        logger.error(f"[VOICE-SEED] No archetype for named character {actor_id}")
        return None
    archetype_path_str = await ensure_palette_seed(archetype_key)
    if not archetype_path_str:
        return None
    archetype_path = Path(archetype_path_str)

    seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_{gender}.wav"
    text_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_{gender}.txt"

    async with seed_forge_lock: # Serialize GPU intensive operations
        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                # We now use Fish Speech as the primary sound designer for ALL unique seeds.
                # This ensures character voices are diverse and not just narrator clones.
                logger.info(f"[VOICE-SEED] Using Fish Speech to forge unique seed for: {actor_id}")
                archetype_b64 = base64.b64encode(archetype_path.read_bytes()).decode("utf-8")
                
                # Use the acoustic_description as the 'prompt' within the text for Fish Speech
                # and the archetype audio as the reference speaker identity.
                import random
                payload = {
                    "text": f"[{acoustic_description}] {seed_text}",
                    "references": [{"audio": archetype_b64, "text": seed_text}],
                    "format": "wav",
                    "normalize": True,
                    "temperature": 0.8,
                    "top_p": 0.8,
                    "seed": random.randint(0, 1000000)
                }
                response = await client.post(f"{TTS_MONSTER_URL}/v1/tts", json=payload)

                if response.status_code == 200:
                    seed_path.write_bytes(response.content)
                    text_path.write_text(seed_text)
                    
                    # Trigger neural feature extraction for unique seed
                    if not is_monster:
                        try:
                            with open(seed_path, "rb") as f:
                                files = {"reference_audio": (seed_path.name, f, "audio/wav")}
                                await client.post(
                                    f"{TTS_ACTOR_URL}/api/extract-features",
                                    data={"actorId": actor_id, "prompt_text": seed_text},
                                    files=files
                                )
                        except Exception as fe:
                            logger.warning(f"[VOICE-SEED] Feature extraction failed for {actor_id}: {fe}")

                    register_character_voice(
                        actor_id=actor_id,
                        engine="fishspeech",
                        seed_path=str(seed_path.relative_to(VOICE_SEEDS_DIR)),
                        voice_prompt=acoustic_description,
                        is_archetype=False,
                    )
                    logger.info(f"[VOICE-SEED] Forged unique seed for {actor_id} → {seed_path.name}")
                    return str(seed_path)
                else:
                    logger.error(f"[VOICE-SEED] Seed generation failed for {actor_id}: HTTP {response.status_code}")
                    return None
            except Exception as e:
                logger.error(f"[VOICE-SEED] Seed forge error for {actor_id}: {e}")
                return None


async def enrich_and_instruct(speaker: str, role: str, text: str, is_monster: bool = False) -> DialogueEnrichment:
    """Enrich dialogue with Qwen via LLM and format tags for specific TTS engines.

    For monster text, the LLM is instructed to insert inline [emotion] delivery
    tags at natural breakpoints within the dialogue for Fish Speech modulation.
    """
    if is_monster:
        system_instruction = (
            "You are a cinematic monster voice director. Analyze the creature's dialogue "
            "and rewrite it with EXTREME, VIVID inline delivery tags in [square brackets] "
            "at every natural breakpoint to force a highly textured performance.\n\n"
            "Available tags include: [growl], [snarl], [roar], [whisper], [low growl], "
            "[hiss], [guttural], [raspy], [deep], [shriek], [echo], [pause], [slow], "
            "[rising pitch], [falling pitch], [vicious], [animalistic].\n\n"
            "Output JSON with:\n"
            "- 'emotional_resonance': overall mood description\n"
            "- 'vocal_delivery_prompt': how the creature should sound overall (BE VIVID)\n"
            "- 'emotion_tag': a single descriptive tag like 'Enraged Growl' or 'Terrified Hiss'\n"
            "- 'tagged_text': the dialogue WITH inline tags inserted at natural breakpoints\n\n"
            "Example: for \"You dare enter my lair?\" output could be:\n"
            "'[low growl, viscous] You dare enter my lair? [shriek, rising pitch] I will crush you!'"
        )
    else:
        system_instruction = (
            "You are a cinematic dialogue director. Analyze the text for emotional subtext. "
            "Output JSON with 'emotional_resonance', 'vocal_delivery_prompt', "
            "and 'emotion_tag' (a single descriptive tag like 'Enraged Growl', 'Terrified Whisper', or 'Neutral').\n\n"
            "CRITICAL: DO NOT REWRITE OR MODIFY THE DIALOGUE TEXT. RETURN IT EXACTLY AS PROVIDED."
        )
    payload = {
        "model": "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Speaker: {speaker}, Text: {text}"},
        ],
        "temperature": 0.3,
        "max_tokens": 256,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            if response.status_code != 200:
                raise RuntimeError(f"LLM returned {response.status_code}")
            content_str = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            res = json.loads(content_str)

            emotion = res.get("emotion_tag", "Neutral").strip()

            # For monsters: use the tagged_text if the LLM provided it
            if is_monster and res.get("tagged_text", "").strip():
                raw_tagged = res["tagged_text"].strip()
                monster_text = standardize_speech_text(raw_tagged, "fish-speech", emotion)
            else:
                monster_text = standardize_speech_text(text, "fish-speech", emotion)

            instruct_text = standardize_speech_text(text, "cosyvoice", emotion)

            return DialogueEnrichment(
                speaker=speaker,
                role=role,
                raw_text=text,
                emotional_resonance=str(res.get("emotional_resonance", emotion)),
                vocal_delivery_prompt=res.get("vocal_delivery_prompt", f"Deliver as {emotion}."),
                instruct_text=instruct_text,
                monster_text=monster_text,
                emotion_tag=emotion,
            )

        except Exception as e:
            logger.error(f"Instruction error: {e}")
            return DialogueEnrichment(
                speaker=speaker,
                role=role,
                raw_text=text,
                emotional_resonance="Neutral",
                vocal_delivery_prompt="Standard.",
                instruct_text=standardize_speech_text(text, "cosyvoice", "Neutral"),
                monster_text=standardize_speech_text(text, "fish-speech", "neutral"),
                emotion_tag="neutral",
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

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Vox Conjurata Orchestrator starting up...")
    # Pre-warm palette foundation seeds in the background
    asyncio.create_task(prewarm_palette_foundations())

async def prewarm_palette_foundations():
    """Ensure all archetype palette foundations exist on disk."""
    logger.info(f"🎨 Pre-warming {len(PALETTE_DEFINITIONS)} palette foundations...")
    for key in PALETTE_DEFINITIONS.keys():
        try:
            await ensure_palette_seed(key)
        except Exception as e:
            logger.error(f"Failed to pre-warm palette {key}: {e}")
    logger.info("✅ All palette foundations pre-warmed.")


@app.get("/api/v1/registry")
async def get_voice_registry():
    """Returns the entire voice registry for management."""
    return load_voice_registry()

@app.get("/api/v1/registry/audio/{actor_id}")
async def get_seed_audio(actor_id: str):
    """Returns the seed audio for a specific character."""
    path = resolve_seed_path(actor_id)
    if not path:
        raise HTTPException(status_code=404, detail="Seed not found")
    return FileResponse(path, media_type="audio/wav")

@app.post("/api/v1/registry/regenerate/{actor_id}")
async def regenerate_character_voice(actor_id: str):
    """Triggers individual regeneration for a character."""
    # We need the metadata to regenerate. If it's not in the registry, we can't.
    registry = load_voice_registry()
    entry = registry.get(actor_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Character not in registry")
    
    # We don't have the full ActorMetadata here, but we can try to re-forge 
    # based on the stored voice_prompt and archetype_key.
    # This is a bit tricky without the full original stats.
    # For now, let's just return a hint that they should use the /vox forge command.
    return {"status": "error", "message": "Please use the '/vox forge' command on the token in Foundry to regenerate."}

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
    """Narrator voice is driven by CosyVoice seed. See /api/ingest-actor to register voices."""
    return []

async def get_visual_description(image_path_relative: str) -> str:
    """Uses MiniCPM-V-2.6 (vox-vision-reader) to describe the character image."""
    if not image_path_relative or image_path_relative.strip() == "":
        return ""

    full_path = FOUNDRY_DATA_DIR / image_path_relative
    if not full_path.exists() or full_path.is_dir():
        logger.warning(f"🖼️ Visual Scan: Image not found or invalid at {full_path}")
        return ""

    try:
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

@app.post("/api/ingest-actor")
async def ingest_actor(data: ActorMetadata, force_refresh: bool = False):
    """Ingest an actor and forge or register their voice seed.

    - Named characters get a unique seed cloned from their archetype palette.
    - Generic NPCs get registered to the shared archetype seed.
    - If no seed exists, triggers a visual scan to inform vocal profiling.
    """
    # Check existing registration
    registry = load_voice_registry()
    existing_entry = registry.get(data.actorId)
    existing_seeds = list(VOICE_SEEDS_DIR.glob(f"{data.actorId}_seed_*.wav"))

    if existing_entry and not force_refresh:
        logger.info(f"[INGEST] Registry cache hit for {data.actorId} ({data.name}), returning cached seed.")
        return {"status": "cached", "seed_path": existing_entry.get("seed_path"),
                "engine": existing_entry.get("engine"), "is_archetype": existing_entry.get("is_archetype")}

    if existing_seeds and not force_refresh:
        logger.info(f"[INGEST] Filesystem cache hit for {data.actorId} ({data.name}), returning cached seed.")
        return {"status": "cached", "seeds": [s.name for s in existing_seeds]}

    if existing_seeds and force_refresh:
        logger.info(f"[INGEST] force_refresh=True — purging {len(existing_seeds)} stale seed(s) for {data.actorId}")
        for stale in existing_seeds:
            stale.unlink(missing_ok=True)
            stale.with_suffix(".txt").unlink(missing_ok=True)
        # Also remove from registry
        registry.pop(data.actorId, None)
        save_voice_registry(registry)

    # Generate vocal profile
    visual_desc = ""
    if force_refresh or (not existing_seeds and not existing_entry):
        logger.info(f"[INGEST] Triggering visual analysis for {data.name}...")
        visual_desc = await get_visual_description(data.artPath)

    is_named = is_named_character(data)

    # Resolve Gender — Prioritize stats if provided and valid
    gender = str(data.stats.get("gender", "")).lower().strip()
    if gender not in ["male", "female"]:
        gender = ""

    if data.customDescription:
        logger.info(f"[INGEST] GM Override provided for {data.name}: {data.customDescription}")
        profile_desc = data.customDescription
        if not gender:
            is_female = any(w in profile_desc.lower() for w in ["female", "woman", "lady", "she", "her"])
            gender = "female" if is_female else "male"
    else:
        profile_data = await generate_vocal_profile(data, visual_desc)
        profile_desc = profile_data.get("description", "A clear, neutral speaking voice.")
        if not gender:
            gender = profile_data.get("gender", "male").lower().strip()

    if gender not in ["male", "female"]:
        gender = "male"

    archetype_key = resolve_archetype(data, {"gender": gender, "description": profile_desc})

    path = await forge_voice_seed(
        actor_id=data.actorId,
        acoustic_description=profile_desc,
        gender=gender,
        is_monster=data.isMonster,
        is_named=is_named,
        archetype_key=archetype_key,
    )
    return {
        "status": "created" if path else "error",
        "path": path,
        "is_named": is_named,
        "is_archetype": not is_named,
        "archetype_key": archetype_key,
        "visual_description": visual_desc,
    }

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
        enriched = await enrich_and_instruct(speaker_name, role, transcription, is_monster=is_monster)
        logger.info(f"[PERF] Enrichment took {time.time() - enrich_start:.2f}s")

        # 3. Determine VRAM Status, Engine, and Voice Seed
        config = load_routing_config()
        vram_threshold = config.get("system_settings", {}).get("vram_threshold_gb", 18.0)
        vram_used = get_vram_used_gb()
        vram_triggered = vram_used > vram_threshold

        engine = pipeline_factory.get_engine(
            is_monster=is_monster,
            stats=meta.get("stats", {}),
            config=config,
            vram_triggered=vram_triggered,
        )

        # Check if this actor is using an archetype seed
        registry = load_voice_registry()
        registry_entry = registry.get(actor_id, {})
        is_archetype = registry_entry.get("is_archetype", False)

        audio_data = None
        engine_name = "Unknown"

        async with httpx.AsyncClient(timeout=300.0) as client:
            target_text = enriched.monster_text if is_monster else enriched.instruct_text

            if isinstance(engine, FishSpeechEngine):
                engine_name = "Fish Speech"
            elif isinstance(engine, CosyVoiceEngine):
                engine_name = "CosyVoice"

            # Split text into sentences and process concurrently, then concatenate
            gen_start = time.time()
            sentences = split_into_sentences(target_text)
            logger.info(f"[VOICE-ROUTING] Dialogue text split into {len(sentences)} sentences")

            kwargs = {"emotion": enriched.emotion_tag}
            if isinstance(engine, CosyVoiceEngine):
                kwargs["is_archetype"] = is_archetype
                kwargs["delivery_prompt"] = enriched.vocal_delivery_prompt

            if len(sentences) <= 1:
                res_content = await engine.generate(target_text, actor_id, client, **kwargs)
            else:
                logger.info(f"[VOICE-ROUTING] Running concurrent synthesis for {len(sentences)} sentences...")
                tasks = [engine.generate(s, actor_id, client, **kwargs) for s in sentences]
                results = await asyncio.gather(*tasks)
                
                # Check if all sentences failed
                if not any(results):
                    res_content = None
                else:
                    res_content = concatenate_wavs(results)
            logger.info(f"[PERF] TTS Generation took {time.time() - gen_start:.2f}s")

            if res_content is None:
                logger.error(f"🚨 [PIPELINE-CRITICAL] {engine_name} failed for {actor_id} — returning empty response.")

            if res_content:
                transcode_start = time.time()
                # Transcode WAV → Opus OGG for ~90% smaller streaming to Foundry
                if res_content.startswith(b"RIFF"):
                    try:
                        # Boost volume by 3.0 (approx 9.5dB) during transcoding
                        # Use async subprocess to avoid blocking the event loop
                        process = await asyncio.create_subprocess_exec(
                            "ffmpeg", "-i", "pipe:0", "-filter:a", "volume=3.0", 
                            "-c:a", "libopus", "-b:a", "64k", "-f", "ogg", "pipe:1",
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, stderr = await process.communicate(input=res_content)
                        
                        if process.returncode == 0:
                            res_content = stdout
                        else:
                            logger.warning(f"Opus transcode failed (ffmpeg rc={process.returncode}), stderr={stderr.decode()[:200]}")
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
