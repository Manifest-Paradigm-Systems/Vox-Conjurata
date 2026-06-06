import base64
import requests
import logging
import os

logger = logging.getLogger("vox-vision-reader")

class MonsterSightSystem:
    def __init__(self, api_url="http://vox-vision-reader:8000/v1/chat/completions"):
        self.api_url = api_url

    def look_at_battlemap(self, image_path: str, context: str = ""):
        """
        Uses MiniCPM-V to analyze the current battlemap and provide spatial awareness.
        Context includes recent dialogue to help the model 'focus' on what the DM is describing.
        """
        if not os.path.exists(image_path):
            logger.error(f"Battlemap image not found at {image_path}")
            return None

        try:
            with open(image_path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

            # Craft a prompt that combines visual state with narrative intent
            prompt = (
                "You are the 'Monster Sight' tactical AI. Analyze this tabletop battlemap.\n"
                "CONTEXT: The DM just said: '" + context + "'\n\n"
                "TASK:\n"
                "1. Identify player and enemy positions.\n"
                "2. Based on the CONTEXT, identify which units are targeted or 'hit' by spells/effects.\n"
                "3. Calculate approximate spatial ranges (e.g., 'The Fighter is within 15ft of the blast').\n"
                "4. Identify environmental hazards and tactical advantages.\n\n"
                "Respond with a concise tactical report for the DM."
            )

            payload = {
                "model": "MiniCPM-V-2_6-Int4",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
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
