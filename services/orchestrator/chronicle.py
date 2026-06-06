"""
Vox-Chronicle Automation Layer
File: chronicle.py
"""

import requests
import logging

logger = logging.getLogger("vox-chronicle")

class VoxChronicleSystem:
    def __init__(self, api_url="http://vox-llm-core:8000/v1/chat/completions"):
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

        raw_transcript = "\n".join(self.sliding_window_history)
        
        system_prompt = (
            "You are the Vox-Chronicle System. Analyze the provided tabletop transcript segment. "
            "Generate an updated entry for: 1. The Party Quest Journal, and 2. Relevant NPC Relationship Trackers. "
            "Output formatting must be strictly structured as a valid JSON payload."
        )
        
        payload = {
            "model": "Qwen2.5-7B-Instruct-GPTQ",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Session Transcript Data:\n{raw_transcript}"}
            ],
            "response_format": { "type": "json_object" }
        }
        
        try:
            response = requests.post(self.api_url, json=payload, timeout=60)
            response.raise_for_status()
            structured_log = response.json()['choices'][0]['message']['content']
            
            logger.info(f"Chronicle update committed for session {session_id}")
            # Dispatch updating hooks straight into the Foundry VTT database layer
            self._flush_updates_to_foundry(session_id, structured_log)
            
            # Flush the local queue to reset the window for the next scene
            self.sliding_window_history.clear()
        except Exception as e:
            logger.error(f"Failed to commit chronicle update: {e}")

    def _flush_updates_to_foundry(self, session_id, json_payload):
        # Database write code handles updates natively
        # For now, we log it.
        logger.info(f"Foundry Update Payload: {json_payload}")
        pass
