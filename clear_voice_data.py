import os
import json
import shutil
import sys
from pathlib import Path

VOICE_SEEDS_DIR = Path("services/orchestrator/voice_seeds")
VOICE_REGISTRY_PATH = Path("services/orchestrator/settings/voice_registry.json")

def clear_voice_data(purge_palette=False):
    print("🧹 Clearing voice data...")
    
    # Safety Check
    if not purge_palette:
        print("🛡️ Safety: Palette foundations will be PRESERVED.")
    else:
        print("⚠️ WARNING: Palette foundations will be DELETED.")

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
                # ALWAYS PRESERVE NARRATOR SEEDS
                if item.name.startswith("narrator_seed_"):
                    print(f"✅ Preserving narrator seed: {item.name}")
                    continue
                item.unlink()
            elif item.is_dir() and item.name == "_palette":
                if not purge_palette:
                    print(f"✅ Preserving palette directory: {item}")
                else:
                    print(f"🗑️ Deleting palette directory: {item}")
                    shutil.rmtree(item)
    else:
        print("ℹ️ Seeds directory not found.")

    # Re-create directories to be safe
    VOICE_SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    (VOICE_SEEDS_DIR / "_palette").mkdir(parents=True, exist_ok=True)
    
    print("✅ Voice data cleanup complete.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--purge-palette", action="store_true")
    args = parser.parse_args()

    if not args.yes:
        confirm = input("Are you sure you want to clear the character voice registry? (y/N): ")
        if confirm.lower() != 'y':
            print("❌ Aborted.")
            sys.exit(0)
            
        if args.purge_palette:
            confirm_pal = input("‼️ REALLY delete foundations (Dragons, Elves, etc)? This will break ingestion until re-generated. (y/N): ")
            if confirm_pal.lower() != 'y':
                args.purge_palette = False
            
    clear_voice_data(purge_palette=args.purge_palette)
