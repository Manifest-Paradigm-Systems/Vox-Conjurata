import os
import sys

file_path = "services/orchestrator/production_brain.py"

with open(file_path, "r") as f:
    content = f.read()

# 1. Update CosyVoiceEngine to log seed loading status
old_cosy_engine = """class CosyVoiceEngine(SpeechEngine):
    async def generate(self, text: str, actor_id: str, client: httpx.AsyncClient) -> Optional[bytes]:
        seeds = list(VOICE_SEEDS_DIR.glob(f"{actor_id}_seed_*.wav"))
        seed_path = seeds[0] if seeds else None
        
        if not seed_path:
            seed_path = VOICE_SEEDS_DIR / f"{actor_id}_seed_male.wav"
            await forge_voice_seed(actor_id, f"A clear speaking voice for {actor_id}.", "male")
        
        if seed_path and seed_path.exists():
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
        return None"""

new_cosy_engine = """class CosyVoiceEngine(SpeechEngine):
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
                with open(seed_path, "rb") as f:
                    resp = await client.post(
                        f"{TTS_ACTOR_URL}/api/tts",
                        data={"text": text},
                        files={"reference_audio": (seed_path.name, f, "audio/wav")}
                    )
                if resp.status_code == 200:
                    return resp.content
                else:
                    logger.error(f"[VOICE-ROUTING] CosyVoice service returned error {resp.status_code}: {resp.text}")
            except Exception as e:
                logger.error(f"[VOICE-ROUTING] CosyVoice inference failed: {e}")
        else:
            logger.error(f"[VOICE-ROUTING] Failed to locate or create seed for {actor_id} at {seed_path}")
        return None"""

content = content.replace(old_cosy_engine, new_cosy_engine)

# 2. Update FishSpeechEngine to use standard Fish Speech API format
old_fish_engine = """class FishSpeechEngine(SpeechEngine):
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
        return None"""

new_fish_engine = """class FishSpeechEngine(SpeechEngine):
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

content = content.replace(old_fish_engine, new_fish_engine)

with open(file_path, "w") as f:
    f.write(content)

print("Patch applied successfully.")
