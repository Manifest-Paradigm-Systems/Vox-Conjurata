import re
import os

file_path = "services/orchestrator/production_brain.py"

with open(file_path, "r") as f:
    content = f.read()

# Add re import if missing
if "import re" not in content:
    content = "import re\n" + content

# 1. Implement clean_tts_text helper
clean_func = """
def clean_tts_text(text: str) -> str:
    \"\"\"Strips metadata tags, bracketed instructions, and artifacts from LLM-generated dialogue.\"\"\"
    # Remove bracketed tags like [Neutral], [enraged growl]
    text = re.sub(r'\[.*?\]', '', text)
    # Remove parenthetical tags like (neutral), (Whispering)
    text = re.sub(r'\(.*?\)', '', text)
    # Remove common metadata prefixes
    text = re.sub(r'^(Mood|Emotion|Sentiment|Tone|Note|Instruction|Direction):\s*', '', text, flags=re.IGNORECASE)
    # Remove leading "neutral:" or similar followed by space
    text = re.sub(r'^\w+:\s+', '', text)
    # Final cleanup of whitespace
    return text.strip()
"""

if "def clean_tts_text" not in content:
    # Insert after helper functions comment
    content = content.replace("# --- Helper Functions ---", "# --- Helper Functions ---" + clean_func)

# 2. Update forge_voice_seed to save transcript
old_forge = """async def forge_voice_seed(actor_id: str, acoustic_description: str, gender: str = "male") -> str:
    \"\"\"Calls Parler-TTS (vox-designer) to create a unique 10s voice print.\"\"\"
    seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_{gender}.wav"
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(f"{TTS_DESIGNER_URL}/generate", json={"text": acoustic_description})
            if response.status_code == 200:
                with open(seed_path, "wb") as f: f.write(response.content)
                return str(seed_path)
            return ""
        except Exception as e:
            logger.error(f"Seed forge error: {e}"); return \"\"\"

new_forge = """async def forge_voice_seed(actor_id: str, acoustic_description: str, gender: str = "male") -> str:
    \"\"\"Calls Parler-TTS (vox-designer) to create a unique 10s voice print.\"\"\"
    seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_{gender}.wav"
    text_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_{gender}.txt"
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(f"{TTS_DESIGNER_URL}/generate", json={"text": acoustic_description})
            if response.status_code == 200:
                with open(seed_path, "wb") as f: f.write(response.content)
                with open(text_path, "w") as f: f.write(acoustic_description)
                logger.info(f"[VOICE-SEED] Forged seed and saved transcript for {actor_id}")
                return str(seed_path)
            return ""
        except Exception as e:
            logger.error(f"Seed forge error: {e}"); return \"\"\"

content = content.replace(old_forge, new_forge)

# 3. Update FishSpeechEngine to include reference text
old_fish = """class FishSpeechEngine(SpeechEngine):
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient) -> Optional[bytes]:
        seeds = list(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.wav"))
        seed_path = seeds[0] if seeds else None
        
        # Fish Speech 1.5 prefers a reference audio for in-context learning
        # If no specific seed, we use the narrator seed as fallback
        if not seed_path:
            seed_path = VOICE_SEEDS_DIR / "narrator_seed_male.wav"

        try:
            logger.info(f"[VOICE-ROUTING] Fish Speech using reference: {seed_path.name if seed_path else 'None'}")
            
            # Prepare references in the format Fish Speech API expects (Base64 encoded)
            import base64
            references = []
            if seed_path and seed_path.exists():
                with open(seed_path, "rb") as f:
                    audio_b64 = base64.b64encode(f.read()).decode("utf-8")
                    references.append({
                        "audio": audio_b64,
                        "text": "" # We don't have the transcript for the seed yet
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
                logger.error(f"[VOICE-ROUTING] Fish Speech service returned error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"[VOICE-ROUTING] Fish Speech inference failed: {e}")
        return None"""

new_fish = """class FishSpeechEngine(SpeechEngine):
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
        return None"""

content = content.replace(old_fish, new_fish)

# 4. Use clean_tts_text in enrich_and_instruct
old_enrich_logic = """            emotion = res.get("emotion_tag", "Neutral").strip()
            
            # Formatting for Fish Speech (Monster): lowercase inline square brackets
            # Example: [enraged growl] Dialogue text
            monster_text = f"[{emotion.lower()}] {text}"
            
            # Formatting for CosyVoice (Humanoid): Title Case <|endofprompt|> Dialogue text
            # Example: Enraged Growl <|endofprompt|> Dialogue text
            instruct_text = f"{emotion.capitalize()} <|endofprompt|> {text}"
            
            return DialogueEnrichment(
                speaker=speaker, role=role, raw_text=text,
                emotional_resonance=res.get("emotional_resonance", emotion),
                vocal_delivery_prompt=res.get("vocal_delivery_prompt", f"Deliver as {emotion}."),
                instruct_text=instruct_text,
                monster_text=monster_text
            )"""

new_enrich_logic = """            emotion = res.get("emotion_tag", "Neutral").strip()
            
            # Clean the actual spoken text of any metadata tags or artifacts
            clean_text = clean_tts_text(text)
            
            # Formatting for Fish Speech (Monster): lowercase inline square brackets
            monster_text = f"[{emotion.lower()}] {clean_text}"
            
            # Formatting for CosyVoice (Humanoid): Title Case <|endofprompt|> Dialogue text
            instruct_text = f"{emotion.capitalize()} <|endofprompt|> {clean_text}"
            
            return DialogueEnrichment(
                speaker=speaker, role=role, raw_text=text,
                emotional_resonance=res.get("emotional_resonance", emotion),
                vocal_delivery_prompt=res.get("vocal_delivery_prompt", f"Deliver as {emotion}."),
                instruct_text=instruct_text,
                monster_text=monster_text
            )"""

content = content.replace(old_enrich_logic, new_enrich_logic)

# 5. Strict Pipeline Verification & Fallback Logging
old_fallback_logic = """            if res_content is None and not isinstance(engine, EdgeTTSEngine):
                logger.warn(f"Engine {engine_name} failed. Falling back to Edge-TTS Cloud.")
                engine_name = "Edge-TTS (Fallback)"
                fallback_voice = config.get("narrator_preferences", {}).get("default_voice", "en-US-ChristopherNeural")
                rate = config.get("narrator_preferences", {}).get("rate_adjustment", "+0%")
                edge_engine = EdgeTTSEngine(voice_name=fallback_voice, rate=rate)
                res_content = await edge_engine.generate(transcription, actor_id, client)"""

new_fallback_logic = """            if res_content is None and not isinstance(engine, EdgeTTSEngine):
                logger.error(f"🚨 [PIPELINE-CRITICAL] High-fidelity engine {engine_name} failed to generate audio for {actor_id}!")
                logger.warn(f"⚠️ [FALLBACK] Reverting to generic Edge-TTS Cloud as an emergency failsafe.")
                engine_name = "Edge-TTS (Fallback)"
                fallback_voice = config.get("narrator_preferences", {}).get("default_voice", "en-US-ChristopherNeural")
                rate = config.get("narrator_preferences", {}).get("rate_adjustment", "+0%")
                edge_engine = EdgeTTSEngine(voice_name=fallback_voice, rate=rate)
                res_content = await edge_engine.generate(clean_tts_text(transcription), actor_id, client)"""

content = content.replace(old_fallback_logic, new_fallback_logic)

with open(file_path, "w") as f:
    f.write(content)

print("Audio pipeline patches applied successfully.")
