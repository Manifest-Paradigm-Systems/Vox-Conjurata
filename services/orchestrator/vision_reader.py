import base64
import requests
import logging
import os

logger = logging.getLogger("vox-vision-reader")

class MonsterSightSystem:
    def __init__(self, api_url="http://vox-vision-reader:8000/v1/chat/completions"):
        self.api_url = api_url

    def look_at_battlemap(self, image_path: str):
        """
        Uses MiniCPM-V to analyze the current battlemap and provide spatial awareness.
        """
        if not os.path.exists(image_path):
            logger.error(f"Battlemap image not found at {image_path}")
            return None

        try:
            with open(image_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

            payload = {
                "model": "MiniCPM-V-2_6-Int4",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Analyze this tabletop battlemap. Identify player tokens, enemies, and key environmental hazards (grease, fire, pits). Describe their relative positions and any tactical advantages/disadvantages."},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}}
                        ]
                    }
                ],
                "max_tokens": 500
            }

            response = requests.post(self.api_url, json=payload, timeout=60)
            response.raise_for_status()
            analysis = response.json()['choices'][0]['message']['content']
            
            logger.info("Monster Sight: Battlemap analysis complete.")
            return analysis
        except Exception as e:
            logger.error(f"Monster Sight failed: {e}")
            return None
