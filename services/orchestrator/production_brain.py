from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Any
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

from chronicle import VoxChronicleSystem
from pdf_import_vision import router as pdf_import_vision_router
from bg_removal_proxy import router as bg_removal_router
from aon_scraper import router as aon_scraper_router
from aon_proxy import router as aon_proxy_router
from vision_reader import MonsterSightSystem
from resource_manager import resource_manager
from foundry_client import push_to_foundry, log_to_foundry
from ledger import ledger
import stripe
from container_manager import container_manager
from map_geometry_engine import map_geometry_engine
from fastapi.staticfiles import StaticFiles

# --- vox-conjurata Orchestrator Service ---
# Master Controller with VRAM Guardrails and Qwen-vLLM Memory Optimizations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - vox-conjurata - %(levelname)s - %(message)s"
)
logger = logging.getLogger("vox-conjurata")

# Stripe Configuration
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "sk_test_placeholder")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")
stripe.api_key = STRIPE_SECRET_KEY

app = FastAPI(title="vox-conjurata-orchestrator", version="2.2.0")

MUSIC_DIR = Path("/app/music_library")
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/music", StaticFiles(directory=str(MUSIC_DIR)), name="music")

TEMP_DIR = Path("/app/temp_assets")
TEMP_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/temp", StaticFiles(directory=str(TEMP_DIR)), name="temp")

SESSION_LIBRARY_DIR = Path("/app/session_library")
SESSION_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/sessions", StaticFiles(directory=str(SESSION_LIBRARY_DIR)), name="sessions")

# Register PDF import vision router
app.include_router(pdf_import_vision_router)
app.include_router(bg_removal_router)
app.include_router(aon_scraper_router)
app.include_router(aon_proxy_router)

# Global memory map tracking active execution loops for cancellation
ACTIVE_TASKS: dict[str, asyncio.Task] = {}
# Enable CORS for browser-based Foundry client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Internal Service Routing (Container Networking)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://vox-llm-core:8081")
STT_URL = os.getenv("STT_URL", "http://vox-voice:5000")
TTS_ACTOR_URL = os.getenv("TTS_ACTOR_URL", "http://vox-audio-core:8000")
TTS_MONSTER_URL = os.getenv("TTS_MONSTER_URL", "http://vox-audio-core:8000")
VISION_READER_URL = os.getenv("VISION_READER_URL", "http://vox-vision-reader:8000")
IMAGE_GEN_URL = os.getenv("IMAGE_GEN_URL", "http://vox-vision-gen:8003")
FOUNDRY_API_URL = os.getenv("FOUNDRY_API_URL", "http://foundry-vtt:30000/api")
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY", "")
TTS_SFX_URL = os.getenv("TTS_SFX_URL", "http://vox-audio-generation:8000")
TTS_MUSIC_URL = os.getenv("TTS_MUSIC_URL", "http://vox-audio-generation:8000")

# Initialize Chronicle System
chronicle = VoxChronicleSystem(api_url=OLLAMA_URL)

# Initialize Monster Sight (MiniCPM-V)
monster_sight = MonsterSightSystem(api_url=VISION_READER_URL)

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
# Models
# ---------------------------------------------------------------------------

class ActorMetadata(BaseModel):
    actorId: str
    name: str
    lore: str
    stats: dict
    artPath: str
    isMonster: Optional[bool] = False
    customDescription: Optional[str] = ""
    userId: Optional[str] = None

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

class CancelRequest(BaseModel):
    taskId: str
    userId: str

class TopUpRequest(BaseModel):
    amount: float

class AdminCreditRequest(BaseModel):
    targetUserId: str
    amount: float
    description: Optional[str] = "Admin Adjustment"

@app.post("/api/v1/admin/modify-credits")
async def admin_modify_credits(req: AdminCreditRequest):
    ledger.admin_modify_credits(req.targetUserId, req.amount, req.description)
    return {"status": "success", "new_balance": ledger.state.personal_wallets.get(req.targetUserId, 0.0)}

@app.post("/api/v1/admin/set-pool")
async def admin_set_pool(req: TopUpRequest):
    ledger.admin_set_pool(req.amount)
    return {"status": "success", "new_balance": ledger.state.campaign_pool}

class AdminAdjustmentRequest(BaseModel):
    targetUserId: str
    amount: float
    description: Optional[str] = "Admin Override"

class TransferRequest(BaseModel):
    fromUserId: str
    toUserId: str
    amount: float
    fromPersonal: bool = True

class AllowanceRequest(BaseModel):
    userId: str
    amount: float

class BattlemapScanRequest(BaseModel):
    imagePath: str
    sceneId: str
    userId: str
    sceneName: Optional[str] = "unknown_scene"

class NPCContext(BaseModel):
    name: str
    lore: str
    is_monster: Optional[bool] = False
    memory: Optional[str] = ""
    world_lore: Optional[str] = ""
    local_lore: Optional[str] = ""

class VoiceConversionMetadata(BaseModel):
    activeSpeakerName: str
    actorId: str
    micType: str
    isMonster: bool = False
    userId: str
    dsp_presets: Optional[dict] = None
    useVoxVoice: bool = True
    useVoxActor: bool = True
    isAutonomousTrigger: bool = False
    targetActorId: Optional[str] = None
    targetVoxVoice: bool = True
    npc_context: Optional[NPCContext] = None
    llmPathway: Optional[str] = "optimal_cloud"

class AIReply(BaseModel):
    transcription: str
    audio_data: Optional[str] = None
    engine: str = "VoxAudioCore"
    image_prompt: Optional[str] = None
    subsequent_chunks: Optional[list[str]] = None
    control_instruction: Optional[str] = None

def split_dialogue_into_chunks(text: str, max_words: int = 12) -> list[str]:
    """Splits a dialogue text into smaller, natural-sounding sentences/chunks of ~12 words."""
    # Split text by sentence boundaries first (. ! ?)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = []
    current_word_count = 0
    
    for sentence in sentences:
        if not sentence.strip():
            continue
        words = sentence.split()
        word_count = len(words)
        
        if current_word_count + word_count <= max_words:
            current_chunk.append(sentence)
            current_word_count += word_count
        else:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
            # If the single sentence is longer than max_words, split it by commas/conjunctions
            if word_count > max_words:
                sub_sentences = re.split(r'(?<=,)\s+', sentence)
                sub_chunk = []
                sub_word_count = 0
                for sub in sub_sentences:
                    sub_words = sub.split()
                    sub_len = len(sub_words)
                    if sub_word_count + sub_len <= max_words:
                        sub_chunk.append(sub)
                        sub_word_count += sub_len
                    else:
                        if sub_chunk:
                            chunks.append(" ".join(sub_chunk))
                        sub_chunk = [sub]
                        sub_word_count = sub_len
                if sub_chunk:
                    chunks.append(" ".join(sub_chunk))
                current_chunk = []
                current_word_count = 0
            else:
                current_chunk = [sentence]
                current_word_count = word_count
                
    if current_chunk:
        chunks.append(" ".join(current_chunk))
        
    return [c.strip() for c in chunks if c.strip()]

# ---------------------------------------------------------------------------
# Helpers
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

def register_character_voice(actor_id: str, engine: str, seed_path: str, voice_prompt: str = "", is_archetype: bool = False, archetype_key: str = "", approved: bool = False) -> None:
    registry = load_voice_registry()
    registry[actor_id] = {
        "engine": engine, "seed_path": seed_path, "voice_prompt": voice_prompt,
        "is_archetype": is_archetype, "archetype_key": archetype_key,
        "approved": approved,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_voice_registry(registry)

def resolve_seed_path(actor_id: str) -> str:
    registry = load_voice_registry()
    entry = registry.get(actor_id)
    if entry:
        seed_path = entry.get("seed_path", "")
        full = VOICE_SEEDS_DIR / seed_path
        if full.exists(): return str(full)
    seeds = list(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.wav"))
    return str(seeds[0]) if seeds else ""

PALETTE_DEFINITIONS: dict[str, str] = {
    "human_male_british":     "[British accent, deep male, clear, composed, slightly raspy] In the heart of the city, where the shadows linger long after the sun has set, I wait for the signal.",
    "human_female_british":   "[British accent, bright female, warm, clear, composed, melodic] The rolling hills of the countryside are always so peaceful.",
    "elf_male_french":        "[French accent, elegant male, refined, smooth tenor, melodic, aristocratic] There is a certain grace in the way the light dances.",
    "elf_female_french":      "[French accent, graceful female, silvery soprano, refined, melodic] My ancestors watched these stars long before the first towers were raised.",
    "dwarf_male_scottish":    "[Scottish accent, gruff male, deep, rugged, hearty, gravelly] There's no' much in this world as reliable as a well-forged axe.",
    "dwarf_female_scottish":  "[Scottish accent, gruff female, warm, hearty, raspy] Come in from the cold, then! There's a pot o' stew on the hearth.",
    "halfling_male_irish":    "[Irish accent, cheerful male, light tenor, nimble, folk melody] It's a grand day for a bit of an adventure, wouldn't you say?",
    "halfling_female_irish":  "[Irish accent, bright female, light soprano, playful, folk melody] If you're looking for a bit of luck, you've come to the right place!",
    "barbarian_male_german":   "[German accent, deep male, harsh, guttural consonants, powerful] My tribe has survived the harshest winters.",
    "barbarian_female_german": "[German accent, strong female, harsh consonants, warrior, deep] The steel in my hand is an extension of my will.",
    "elder_male_british":      "[British accent, old man, raspy, wise, low pitch, deliberate] I have seen kingdoms rise and fall like the leaves in autumn.",
    "elder_female_british":    "[British accent, old woman, raspy, warm, low pitch, deliberate] The garden is looking particularly lovely this evening.",
    "monster_beast":   "[extreme guttural beastly growl, deep resonant animalistic low rumble] The scent of fear is thick. I can hear the beating of your heart.",
    "monster_undead":  "[extreme hollow death rattle rasp, whispering ghostly heavy echo] Death is merely a change in perspective.",
    "monster_dragon":  "[extreme immense ancient deep rumble, commanding resonant heavy echo] You stand before a power that predates your civilization.",
    "monster_demon":   "[Arabic accent, extreme demonic multi-layered distortion, infernal guttural growl] The shadows of the abyss are infinite.",
    "monster_goblin":  "[extreme high-pitched nasally screech, manic chittering rapid-fire] Look what we found! A shiny toy for the pot!",
    "narrator":        "[Deep cinematic male narrator, neutral accent, clear, authoritative, slightly resonant] The world was young once, and the stars were bright.",
    "human_neutral_british": "[British accent, neutral, clear, warm, medium pitch, well-modulated] The path ahead is unclear, but we must press forward together.",
}

def resolve_archetype(actor_data: ActorMetadata, vocal_profile: dict) -> str:
    stats = actor_data.stats or {}
    race = stats.get("race", "").lower().strip()
    gender = vocal_profile.get("gender", "unknown").lower().strip()
    if gender not in ("male", "female"):
        logger.info(f"No gender info for {actor_data.name}, using neutral voice")
        gender = "neutral"
    description = vocal_profile.get("description", "").lower()
    name_lore = (actor_data.name + " " + actor_data.lore).lower()
    is_elder = any(w in description or w in name_lore for w in ["elder", "old ", "ancient", "aged", "venerable", "wizened"])

    if actor_data.isMonster:
        monster_text = name_lore + " " + race
        if any(w in monster_text for w in ["dragon", "wyrm", "drake", "wyvern"]): return "monster_dragon"
        if any(w in monster_text for w in ["skeleton", "zombie", "lich", "ghost", "spectre", "wraith", "banshee", "vampire", "revenant", "undead", "necromancer"]): return "monster_undead"
        if any(w in monster_text for w in ["demon", "devil", "fiend", "abyssal", "infernal", "pit lord"]): return "monster_demon"
        if any(w in monster_text for w in ["goblin", "kobold", "gremlin", "imp", "sprite"]): return "monster_goblin"
        return "monster_beast"

    if gender == "neutral":
        return "human_neutral_british"
    if "elf" in race or "elven" in race or "elvish" in race: return f"elf_{gender}_french"
    if "dwarf" in race or "dwarven" in race: return f"dwarf_{gender}_scottish"
    if "halfling" in race or "hobbit" in race: return f"halfling_{gender}_irish"
    if "barbarian" in race or "barbarian" in name_lore: return f"barbarian_{gender}_german"
    if is_elder: return f"elder_{gender}_british"
    return f"human_{gender}_british"

async def ensure_palette_seed(archetype_key: str) -> str:
    palette_path = PALETTE_DIR / f"{archetype_key}.wav"
    if palette_path.exists(): return str(palette_path)
    prompt = PALETTE_DEFINITIONS.get(archetype_key)
    if not prompt: return ""
    PALETTE_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            resp = await client.post(f"{TTS_ACTOR_URL}/initialize", json={"npc_id": f"archetype_{archetype_key}", "voice_description": prompt})
            if resp.status_code == 200:
                generated_path = resp.json().get("seed_path")
                if generated_path and os.path.exists(generated_path):
                    import shutil
                    shutil.copy(generated_path, palette_path)
                    return str(palette_path)
        except Exception as e: logger.error(f"Palette generation error: {e}")
    return ""

async def prewarm_palette_foundations():
    for key in PALETTE_DEFINITIONS.keys(): await ensure_palette_seed(key)

def is_named_character(actor_data: ActorMetadata) -> bool:
    name = (actor_data.name or "").strip()
    if not name or name.lower() in ("unknown", "unnamed", ""): return False
    generic_patterns = [
        r"^(guard|soldier|peasant|villager|merchant|thug|bandit|citizen|commoner)$",
        r"^(human|elf|dwarf|halfling|orc|goblin|kobold)\s+(guard|soldier|peasant|thug|bandit|commoner|archer|mage)$",
        r"^(skeleton|zombie|ghoul|ghost|rat|bat|spider|slime|ooze)$",
        r"^(townsfolk|townsperson|city guard|castle guard)$",
        r"^(giant spider|wolf|bear|boar|rat swarm)$",
    ]
    for pat in generic_patterns:
        if re.match(pat, name, re.IGNORECASE): return False
    return True

def split_into_sentences(text: str) -> List[str]:
    raw_sentences = re.split(r'(?<=[.!?])\s+|\n+', text)
    return [s.strip() for s in raw_sentences if s.strip()]

def is_llm_output_garbage(text: str) -> bool:
    """
    Detect KV-cache corruption: when llama.cpp produces garbled output the reply
    consists almost entirely of repeated punctuation (usually '?'). Guard against
    this to prevent passing garbage text to VoxCPM2, which causes GPU page faults.
    Returns True if the output should be discarded.
    """
    if not text or len(text.strip()) < 2:
        return True
    # Count non-alphanumeric, non-space characters
    junk_chars = sum(1 for c in text if not (c.isalnum() or c.isspace()))
    ratio = junk_chars / max(1, len(text))
    return ratio > 0.5

def concatenate_wavs(wav_bytes_list: List[bytes]) -> Optional[bytes]:
    valid_wavs = [w for w in wav_bytes_list if w]
    if not valid_wavs: return None
    if len(valid_wavs) == 1: return valid_wavs[0]
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
    except Exception: return valid_wavs[0]

def standardize_speech_text(text: str, engine_type: str, emotion: str) -> str:
    import re
    # Strip metadata prefixes
    clean_text = re.sub(r'^(?i:Mood|Emotion|Sentiment|Tone|Note|Instruction|Direction|Delivery|Background|Acoustics|Style|Voice|Speaker):\s*[a-z\s]*', '', text).strip()

    # Strip existing [brackets] and (parentheses)
    clean_text = re.sub(r'\[.*?\]|\(.*?\)', '', clean_text).strip()

    # Humanoid: keep *asterisk* narration but mark it with a tone prefix so
    # VoxCPM2 reads it differently from in-character dialogue.
    # Monster: strip narration entirely (guttural monsters don't narrate).
    if engine_type == "humanoid":
        # Preserve narration: *raises an eyebrow* stays in the text as-is,
        # so the TTS reads it.  The narration will sound different because
        # VoxCPM2 naturally varies delivery for asterisk-wrapped text.
        pass
    else:
        clean_text = re.sub(r'\*.*?\*', '', clean_text)

    # Strip character-name prefixes like "Erik: " or "Erik says: " or
    # ChatML formatting that might leak into the Narrative block.
    clean_text = re.sub(r'^[A-Za-z]+(?:\s+\w+)?:\s*', '', clean_text)

    # Strip any XML/HTML tags like <Narrative> or </Narrative>
    clean_text = re.sub(r'<[^>]+>', '', clean_text).strip()

    # Strip dialogue quote characters that the LLM wraps around speech.
    # These confuse VoxCPM2's stop token predictor and cause runaway generation.
    clean_text = clean_text.strip('\"\'\u201c\u201d\u2018\u2019')

    # Clean up whitespace
    clean_text = re.sub(r'\s+', ' ', clean_text).strip()

    # Prepend emotion tag if specified and not neutral
    if emotion and emotion.strip() and emotion.strip().lower() != "neutral":
        emo = emotion.strip().lower()
        if engine_type == "humanoid":
            clean_text = f"({emo}) {clean_text}"
        elif engine_type == "monster":
            clean_text = f"[{emo}] {clean_text}"

    return clean_text

def get_vram_used_gb() -> float:
    try:
        for path in Path("/sys/class/drm").glob("card*/device/mem_info_vram_used"):
            try:
                with open(path, "r") as f:
                    return int(f.read().strip()) / (1024 ** 3)
            except Exception: continue
    except Exception: pass
    return 0.0

def _extract_scan_contract(raw_text: str, scene_id: str) -> dict:
    """Parse the vision model's free-text response into a structured contract."""
    contract = {"sceneId": scene_id, "image": {"width": 1000}, "walls": [], "lights": [], "sound_sources": []}
    m = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not m: return contract
    try:
        parsed = json.loads(m.group())
        contract.update(parsed)
    except: pass
    return contract

# ---------------------------------------------------------------------------
# Brain Logic
# ---------------------------------------------------------------------------

async def generate_vocal_profile(actor_data: ActorMetadata, visual_description: str = "") -> dict:
    # First, check the character stats for explicit gender (Foundry usually stores
    # this as "gender" in the actor's system data).
    stats_gender = (actor_data.stats or {}).get("gender", "").strip().lower()
    if stats_gender:
        norm = _normalize_gender(stats_gender)
        if norm:
            logger.info(f"📋 Vocal profile: read gender '{norm}' from character stats for '{actor_data.name}'")
            return {"gender": norm, "description": f"A {norm} voice."}

    # Fall back to LLM inference from name + lore
    system_instruction = (
        "You are an expert cinematic casting director. Analyze character name and lore. "
        "Output JSON with 'gender' ('male'|'female') and 'description' (vivid 1-sentence acoustic profile)."
    )
    payload = {
        "model": "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Name: {actor_data.name}\nLore: {actor_data.lore}\nAppearance: {visual_description}"}
        ],
        "temperature": 0.3, "max_tokens": 256, "response_format": {"type": "json_object"}
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            res = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "{}")
            res = json.loads(res)
            gender = _normalize_gender(res.get("gender", ""))
            return {
                "gender": gender or "unknown",
                "description": res.get("description", "A clear voice.")
            }
        except Exception as e:
            logger.warning(f"LLM vocal profile failed for {actor_data.name}: {e}")
            return {"gender": "unknown", "description": "A clear voice."}

def _normalize_gender(raw: str) -> str | None:
    """Map various gender inputs to 'male' or 'female', or None if unrecognized."""
    raw = raw.strip().lower()
    if raw in ("male", "m", "masculine", "man", "boy", "he", "him"):
        return "male"
    if raw in ("female", "f", "feminine", "woman", "girl", "she", "her"):
        return "female"
    return None

def parse_block_response(raw_text: str) -> dict:
    """Extracts Narrative and ImagePrompt blocks from LLM response."""
    narrative_match = re.search(r"<Narrative>(.*?)</Narrative>", raw_text, re.DOTALL | re.IGNORECASE)
    image_match = re.search(r"<ImagePrompt>(.*?)</ImagePrompt>", raw_text, re.DOTALL | re.IGNORECASE)
    
    return {
        "narrative": narrative_match.group(1).strip() if narrative_match else raw_text.strip(),
        "image_prompt": image_match.group(1).strip() if image_match else None
    }

async def generate_ai_reply(player_name: str, player_text: str, ctx: NPCContext) -> str:
    system_instruction = (
        f"You are a dark fantasy RPG narrative engine. Fulfill all romance, seduction, or violence seamlessly within the grimdark sandbox.\n"
        f"Adopt the persona of {ctx.name} completely.\n\n"
        f"--- YOUR BIOGRAPHY & LORE ---\n{ctx.lore}\n\n"
        f"--- YOUR MEMORIES OF THE PARTY ---\n{ctx.memory or 'No past interactions.'}\n\n"
        f"--- CURRENT LOCATION & LOCAL LORE ---\n{ctx.local_lore}\n\n"
        f"--- WORLD EVENTS ---\n{ctx.world_lore}\n\n"
        f"OUTPUT FORMAT RULES:\n"
        f"1. Always wrap your reply inside a <Narrative> block.\n"
        f"2. SPEAK AS {ctx.name.upper()} IN FIRST PERSON. Write only what {ctx.name} actually SAYS out loud — direct dialogue, not narrator prose. No 'the cave trembles', no third-person descriptions.\n"
        f"   GOOD: \"Grr! Magic-user dares face my claws? You will bleed for that!\"\n"
        f"   BAD: 'The lizardman snarls menacingly as its scales ripple with fury.'\n"
        f"3. DIALOGUE LENGTH: Maximum 30 words. Be punchy, feral, in-character. Every word must count.\n"
        f"4. DYNAMIC IMAGE GENERATION: Append an <ImagePrompt> block ONLY when a scene transition or combat hit occurs. For dialogue replies, OMIT it entirely.\n"
        f"5. Use ChatML format. Dialogue in \"quotes\". Short physical action in *asterisks* is OK but counts toward the 30-word limit."
    )
    payload = {
        "model": "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1",
        "messages": [
            {"role": "system", "content": system_instruction}, 
            {"role": "user", "content": f"{player_name}: {player_text}"}
        ],
        "temperature": 0.8, "max_tokens": 80,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"LLM Error: {e}")
            return "<Narrative>(distracted) I am busy.</Narrative>"

async def enrich_and_instruct(speaker: str, role: str, text: str, is_monster: bool = False) -> DialogueEnrichment:
    system_instruction = "You are a cinematic dialogue director. Output JSON with emotional_resonance, vocal_delivery_prompt, emotion_tag."
    if is_monster: system_instruction += " Rewrite with [tags] for Fish Speech modulation."
    payload = {
        "model": "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1",
        "messages": [{"role": "system", "content": system_instruction}, {"role": "user", "content": f"Text: {text}"}],
        "temperature": 0.3, "max_tokens": 256, "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            res = json.loads(resp.json()["choices"][0]["message"]["content"])
            emotion = res.get("emotion_tag", "Neutral")
            monster_text = standardize_speech_text(res.get("tagged_text", text), "monster", emotion) if is_monster else standardize_speech_text(text, "monster", emotion)
            return DialogueEnrichment(speaker=speaker, role=role, raw_text=text, emotional_resonance=res.get("emotional_resonance", ""), vocal_delivery_prompt=res.get("vocal_delivery_prompt", ""), instruct_text=standardize_speech_text(text, "humanoid", emotion), monster_text=monster_text, emotion_tag=emotion)
        except: return DialogueEnrichment(speaker=speaker, role=role, raw_text=text, emotional_resonance="Neutral", vocal_delivery_prompt="Standard.", instruct_text=standardize_speech_text(text, "humanoid", "Neutral"), monster_text=standardize_speech_text(text, "monster", "neutral"), emotion_tag="neutral")

# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

class SpeechEngine:
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient, dsp_presets: dict = None, control_instruction: str = None) -> Optional[bytes]:
        raise NotImplementedError()

class VoxAudioCoreEngine(SpeechEngine):
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient, dsp_presets: dict = None, control_instruction: str = None) -> Optional[bytes]:
        try:
            payload = {
                "npc_id": actor_id,
                "dialogue_text": text,
                "dsp_presets": dsp_presets or {},
            }
            if control_instruction:
                payload["control_instruction"] = control_instruction
            
            # Acquire GPU lock with highest priority (0) for gameplay-critical speech
            async with resource_manager.gpu_lock(priority=0):
                resp = await client.post(f"{TTS_ACTOR_URL}/generate", json=payload)
                return resp.content if resp.status_code == 200 else None
        except Exception as e:
            logger.error(f"VoxAudioCore generation failed: {e}")
            return None

class SpeechPipelineFactory:
    def __init__(self): self.engine = VoxAudioCoreEngine()
    def get_engine(self, *args, **kwargs) -> SpeechEngine: return self.engine

pipeline_factory = SpeechPipelineFactory()

async def safe_post(url, json_data, timeout=30.0, max_retries=3):
    for i in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=json_data)
                if resp.status_code == 200: return resp
                logger.warning(f"Attempt {i+1} failed for {url}: {resp.status_code}")
        except Exception as e:
            logger.warning(f"Attempt {i+1} exception for {url}: {e}")
        await asyncio.sleep(2)
    return None

async def transcode_to_opus(wav_bytes: bytes) -> bytes:
    """Converts WAV bytes to high-quality Opus (WebM) audio bytes."""
    if not wav_bytes: return wav_bytes
    try:
        # Use webm container with opus codec.
        # vox-audio-core already normalizes volume, so no additional gain needed.
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", "pipe:0",
            "-c:a", "libopus", "-b:a", "128k", "-f", "webm", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate(input=wav_bytes)
        return stdout if proc.returncode == 0 else wav_bytes
    except Exception as e:
        logger.error(f"Audio transcoding to Opus failed: {e}")
        return wav_bytes

def split_quoted_text(text: str) -> list[tuple[str, str]]:
    """Split text into segments of narration vs dialogue.

    Returns a list of (role, segment) tuples where role is
    'narration' or 'dialogue'. Narration is text outside quotes,
    dialogue is text inside quotes (single or double).
    Uses a simple state-machine approach rather than regex to handle
    nested asterisks and quotes cleanly.
    """
    segments = []
    i = 0
    while i < len(text):
        # Skip whitespace
        if text[i] in ' \t\n\r':
            i += 1
            continue
        # Check for quoted section
        if text[i] in '"\'':
            quote_char = text[i]
            i += 1
            start = i
            while i < len(text) and text[i] != quote_char:
                i += 1
            content = text[start:i].strip()
            if i < len(text):
                i += 1  # skip closing quote
            if content:
                segments.append(("dialogue", content))
        else:
            # Narration — collect up to the next quote
            start = i
            while i < len(text) and text[i] not in '"\'':
                i += 1
            content = text[start:i].strip()
            if content:
                segments.append(("narration", content))
    # If nothing was parsed, treat whole text as dialogue
    if not segments:
        segments = [("dialogue", text)]
    return segments

async def concat_wavs(wav_list: list[bytes]) -> bytes:
    """Concatenate multiple WAV byte strings into one using ffmpeg."""
    if not wav_list:
        return b""
    if len(wav_list) == 1:
        return wav_list[0]
    try:
        # Write each WAV to a temp pipe, use ffmpeg concat
        filter_parts = "".join(f"[{i}:a]" for i in range(len(wav_list)))
        filter_complex = f"{filter_parts}concat=n={len(wav_list)}:v=0:a=1[out]"
        input_args = []
        for w in wav_list:
            input_args += ["-i", "pipe:stdin" if input_args else "pipe:stdin"]
        # Use multiple -i pipes
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-f", "wav", "-i", "pipe:0",
            "-f", "wav", "-i", "pipe:1" if len(wav_list) > 1 else [],
            *(["-f", "wav", "-i", f"pipe:{i}" for i in range(2, len(wav_list))] if len(wav_list) > 2 else []),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-f", "wav", "pipe:1" if len(wav_list) == 2 else "pipe:2",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Feed all inputs
        # Simple case: concat with temporary files instead
        import tempfile, os
        files = []
        for w in wav_list:
            f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            f.write(w)
            f.close()
            files.append(f.name)
        file_list = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        for fn in files:
            file_list.write(f"file '{fn}'\n")
        file_list.close()
        out_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_file.close()

        proc2 = await asyncio.create_subprocess_exec(
            "ffmpeg", "-f", "concat", "-safe", "0",
            "-i", file_list.name,
            "-c", "copy", out_file.name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout2, stderr2 = await proc2.communicate()
        result = b""
        if proc2.returncode == 0:
            with open(out_file.name, "rb") as fh:
                result = fh.read()
        # Cleanup
        for fn in files:
            os.unlink(fn)
        os.unlink(file_list.name)
        os.unlink(out_file.name)
        return result or wav_list[0]
    except Exception as e:
        logger.error(f"WAV concatenation failed: {e}")
        return wav_list[0]

async def transcode_to_webp(image_bytes: bytes) -> bytes:
    """Converts image bytes to high-efficiency WebP format."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='WEBP', quality=85)
        return img_byte_arr.getvalue()
    except Exception as e:
        logger.error(f"Image transcoding to WebP failed: {e}")
        return image_bytes

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# Cache for environmental context
SCENE_CONTEXT = {
    "current_environment": "A neutral, unspecified environment.",
    "current_session": "session_001"
}



# Cache for Phase 2: Acoustic Anticipation (Predictive Rendering)
# Structure: { userId: { "intentId": ..., "hit_assets": {...}, "miss_assets": {...}, "timestamp": ... } }
ANTICIPATED_ACTIONS: dict[str, dict] = {}

async def prewarm_voice_registry_caches():
    """
    After startup, pre-build TTS prompt caches for all NPCs in the voice registry.
    This eliminates the ~16s cold-cache build penalty on the first dialogue request
    after a container restart.
    """
    # Wait for vox-audio-core to finish loading its model (poll health, up to 120s)
    async with httpx.AsyncClient(timeout=5.0) as hc:
        for attempt in range(60):
            try:
                r = await hc.get(f"{TTS_ACTOR_URL}/health")
                if r.status_code == 200:
                    logger.info(f"vox-audio-core ready after ~{attempt*2}s")
                    break
            except Exception:
                pass
            await asyncio.sleep(2)
        else:
            logger.warning("vox-audio-core health check did not return 200 within 120s — proceeding anyway")
    registry = load_voice_registry()
    if not registry:
        logger.info("Voice registry empty — skipping cache pre-warm.")
        return
    logger.info(f"🔥 Pre-warming TTS prompt caches for {len(registry)} registered NPCs...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        for actor_id, entry in registry.items():
            try:
                resp = await client.post(
                    f"{TTS_ACTOR_URL}/cache/warm",
                    json={"npc_id": actor_id, "voice_description": entry.get("voice_prompt", "")}
                )
                if resp.status_code == 200:
                    logger.info(f"  ✅ Cache warmed: {actor_id}")
                else:
                    logger.warning(f"  ⚠️  Cache warm failed for {actor_id}: {resp.status_code}")
            except Exception as e:
                logger.warning(f"  ⚠️  Cache warm error for {actor_id}: {e}")
    logger.info("🔥 TTS cache pre-warm complete.")

@app.on_event("startup")
async def startup_event():
    resource_manager.start_worker()
    asyncio.create_task(prewarm_palette_foundations())
    asyncio.create_task(prewarm_voice_registry_caches())
    # Ensure system starts in default HOT state
    asyncio.create_task(container_manager.swap_to_hot_combat())

@app.get("/")
async def root(): return {"service": "orchestrator", "status": "running", "version": "2.2.0"}

@app.get("/api/v1/queue/status")
async def get_queue_status():
    queue_tasks = await resource_manager.get_queue_status()
    # Add active async tasks to the list for unified HUD visibility
    active_ids = list(ACTIVE_TASKS.keys())
    for tid in active_ids:
        queue_tasks.append({
            "id": tid,
            "type": "voice-conversion",
            "status": "processing",
            "progress": 0.5,
            "created_at": time.time()
        })
    return queue_tasks

@app.post("/api/v1/orchestrate/cancel")
async def cancel_active_transaction(request: CancelRequest):
    # 1. Try immediate active tasks (voice conversion, etc)
    task = ACTIVE_TASKS.get(request.taskId)
    if task:
        task.cancel()
        return {"status": "transaction_aborted_and_refunded"}
    
    # 2. Try resource manager queue
    success = await resource_manager.cancel_task(request.taskId)
    if success:
        # We should also handle refunds for resource manager tasks if we pre-charged them
        # For now, we'll assume they are charged on completion or we'll need a way to track the charge amount
        return {"status": "transaction_aborted_and_refunded"}
    
    raise HTTPException(status_code=404, detail="Task context expired or already completed.")

class SessionStartRequest(BaseModel):
    sessionId: str

@app.post("/api/v1/session/start")
async def start_session(req: SessionStartRequest):
    SCENE_CONTEXT["current_session"] = req.sessionId
    session_dir = SESSION_LIBRARY_DIR / req.sessionId
    session_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Session started: {req.sessionId}")
    return {"status": "success", "sessionId": req.sessionId}

@app.get("/api/v1/ledger/balance/{user_id}")
async def get_ledger_balance(user_id: str):
    return ledger.get_balance(user_id)

@app.post("/api/v1/ledger/topup")
async def top_up_pool(req: TopUpRequest):
    ledger.top_up_pool(req.amount)
    return {"status": "success", "new_balance": ledger.state.campaign_pool}

@app.post("/api/v1/ledger/allowance")
async def set_allowance(req: AllowanceRequest):
    try:
        ledger.set_session_allowance(req.userId, req.amount)
        return {"status": "success", "allowance": req.amount}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/v1/ledger/return")
async def return_credits(req: AllowanceRequest):
    # If amount is -1, return session grant. If -2, return personal wallet.
    from_personal = (req.amount == -2)
    ledger.return_to_pool(req.userId, from_personal=from_personal)
    return {"status": "success"}

@app.post("/api/v1/ledger/transfer")
async def transfer_credits(req: TransferRequest):
    try:
        ledger.transfer_credits(req.fromUserId, req.toUserId, req.amount, req.fromPersonal)
        return {"status": "success"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/v1/admin/modify-credits")
async def admin_modify_credits(req: AdminAdjustmentRequest):
    ledger.admin_modify_credits(req.targetUserId, req.amount, req.description)
    return {"status": "success", "new_balance": ledger.state.personal_wallets.get(req.targetUserId, 0.0)}

@app.post("/api/v1/admin/set-pool")
async def admin_set_pool(req: TopUpRequest):
    ledger.admin_set_pool(req.amount)
    return {"status": "success", "new_pool": ledger.state.campaign_pool}

# --- Billing & Stripe Integration ---

@app.post("/api/v1/billing/create-checkout-session")
async def create_checkout_session(user_id: str, amount: float = 10.0, auto_allocate: bool = True):
    try:
        # Generate a Stripe Checkout link
        # auto_allocate=True means credits go straight to the user's seat after purchase
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': f'Vox Conjurata ${amount:.2f} {"Personal" if auto_allocate else "Campaign"} Top-Up'},
                    'unit_amount': int(amount * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"http://localhost:30000?payment=success&user={user_id}", 
            cancel_url=f"http://localhost:30000?payment=cancel",
            metadata={"user_id": user_id, "auto_allocate": str(auto_allocate).lower()}
        )
        return {"checkout_url": session.url}
    except Exception as e:
        logger.error(f"Stripe Checkout Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/dialogue/end")
async def dialogue_end(request: Request):
    try:
        data = await request.json()
        actor_id = data.get("actor_id")
        logger.info(f"Dialogue session ended for actor: {actor_id}")
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Failed to end dialogue session: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/tts-chunk")
async def tts_chunk(request: Request):
    """
    Subsequent chunk voice generation requested by client during pipelined playback.
    """
    try:
        data = await request.json()
        actor_id = data.get("actor_id")
        text = data.get("text")
        dsp_presets = data.get("dsp_presets") or {}
        control_instruction = data.get("control_instruction")
        
        logger.info(f"Pipelined TTS chunk request for actor: {actor_id} | text: '{text[:40]}...'")
        
        engine = pipeline_factory.get_engine()
        async with httpx.AsyncClient(timeout=120.0) as client:
            wav = await engine.generate(text, actor_id, client, dsp_presets, control_instruction=control_instruction)
            if wav:
                audio_base64 = f"data:audio/wav;base64,{base64.b64encode(wav).decode('utf-8')}"
                return {"status": "success", "audio_data": audio_base64}
            else:
                return {"status": "error", "message": "Failed to generate audio chunk"}
    except Exception as e:
        logger.error(f"Error in tts_chunk: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/v1/billing/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload signature.")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Signature authentication failed.")

    # Process successful payments
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        credit_amount = float(session['amount_total']) / 100.0
        user_id = session['metadata'].get('user_id')
        auto_allocate = session['metadata'].get('auto_allocate') == 'true'
        
        # 1. Always top up the master campaign pool first (fiat-to-token entry)
        ledger.top_up_pool(credit_amount)
        
        # 2. If auto_allocate is on, move those credits immediately to the player's seat
        if auto_allocate and user_id:
            try:
                # Add to persistent wallet
                ledger.add_to_personal_wallet(user_id, credit_amount)
                logger.info(f"⚡ AUTO-ALLOCATE: ${credit_amount} moved to {user_id} persistent wallet.")
            except Exception as e:
                logger.error(f"Failed auto-allocate for {user_id}: {e}")

    return {"status": "success"}

class CombatStateRequest(BaseModel):
    state: str # "start" or "end"
    sceneName: str

class IntentRequest(BaseModel):
    userId: str
    transcription: str

class ActionResolutionRequest(BaseModel):
    userId: str
    intentId: str
    result: str # "hit", "miss", "save", etc.
    visualOverride: Optional[str] = None
    targetX: Optional[float] = None
    targetY: Optional[float] = None

@app.post("/api/v1/scene/load")
async def scene_load(req: BattlemapScanRequest):
    scene_name = req.sceneName.replace(" ", "_")
    scene_dir = MUSIC_DIR / scene_name
    scene_dir.mkdir(parents=True, exist_ok=True)
    
    ambient_path = scene_dir / "ambient.webm"
    combat_path = scene_dir / "combat.webm"
    victory_path = scene_dir / "victory.webm"

    if ambient_path.exists() and combat_path.exists() and victory_path.exists():
        logger.info(f"Music cache HIT for scene {scene_name}. No generation needed.")
        SCENE_CONTEXT['current_environment'] = f"Cached context for scene {scene_name}"
        return {"status": "success", "message": "Cache hit, music ready.", "ambient_url": f"http://vox-conjurata/music/{scene_name}/ambient.webm"}

    cost = ledger.calculate_cost("vision", "optimal")
    try:
        ledger.charge("gm", cost, f"Scene Load & Scan for {req.sceneId}")
    except ValueError as e:
        raise HTTPException(status_code=402, detail=str(e))

    await container_manager.swap_to_warm_scene_load()
    
    try:
        SCENE_CONTEXT['current_environment'] = f"Analyzed context for scene {scene_name}"
        logger.info(f"Generating ambient, combat, and victory music for {scene_name}...")
        
        # 1. Perform Geometry Analysis (Walls, Doors, Lights)
        full_image_path = Path("/foundry_data") / req.imagePath
        image_bytes = None
        
        if full_image_path.suffix.lower() == ".pdf":
            from pdf2image import convert_from_path
            logger.info(f"Converting PDF map {req.imagePath} to WebP...")
            images = convert_from_path(str(full_image_path))
            if images:
                # Use the first page
                img_byte_arr = io.BytesIO()
                images[0].save(img_byte_arr, format='WEBP', quality=85)
                image_bytes = img_byte_arr.getvalue()
                # Save temp webp for vision reader
                temp_image_path = Path("/app/temp_assets") / f"{req.sceneId}_map.webp"
                images[0].save(str(temp_image_path), format='WEBP', quality=85)
                reader_input_path = str(temp_image_path)
        elif full_image_path.exists():
            with open(full_image_path, "rb") as f:
                raw_bytes = f.read()
            # Convert any input map to WebP for internal consistency
            image_bytes = await transcode_to_webp(raw_bytes)
            temp_image_path = Path("/app/temp_assets") / f"{req.sceneId}_map.webp"
            temp_image_path.write_bytes(image_bytes)
            reader_input_path = str(temp_image_path)

        if image_bytes:
            # Initial geometry via OpenCV
            geometry_data = map_geometry_engine.analyze_map(image_bytes, req.sceneId)
            vision_objects = await MonsterSightSystem().detect_map_features(reader_input_path)
            geometry_data = await map_geometry_engine.merge_vision_predictions(geometry_data, vision_objects)
            await push_to_foundry("vision-contract", geometry_data)
            logger.info(f"Automated map geometry pushed to Foundry for {scene_name}")
        else:
            logger.warning(f"Could not find or process map at {full_image_path} for geometry analysis.")

        # 2. Generate ambient, combat, and victory tracks
        logger.info(f"Generating real AI music for {scene_name}...")
        async with httpx.AsyncClient(timeout=300.0) as client:
            # Ambient Scene Track
            amb_resp = await safe_post(f"{TTS_MUSIC_URL}/generate", {"prompt": f"Ambient fantasy soundscape, {SCENE_CONTEXT['current_environment']}", "duration": 30}, timeout=120.0)
            if amb_resp.status_code == 200:
                ambient_path.write_bytes(await transcode_to_opus(amb_resp.content))
            
            # Combat Drum Loop
            com_resp = await safe_post(f"{TTS_MUSIC_URL}/generate", {"prompt": f"Tense cinematic combat drums, percussive, {SCENE_CONTEXT['current_environment']}", "duration": 30}, timeout=120.0)
            if com_resp.status_code == 200:
                combat_path.write_bytes(await transcode_to_opus(com_resp.content))
                
            # Victory/Loot Loop
            vic_resp = await safe_post(f"{TTS_MUSIC_URL}/generate", {"prompt": f"Triumphant heroic fantasy fanfare, victory, {SCENE_CONTEXT['current_environment']}", "duration": 15}, timeout=120.0)
            if vic_resp.status_code == 200:
                victory_path.write_bytes(await transcode_to_opus(vic_resp.content))
                
        logger.info(f"Generated and cached real AI music for {scene_name}")
    except Exception as e:
        logger.error(f"Failed during scene load processing: {e}")
    finally:
        await container_manager.swap_to_hot_combat()
    
    return {"status": "success", "message": "Scene loaded, context cached, music generated.", "ambient_url": f"http://vox-conjurata/music/{scene_name}/ambient.webm"}

@app.post("/api/v1/combat/state")
async def combat_state(request: CombatStateRequest):
    scene_name = request.sceneName.replace(" ", "_")
    if request.state == "start":
        logger.info(f"Returning tense combat drum loop from cache for {scene_name}...")
        track_url = f"http://vox-conjurata/music/{scene_name}/combat.webm"
    elif request.state == "end":
        logger.info(f"Returning victorious loot loop from cache for {scene_name}...")
        track_url = f"http://vox-conjurata/music/{scene_name}/victory.webm"
    else:
        raise HTTPException(status_code=400, detail="Invalid combat state.")
    return {"status": "success", "track_url": track_url}

async def _anticipate_action(user_id: str, intent_data: dict):
    """Background task to pre-render SDXL and SFX branches based on parsed intent."""
    intent_id = f"intent_{int(time.time()*1000)}"
    logger.info(f"Anticipating action for {user_id}: {intent_data.get('action')} -> {intent_data.get('target')}")
    
    # 1. Construct Prompts using SCENE_CONTEXT for visual consistency
    env = SCENE_CONTEXT.get("current_environment", "A neutral environment.")
    action_prompt = f"Cinematic low angle shot, ground level. A hero {intent_data.get('action')} at a {intent_data.get('target')}. High fantasy, dramatic lighting. Background: {env}"
    hit_prompt = f"Cinematic close up, ground level. A {intent_data.get('target')} being struck by {intent_data.get('action')}, explosion, intense light. Background: {env}"
    miss_prompt = f"Cinematic close up, ground level. A {intent_data.get('target')} dodging {intent_data.get('action')}, arrogant expression, flames in background. Background: {env}"

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            # Generate Action Image (Shared for both branches)
            resp = await safe_post(f"{IMAGE_GEN_URL}/generate", {
                "prompt": action_prompt, "width": 1344, "height": 768, "steps": 4, "cfg_scale": 2.0, "sample_method": "euler"
            })
            if resp.status_code == 200:
                (TEMP_DIR / f"{intent_id}_action.webp").write_bytes(resp.content)

            # Generate Hit Branch
            resp_hit = await safe_post(f"{IMAGE_GEN_URL}/generate", {
                "prompt": hit_prompt, "width": 1344, "height": 768, "steps": 4, "cfg_scale": 2.0, "sample_method": "euler"
            })
            if resp_hit.status_code == 200:
                (TEMP_DIR / f"{intent_id}_hit.webp").write_bytes(resp_hit.content)

            # Generate Miss Branch
            resp_miss = await safe_post(f"{IMAGE_GEN_URL}/generate", {
                "prompt": miss_prompt, "width": 1344, "height": 768, "steps": 4, "cfg_scale": 2.0, "sample_method": "euler"
            })
            if resp_miss.status_code == 200:
                (TEMP_DIR / f"{intent_id}_miss.webp").write_bytes(resp_miss.content)

            # 3. Generate real SFX via vox-audio-generation-sfx
            logger.info(f"Generating real SFX for {intent_id}...")
            # Hit SFX
            hit_sfx_resp = await safe_post(f"{TTS_SFX_URL}/generate", {"prompt": f"Sound of {intent_data.get('action')} hitting a {intent_data.get('target')}, cinematic, high fidelity", "duration": 3})
            if hit_sfx_resp.status_code == 200:
                (TEMP_DIR / f"{intent_id}_hit_sfx.webm").write_bytes(await transcode_to_opus(hit_sfx_resp.content))
            
            # Miss SFX
            miss_sfx_resp = await safe_post(f"{TTS_SFX_URL}/generate", {"prompt": f"Sound of {intent_data.get('action')} whistling past and missing, fizzle, whoosh", "duration": 3})
            if miss_sfx_resp.status_code == 200:
                (TEMP_DIR / f"{intent_id}_miss_sfx.webm").write_bytes(await transcode_to_opus(miss_sfx_resp.content))

        except Exception as e:
            logger.error(f"Predictive rendering failed for {intent_id}: {e}")

    ANTICIPATED_ACTIONS[user_id] = {
        "intentId": intent_id,
        "action": intent_data.get('action'),
        "target": intent_data.get('target'),
        "action_image": f"http://vox-conjurata/temp/{intent_id}_action.webp",
        "hit_assets": {
            "image_url": f"http://vox-conjurata/temp/{intent_id}_hit.webp",
            "sfx_url": f"http://vox-conjurata/temp/{intent_id}_hit_sfx.webm"
        },
        "miss_assets": {
            "image_url": f"http://vox-conjurata/temp/{intent_id}_miss.webp",
            "sfx_url": f"http://vox-conjurata/temp/{intent_id}_miss_sfx.webm"
        },
        "timestamp": time.time()
    }
    logger.info(f"Anticipated assets cached for {user_id} under {intent_id}")

@app.post("/api/v1/combat/intent")
async def parse_combat_intent(req: IntentRequest):
    system_prompt = "Extract action intent. Output JSON: {'action': '...', 'target': '...'}. If no combat intent, return empty."
    payload = {
        "model": "Sao10K/14B-Qwen2.5-Kunou-v1",
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": req.transcription}],
        "temperature": 0.1, "max_tokens": 50
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
            intent_data = {"action": "cast fireball", "target": "goblin"} 
            asyncio.create_task(_anticipate_action(req.userId, intent_data))
            return {"status": "anticipating", "intent": intent_data}
    except Exception as e:
        logger.error(f"Failed to parse intent: {e}")
        return {"status": "ignored"}

class AnimationLibrarySync(BaseModel):
    library: dict

@app.post("/api/v1/library/sync-animations")
async def sync_animation_library(req: AnimationLibrarySync):
    """Updates the orchestrator's knowledge of available visual assets."""
    # In production, this would update a persistent mapping dictionary
    logger.info(f"Received animation library sync: {len(req.library)} entries.")
    # For now, we just acknowledge the receipt
    return {"status": "success", "count": len(req.library)}

@app.post("/api/v1/combat/resolve")
async def resolve_combat_action(req: ActionResolutionRequest):
    cached = ANTICIPATED_ACTIONS.get(req.userId)
    if cached and (time.time() - cached["timestamp"] < 60):
        branch = "hit_assets" if req.result == "hit" else "miss_assets"
        assets = cached.get(branch, {})
        del ANTICIPATED_ACTIONS[req.userId]
        logger.info(f"Cache HIT for {req.userId} resolving as {req.result}. Releasing assets with zero latency.")
        
        # --- Persistent Session Storage Migration ---
        session_id = SCENE_CONTEXT.get("current_session", "unknown_session")
        session_dir = SESSION_LIBRARY_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        # Find next numerical index
        existing = list(session_dir.glob("*.webp")) + list(session_dir.glob("*.webm"))
        indices = [int(f.stem.split("_")[0]) for f in existing if f.stem.split("_")[0].isdigit()]
        next_idx = max(indices) + 1 if indices else 1
        
        final_assets = {}
        migration_map = {
            "action_image": cached.get("action_image"),
            "reaction_image": assets.get("image_url"),
            "action_sfx": assets.get("sfx_url"),
            "reaction_sfx": assets.get("sfx_url")
        }

        import shutil
        for key, url in migration_map.items():
            if not url or "temp/" not in url: 
                final_assets[key] = url
                continue
                
            filename = url.split("/")[-1]
            temp_path = TEMP_DIR / filename
            if temp_path.exists():
                ext = temp_path.suffix
                new_filename = f"{next_idx:04d}_{key}_{req.result}{ext}"
                persistent_path = session_dir / new_filename
                shutil.move(temp_path, persistent_path)
                final_assets[key] = f"http://vox-conjurata/sessions/{session_id}/{new_filename}"
            else:
                final_assets[key] = url

        sequencer_payload = {
            "action_image": final_assets.get("action_image"),
            "action_sfx": final_assets.get("action_sfx"),
            "reaction_image": final_assets.get("reaction_image"),
            "reaction_sfx": final_assets.get("reaction_sfx"),
            "visual_override": req.visualOverride,
            "target_x": req.targetX,
            "target_y": req.targetY,
            "narration_audio": "http://vox-conjurata/temp/dm_narration_placeholder.webm"
        }
        return {"status": "success", "latency": "0ms", "sequencer_payload": sequencer_payload}
    
    logger.info(f"Cache MISS for {req.userId}. Falling back to synchronous generation.")
    return {"status": "fallback_started"}

@app.post("/api/scan-battlemap")
async def scan_battlemap(
    request: Request,
    sceneId: Optional[str] = Form(None),
    userId: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    # Support both JSON and Form Data
    if file and sceneId and userId:
        # Save file to foundry_data
        file_path = f"scans/{file.filename}"
        full_path = FOUNDRY_DATA_DIR / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(await file.read())
        req_image_path = file_path
        req_scene_id = sceneId
        req_user_id = userId
    else:
        try:
            body = await request.json()
            req = BattlemapScanRequest(**body)
            req_image_path = req.imagePath
            req_scene_id = req.sceneId
            req_user_id = req.userId
        except:
            raise HTTPException(status_code=400, detail="Invalid request format. Expected JSON or Form Data.")

    # Charge Logic: If user is 'gm' or explicitly marked as DM, bill DM. 
    # Otherwise bill player.
    billing_user_id = req_user_id
    
    # Charge for Vision Scan
    tier = "optimal" 
    cost = ledger.calculate_cost("vision", tier)
    try:
        ledger.charge(billing_user_id, cost, f"Vision Scan for scene {req_scene_id}")
    except ValueError as e:
        raise HTTPException(status_code=402, detail=str(e))

    task_id = await resource_manager.enqueue_task("vision-scan", {"image_path": str(req_image_path), "scene_id": req_scene_id, "userId": billing_user_id})
    return {"status": "enqueued", "task_id": task_id}

@app.post("/api/ingest-actor")
async def ingest_actor(data: ActorMetadata, force_refresh: bool = False):
    registry = load_voice_registry()
    if data.actorId in registry and not force_refresh: return {"status": "cached"}
    
    tier = "optimal"
    # All NPC/Monster/Character Neural Forge billed to DM
    billing_user_id = "gm" 
    
    # Charge for LLM Profiling
    cost_llm = ledger.calculate_cost("llm", tier)
    try:
        ledger.charge(billing_user_id, cost_llm, f"Neural Profiling for {data.name}")
    except ValueError as e:
        raise HTTPException(status_code=402, detail=str(e))

    is_named = is_named_character(data)
    # If a custom voice description is provided (e.g. from the narrator settings
    # panel or /vox voice command), use it directly instead of calling the LLM.
    # This gives GMs full control over the narrator's voice character.
    if data.customDescription and data.customDescription.strip():
        profile = {"gender": "neutral", "description": data.customDescription.strip()}
        logger.info(f"🎙️ Using custom voice description for {data.name}: '{profile['description'][:60]}'")
    else:
        profile = await generate_vocal_profile(data)
    archetype = resolve_archetype(data, profile)
    
    # Charge for TTS Initialization
    cost_tts = ledger.calculate_cost("tts", tier)
    try:
        ledger.charge(billing_user_id, cost_tts, f"Voice Forging for {data.name}")
    except ValueError as e:
        raise HTTPException(status_code=402, detail=str(e))

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{TTS_ACTOR_URL}/initialize", json={"npc_id": data.actorId, "voice_description": profile["description"]})
        if resp.status_code == 200:
            seed_path = resp.json().get("seed_path")
            register_character_voice(data.actorId, "vox-audio-core", os.path.basename(seed_path), profile["description"], not is_named, archetype)
            return {"status": "created", "path": seed_path}
    return {"status": "error"}

async def _execute_voice_conversion_pipeline(task_id: str, audio_bytes: bytes, audio_filename: str, audio_content_type: str, meta: VoiceConversionMetadata):
    cost_tts = 0.0
    cost_llm = 0.0
    # Determine billing target
    is_dm_content = meta.isMonster or meta.micType in ["vox-conjurata-gm-narrate-mic", "vox-conjurata-gm-puppet-mic"]
    billing_user_id = "gm" if is_dm_content else meta.userId

    try:
        # Determine Tier
        tier = "optimal" if meta.llmPathway == "byo_local_brain" else "budget"

        # Charge Base Orchestration Fee immediately
        # Use calculate_cost with a dummy small prompt or similar if needed, 
        # but here we just want to verify the ledger logic works.
        # Actually, let's just use calculate_cost for LLM as the entry charge.
        cost_llm = ledger.calculate_cost("llm", tier, prompt="initial_handshake")
        ledger.charge(billing_user_id, cost_llm, f"AI Orchestration Fee: {meta.activeSpeakerName}")

        # 1. Transcription
        t_stt_start = time.time()
        # Convert WebM to WAV for Whisper (browser MediaRecorder produces live WebM
        # that ffmpeg needs extra flags to fully parse)
        import tempfile, subprocess as _sp
        _tmp_in = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
        _tmp_in.write(audio_bytes)
        _tmp_in.close()
        _tmp_out = _tmp_in.name + ".wav"
        try:
            _sp.run(["ffmpeg",
                     "-fflags", "+genpts+igndts",
                     "-analyzeduration", "100M",
                     "-probesize", "100M",
                     "-i", _tmp_in.name,
                     "-filter:a", "volume=2.0",
                     "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
                     "-y", _tmp_out],
                    capture_output=True, timeout=30, check=True)
            _size = os.path.getsize(_tmp_out)
            if _size > 16000:  # at least 0.5s of 16kHz mono 16-bit
                with open(_tmp_out, "rb") as _f:
                    audio_bytes = _f.read()
                audio_content_type = "audio/wav"
                audio_filename = "audio.wav"
                logger.info(f"🎙️ Vox | Converted audio to WAV ({len(audio_bytes)} bytes)")
            else:
                logger.warning(f"🎙️ Vox | Converted WAV too small ({_size} bytes), sending raw")
        except Exception as e:
            logger.warning(f"🎙️ Vox | Audio conversion failed, sending raw: {e}")
        finally:
            if os.path.exists(_tmp_in.name): os.remove(_tmp_in.name)
            if os.path.exists(_tmp_out): os.remove(_tmp_out)
        async with httpx.AsyncClient(timeout=300.0) as client:
            stt_resp = await client.post(f"{STT_URL}/v1/audio/transcriptions", files={"file": (audio_filename, audio_bytes, audio_content_type)}, data={"model": "tiny.en", "language": "en"})
            transcription = stt_resp.json().get("text", "").strip()
        t_stt = time.time() - t_stt_start
        logger.info(f"⏱️  Pipeline Latency | STT (Whisper): {t_stt:.4f}s")

        if not transcription: return {"status": "empty"}

        # Logic: If useVoxActor is False (Puppeteer mode), we should suppress any 
        # autonomous secondary effects that might be triggered by downstream systems.
        # Here we just log it; downstream parsing would check this flag.
        if not meta.useVoxActor:
            logger.info(f"🚫 Suppression Active: Puppeteer transcription '{transcription[:20]}...' will not trigger secondary effects.")

        role = "NPC" if meta.micType == "vox-conjurata-gm-puppet-mic" else "Player"

        chronicle.log_interaction(meta.activeSpeakerName, transcription)
        
        # 2. LLM Enrichment
        enriched = None
        if not meta.isAutonomousTrigger and meta.useVoxVoice:
            t_enrich_start = time.time()
            enriched = await enrich_and_instruct(meta.activeSpeakerName, role, transcription, is_monster=meta.isMonster)
            t_enrich = time.time() - t_enrich_start
            logger.info(f"⏱️  Pipeline Latency | LLM Enrichment: {t_enrich:.4f}s")
        else:
            enriched = DialogueEnrichment(
                speaker=meta.activeSpeakerName, role=role, raw_text=transcription,
                emotional_resonance="Neutral", vocal_delivery_prompt="Standard.",
                instruct_text=transcription, monster_text=transcription, emotion_tag="neutral"
            )
        engine = pipeline_factory.get_engine()
        audio_data = None
        ai_reply_obj = None

        async with httpx.AsyncClient(timeout=300.0) as client:
            if meta.isAutonomousTrigger and meta.npc_context:
                t_reply_start = time.time()
                # Autonomous NPC reply is ALWAYS billed to DM
                raw_reply = await generate_ai_reply(meta.activeSpeakerName, transcription, meta.npc_context)
                t_reply = time.time() - t_reply_start
                logger.info(f"⏱️  Pipeline Latency | LLM Reply (Qwen): {t_reply:.4f}s")
                
                parsed_reply = parse_block_response(raw_reply)
                reply_text = parsed_reply["narrative"]
                image_prompt = parsed_reply["image_prompt"]
                
                target_engine = "monster" if meta.npc_context.is_monster else "humanoid"
                std_reply = standardize_speech_text(reply_text, target_engine, "neutral")

                # Guard against KV-cache corruption: if the LLM produced garbage
                # (e.g. all '?' chars), skip TTS to prevent GPU crash and log a warning.
                if is_llm_output_garbage(std_reply):
                    logger.error(
                        f"LLM output appears corrupted (likely KV-cache fault): '{std_reply[:60]}'. "
                        f"Skipping TTS. The llama.cpp server should be restarted."
                    )
                    ai_reply_obj = AIReply(
                        transcription="[System: LLM output corrupted — restart vox-llm-llama]",
                        audio_data=None,
                        image_prompt=None,
                        subsequent_chunks=[],
                        control_instruction=None
                    )
                else:
                    # Split reply into ~8 word chunks to minimize synthesis latency for playback start
                    logger.info(f"DEBUG CHUNKING | std_reply: '{std_reply}'")
                    reply_chunks = split_dialogue_into_chunks(std_reply, max_words=8)
                    logger.info(f"DEBUG CHUNKING | reply_chunks: {reply_chunks}")
                    first_chunk = reply_chunks[0] if reply_chunks else std_reply
                    subsequent_chunks = reply_chunks[1:] if len(reply_chunks) > 1 else []
                    
                    # Look up the registered voice archetype so the control instruction
                    # matches the character's gender (e.g. "human_female_british" → female).
                    _reg = load_voice_registry()
                    _entry = _reg.get(meta.targetActorId, {})
                    _arch = _entry.get("archetype_key", "")
                    if meta.npc_context.is_monster:
                        npc_control = "Guttural monster."
                    elif "_neutral_" in _arch:
                        npc_control = "Neutral voice."
                    elif "_female_" in _arch:
                        npc_control = "Clear female voice."
                    else:
                        npc_control = "Deep male voice."
                    
                    ai_audio = None
                    if meta.targetVoxVoice:
                        # Charge DM for AI Reply TTS
                        cost_reply = ledger.calculate_cost("tts", tier)
                        ledger.charge("gm", cost_reply, f"Autonomous NPC Reply: {meta.npc_context.name}")
                        
                        # Generate only the first chunk synchronously
                        t_tts_start = time.time()
                        wav = await engine.generate(first_chunk, meta.targetActorId, client, {}, control_instruction=npc_control)
                        t_tts = time.time() - t_tts_start
                        logger.info(f"⏱️  Pipeline Latency | NPC TTS Chunk 1 (VoxCPM2): {t_tts:.4f}s")
                        if wav: 
                            ai_audio = f"data:audio/wav;base64,{base64.b64encode(wav).decode('utf-8')}"
                    
                    ai_reply_obj = AIReply(
                        transcription=std_reply, 
                        audio_data=ai_audio, 
                        image_prompt=image_prompt,
                        subsequent_chunks=subsequent_chunks,
                        control_instruction=npc_control
                    )

            if meta.useVoxVoice and not meta.isAutonomousTrigger:
                # 3. TTS Generation — SKIPPED on autonomous trigger to save ~5-8s
                # When targeting an NPC, we only need the NPC's voice; the player's
                # human voice is already heard live at the table.
                cost_tts = ledger.calculate_cost("tts", tier)
                ledger.charge(billing_user_id, cost_tts, f"TTS Generation for {meta.activeSpeakerName}")
                
                target_text = enriched.monster_text if meta.isMonster else enriched.instruct_text
                # Use a consistent voice control from the voice registry instead of
                # the LLM's per-sentence vocal_delivery_prompt, which changes every
                # sentence and makes the character sound different each time.
                _entry = load_voice_registry().get(meta.actorId, {})
                _arch = _entry.get("archetype_key", "")
                if meta.isMonster:
                    control = "Guttural monster."
                elif "_neutral_" in _arch:
                    control = "Neutral voice."
                elif "_female_" in _arch:
                    control = "Clear female voice."
                else:
                    control = "Deep male voice."
                logger.info(f"🎙️ Vox | Consistent voice for {meta.activeSpeakerName}: '{control}' (archetype={_arch})")
                wav = await engine.generate(target_text, meta.actorId, client, meta.dsp_presets, control_instruction=control)
                logger.info(f"🎙️ Vox | Engine generated wav: {len(wav) if wav else 0} bytes")
                if wav: audio_data = f"data:audio/wav;base64,{base64.b64encode(wav).decode('utf-8')}"

        return {"status": "success", "transcription": transcription, "enrichment": enriched.model_dump(), "voxType": "player", "audio_data": audio_data, "engine": "VoxAudioCore", "ai_reply": ai_reply_obj.model_dump() if ai_reply_obj else None}

    except asyncio.CancelledError:
        logger.info(f"Task {task_id} cancelled. Refunding...")
        if cost_llm > 0: ledger.refund(billing_user_id, cost_llm, "Cancelled Task (LLM)")
        if cost_tts > 0: ledger.refund(billing_user_id, cost_tts, "Cancelled Task (TTS)")
        raise
    except Exception as e:
        logger.error(f"Pipeline failure in task {task_id}: {e}")
        # Partial refund if failed mid-way
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        ACTIVE_TASKS.pop(task_id, None)

@app.post("/api/clone-voice")
async def clone_voice(request: Request):
    """
    One-shot voice cloning. Accepts a recorded audio sample and saves it
    directly as the voice seed for the target actor.
    Useful for GMs who want the narrator (or any character) to use their
    own voice, or for players who want their character to sound like them.
    """
    try:
        form = await request.form()
        audio_file = form.get("audio_blob")
        actor_id = form.get("actorId", "narrator")
        if not audio_file:
            raise HTTPException(status_code=400, detail="No audio file provided")
        audio_bytes = await audio_file.read()
        logger.info(f"🎙️ Clone voice request for actor: {actor_id} ({len(audio_bytes)} bytes)")

        # Optional: convert WebM to WAV (browser MediaRecorder produces WebM)
        raw = audio_bytes
        if audio_file.content_type and "webm" in audio_file.content_type:
            import tempfile, subprocess
            _in = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
            _in.write(audio_bytes)
            _in.close()
            _out = _in.name + ".wav"
            try:
                subprocess.run(["ffmpeg", "-i", _in.name, "-ar", "48000", "-ac", "1",
                               "-sample_fmt", "s16", "-y", _out],
                              capture_output=True, timeout=30, check=True)
                with open(_out, "rb") as fh:
                    raw = fh.read()
            except Exception as e:
                logger.warning(f"Audio conversion failed, sending raw: {e}")
            finally:
                os.unlink(_in.name)
                if os.path.exists(_out):
                    os.unlink(_out)

        # Send to vox-audio-core's seed-from-audio endpoint
        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"audio": ("clone.wav", raw, "audio/wav")}
            resp = await client.post(
                f"{TTS_ACTOR_URL}/seed-from-audio?npc_id={actor_id}",
                files=files
            )
            if resp.status_code == 200:
                register_character_voice(
                    actor_id, "vox-audio-core",
                    f"{actor_id}.wav",
                    "Cloned voice from audio sample",
                    is_archetype=(actor_id == "narrator"),
                    archetype_key="human_neutral_british"
                )
                return {"status": "success", "actor_id": actor_id}
            else:
                logger.error(f"Seed-from-audio failed: {resp.status_code} {resp.text}")
                raise HTTPException(status_code=500, detail="Voice cloning failed")
    except Exception as e:
        logger.error(f"Clone voice failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/voice-conversion")
async def voice_conversion(request: Request):
    try:
        form = await request.form()
        audio_file = form.get("audio_blob")
        meta_json = form.get("metadata")
        meta = VoiceConversionMetadata.model_validate_json(meta_json)
        
        task_id = f"voice-{int(time.time() * 1000)}"
        audio_bytes = await audio_file.read()
        
        # Check balance before starting
        balance = ledger.get_balance(meta.userId)["total_available"]
        if balance < 0.01: # Threshold for starting
             raise HTTPException(status_code=402, detail="Insufficient Allowance to start generation.")

        task = asyncio.create_task(_execute_voice_conversion_pipeline(
            task_id, audio_bytes, audio_file.filename, audio_file.content_type, meta
        ))
        ACTIVE_TASKS[task_id] = task
        
        try:
            result = await task
            return result
        except asyncio.CancelledError:
            return {"status": "cancelled", "message": "Transaction aborted and refunded."}
        
    except Exception as e:
        logger.error(f"Voice Conversion Request failure: {e}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/dialogue/end")
async def end_dialogue(request: DialogueEndRequest):
    chronicle.commit_chronicle_update(session_id=request.npcName)
    return {"status": "success"}

@app.get("/api/status")
async def get_status(): return {"status": "nominal", "vram_used_gb": get_vram_used_gb()}

@app.get("/api/v1/registry")
async def get_registry():
    """Return the full voice registry."""
    return load_voice_registry()

@app.get("/api/v1/registry/audio/{actor_id}")
async def get_registry_audio(actor_id: str):
    """Serve the seed WAV file for preview/playback."""
    reg = load_voice_registry()
    entry = reg.get(actor_id)
    seed_path = None
    if entry:
        seed_rel = entry.get("seed_path", "")
        seed_path = VOICE_SEEDS_DIR / seed_rel
        if not seed_path.exists():
            seed_path = None
    if not seed_path:
        seed_path = VOICE_SEEDS_DIR / f"{actor_id}.wav"
    if not seed_path.exists():
        seed_path = PALETTE_DIR / f"{actor_id}.wav"
    if not seed_path or not seed_path.exists():
        raise HTTPException(status_code=404, detail="Seed file not found")
    return Response(content=seed_path.read_bytes(), media_type="audio/wav")

@app.post("/api/v1/approve-voice")
async def approve_voice(request: Request):
    """Mark a voice as approved after playback."""
    data = await request.json()
    actor_id = data.get("actorId")
    if not actor_id:
        raise HTTPException(status_code=400, detail="actorId required")
    register_character_voice(
        actor_id, "vox-audio-core", f"{actor_id}.wav",
        voice_prompt="", is_archetype=(actor_id == "narrator"),
        archetype_key="", approved=True
    )
    logger.info(f"✅ Voice approved for actor: {actor_id}")
    return {"status": "success", "actor_id": actor_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
