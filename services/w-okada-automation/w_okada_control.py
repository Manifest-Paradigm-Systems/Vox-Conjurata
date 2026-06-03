#!/usr/bin/env python3
"""
Specification: W-Okada RVC Automation Layer for Vox Conjurata
Objective: Orchestration script to control W-Okada Realtime Voice Changer via REST API.
"""

import os
import sys
import json
import argparse
import logging
from typing import Dict, Any, Optional

# --- TASK A: Environment Sanitization ---
# Ensure execution blocks target the native RDNA4 architecture without emulation fallbacks
os.environ["HSA_OVERRIDE_GFX_VERSION"] = ""

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger("w-okada-control")

# --- CONFIGURATION ---
SERVER_IP = os.getenv("W_OKADA_IP", "127.0.0.1")
SERVER_PORT = os.getenv("W_OKADA_PORT", "18888")
BASE_URL = f"http://{SERVER_IP}:{SERVER_PORT}/api/voice-changer"

# Performance Settings
DEFAULT_SETTINGS = {
    "f0Detector": "rmvpe_onnx",
    "chunkSize": 112,
    "extraFrame": 4096,
    "rvc_quality": 0  # low-latency
}

# Voice Actor Mapping
ACTOR_PROFILES: Dict[str, Dict[str, Any]] = {
    "elminster": {"modelId": 1, "tran": -3},
    "goblin":    {"modelId": 2, "tran": +7},
    "strahd":    {"modelId": 3, "tran": 0}
}

# --- TASK B: REST Client Core Logic ---
class VoiceChangerClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        # Attempt to import httpx, fallback to requests if not available
        try:
            import httpx
            self.client_type = "httpx"
            self._client = httpx.Client(timeout=10.0)
        except ImportError:
            try:
                import requests
                self.client_type = "requests"
                self._client = requests.Session()
            except ImportError:
                logger.error("Neither 'httpx' nor 'requests' library found. Please install one.")
                sys.exit(1)

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint}"
        try:
            if self.client_type == "httpx":
                resp = self._client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
            else:
                resp = self._client.post(url, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            error_msg = {"status": "error", "message": str(e), "endpoint": endpoint}
            logger.error(json.dumps(error_msg))
            return error_msg

    def switch_actor(self, actor_id: str) -> bool:
        """Instantly changes voice model, pitch, and parameters for the given actor."""
        profile = ACTOR_PROFILES.get(actor_id.lower())
        if not profile:
            logger.error(json.dumps({"status": "error", "message": f"Actor profile '{actor_id}' not found."}))
            return False

        # Merge default performance settings with actor-specific profile
        payload = {**DEFAULT_SETTINGS, **profile}
        
        logger.info(f"Switching to actor: {actor_id} (Model: {payload['modelId']}, Pitch: {payload['tran']})")
        
        # W-Okada update settings endpoint
        result = self._post("update_settings", payload)
        
        if result.get("status") == "error":
            return False
        
        logger.info(f"Successfully switched to {actor_id}.")
        return True

# --- TASK C: CLI Execution Gateway ---
def main():
    parser = argparse.ArgumentParser(description="W-Okada RVC Automation Layer Control")
    parser.add_argument("--actor", type=str, required=True, help="Actor ID to switch to (e.g., elminster, goblin, strahd)")
    parser.add_argument("--ip", type=str, default=SERVER_IP, help=f"Server IP (default: {SERVER_IP})")
    parser.add_argument("--port", type=str, default=SERVER_PORT, help=f"Server Port (default: {SERVER_PORT})")
    
    args = parser.parse_args()

    client = VoiceChangerClient(f"http://{args.ip}:{args.port}/api/voice-changer")
    success = client.switch_actor(args.actor)
    
    if not success:
        sys.exit(1)

if __name__ == "__main__":
    main()
