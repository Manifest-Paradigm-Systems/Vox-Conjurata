import os
import json
import shutil
from pathlib import Path

VOICE_SEEDS_DIR = Path("services/orchestrator/voice_seeds")
VOICE_REGISTRY_PATH = Path("services/orchestrator/settings/voice_registry.json")

def clear_voice_data():
    print("🧹 Clearing voice data...")
    
    # 1. Clear Registry
    if VOICE_REGISTRY_PATH.exists():
        print(f"🗑️ Deleting registry: {VOICE_REGISTRY_PATH}")
        VOICE_REGISTRY_PATH.unlink()
    else:
        print("ℹ️ Registry file not found.")

    # 2. Clear Seeds
    if VOICE_SEEDS_DIR.exists():
        print(f"🗑️ Cleaning seeds directory: {VOICE_SEEDS_DIR}")
        for item in VOICE_SEEDS_DIR.iterdir():
            if item.is_file():
                if item.name == ".gitkeep":
                    continue
                item.unlink()
            elif item.is_dir() and item.name == "_palette":
                print(f"🗑️ Deleting palette directory: {item}")
                shutil.rmtree(item)
    else:
        print("ℹ️ Seeds directory not found.")

    # Re-create directories to be safe
    VOICE_SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    (VOICE_SEEDS_DIR / "_palette").mkdir(parents=True, exist_ok=True)
    
    print("✅ Voice data cleared successfully.")

if __name__ == "__main__":
    clear_voice_data()
