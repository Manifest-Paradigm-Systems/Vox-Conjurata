"""
Vox-Chronicle Automation Layer
File: chronicle.py
"""

import logging
import time
import json
import os
import asyncio
from resource_manager import resource_manager
from foundry_client import push_to_foundry, log_to_foundry

logger = logging.getLogger("vox-chronicle")

class VoxChronicleSystem:
    def __init__(self, api_url="http://vox-llm-openrouter:8081/v1/chat/completions"):
        self.api_url = api_url
        self.sliding_window_history = []

    def log_interaction(self, speaker: str, content: str):
        """Appends active table text directly to the rolling queue buffer."""
        self.sliding_window_history.append(f"{speaker}: {content}")
        if len(self.sliding_window_history) > 150: # Limit sliding context
            self.sliding_window_history.pop(0)

    def commit_chronicle_update(self, session_id: str):
        """
        Fires automatically when a scene shifts or an encounter finishes.
        Forces Qwen to update player histories and NPC records.
        """
        if not self.sliding_window_history:
            return
        asyncio.create_task(self._async_commit_chronicle_update(session_id))

    async def _async_commit_chronicle_update(self, session_id: str):
        import httpx
        raw_transcript = "\n".join(self.sliding_window_history)
        self.sliding_window_history.clear()

        system_prompt = (
            "You are the Vox-Chronicle System. Analyze the provided tabletop transcript segment. "
            "Generate an updated entry for:\n"
            "1. The Party Quest Journal (key: 'PartyJournal')\n"
            "2. NPC Personal Memories (key: 'NPCMemories' - a dict of npcName -> short memory)\n"
            "3. Relevant NPC Relationship Trackers (key: 'NPCRelationships' - dict of npcName -> flag update)\n"
            "4. Visual Atmosphere (key: 'Atmosphere')\n"
            "5. Map Effects (key: 'Effect')\n"
            "6. Ambient soundscape (key: 'Presence').\n\n"
            "Output strictly as a valid JSON payload."
        )
        
        payload = {
            "model": "EVA-UNIT-01/EVA-Qwen2.5-7B-v0.1",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Session Transcript Data:\n{raw_transcript}"}
            ],
            "response_format": { "type": "json_object" }
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                response = await client.post(self.api_url, json=payload)
                response.raise_for_status()
                structured_log = response.json()['choices'][0]['message']['content']
                
                logger.info(f"Chronicle update committed for session {session_id}")
                
                await self._trigger_visual_update(structured_log)
                await self._trigger_sfx_update(structured_log)
                await self._flush_updates_to_foundry(session_id, structured_log)
                
            except Exception as e:
                logger.error(f"Failed to commit chronicle update: {e}")

    async def _trigger_visual_update(self, structured_log):
        """Parses the chronicle summary and enqueues visual tasks."""
        try:
            log_data = json.loads(structured_log)
            atmosphere = log_data.get("Atmosphere", "")
            map_effect = log_data.get("Effect", "")

            if atmosphere:
                await resource_manager.enqueue_task("image-gen", {
                    "prompt": f"(Cinematic landscape illustration, highly detailed, masterwork): {atmosphere}",
                    "negative_prompt": "tokens, grid, map, low quality, characters",
                    "steps": 4,
                    "target": "atmosphere",
                    "original_prompt": atmosphere
                })

            if map_effect:
                await resource_manager.enqueue_task("image-gen", {
                    "prompt": f"(Top-down view, semi-transparent atmospheric effect, isolated on black): {map_effect}",
                    "negative_prompt": "background, floor, grass, text",
                    "steps": 4,
                    "target": "effect",
                    "original_prompt": map_effect
                })
        except Exception as e:
            logger.error(f"Visual update trigger failed: {e}")

    async def _trigger_sfx_update(self, structured_log):
        """Parses the chronicle summary and enqueues SFX Task."""
        try:
            log_data = json.loads(structured_log)
            presence = log_data.get("Presence", log_data.get("Soundscape", ""))
            if presence:
                await resource_manager.enqueue_task("sfx-gen", {
                    "prompt": f"Ambience, loopable, dark fantasy mood: {presence}",
                    "duration_seconds": 15
                })
        except Exception as e:
            logger.error(f"SFX update trigger failed: {e}")

    async def _flush_updates_to_foundry(self, session_id, json_payload):
        """Pushes structured data updates back to Foundry VTT."""
        try:
            log_data = json.loads(json_payload)
            
            # 1. Update Quest Journal
            quest_update = log_data.get("PartyJournal", "")
            if quest_update:
                await push_to_foundry("journal", {"title": f"Chronicle: {session_id}", "content": quest_update})

            # 2. Update NPC Relationship Flags
            npc_rel_updates = log_data.get("NPCRelationships", {})
            if npc_rel_updates:
                # Assuming push_to_foundry handles dict for flags if type=npc-flags
                pass
                
            # 3. Update NPC Personal Memory Journals
            npc_mem_updates = log_data.get("NPCMemories", {})
            for npc_name, memory_text in npc_mem_updates.items():
                await push_to_foundry("journal", {
                    "title": f"{npc_name} Memory",
                    "content": memory_text,
                    "append": True
                })

            logger.info(f"Successfully flushed Chronicle updates to Foundry for {session_id}")
        except Exception as e:
            logger.error(f"Failed to flush updates to Foundry: {e}")
