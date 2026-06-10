import httpx
import logging
import time
import os
from typing import Any, Optional

logger = logging.getLogger("vox-foundry-client")

FOUNDRY_API_URL = os.getenv("FOUNDRY_API_URL", "http://foundry-vtt:30000/api")
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY", "")

async def push_to_foundry(update_type: str, content: Any, original_prompt: str = "", filename: str = "") -> bool:
    """Pushes a generated asset (image or data) to the Foundry VTT REST API."""
    if not FOUNDRY_API_KEY:
        logger.warning(f"⚠️ Skipping push to Foundry ({update_type}): No API Key.")
        return False

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {"Authorization": f"Bearer {FOUNDRY_API_KEY}"}
            
            if update_type in ["atmosphere", "effect"]:
                # Content is image bytes
                if not filename:
                    filename = f"vox_{update_type}_{int(time.time())}.png"
                files = {"file": (filename, content, "image/png")}
                resp = await client.post(f"{FOUNDRY_API_URL}/vox/display-image", 
                             data={"type": update_type, "prompt": original_prompt},
                             files=files, headers=headers)
                return resp.status_code == 200
            
            elif update_type == "vision-contract":
                # Content is a dict (walls, lights, etc)
                resp = await client.post(f"{FOUNDRY_API_URL}/vox/apply-scan", 
                             json=content, headers=headers)
                return resp.status_code == 200
                
            return False
    except Exception as e:
        logger.error(f"Push to Foundry failed ({update_type}): {e}")
        return False

async def log_to_foundry(npc_name: str, summary: str) -> bool:
    """Fires a secure POST request to Foundry VTT REST API to trigger a macro."""
    if not FOUNDRY_API_KEY: return False
    try:
        payload = {
            "macroName": "LogNPCSession",
            "args": [{"npcName": npc_name, "summary": summary}]
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{FOUNDRY_API_URL}/vox/macro", 
                json=payload,
                headers={"Authorization": f"Bearer {FOUNDRY_API_KEY}"}
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Failed to log to Foundry: {e}")
        return False
