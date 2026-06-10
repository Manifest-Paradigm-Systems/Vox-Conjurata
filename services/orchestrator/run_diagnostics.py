#!/usr/bin/env python3
"""
Vox Conjurata — Diagnostic & Self-Healing Engine
Verifies core logic, environment health, and performs automated repairs.
"""

import os
import sys
import json
import socket
import logging
import subprocess
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [DIAGNOSTIC] - %(levelname)s - %(message)s"
)
logger = logging.getLogger("vox-diagnostic")

PROJECT_ROOT = Path("/var/home/EvokeStudio/vox-conjurata")
ORCHESTRATOR_DIR = PROJECT_ROOT / "services" / "orchestrator"
VOICE_SEEDS_DIR = ORCHESTRATOR_DIR / "voice_seeds"
SETTINGS_DIR = ORCHESTRATOR_DIR / "settings"

def check_port(port: int, host="127.0.0.1"):
    """Check if a port is open."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.settimeout(0.5)
            s.connect((host, port))
            return True
        except:
            return False

def run_test_suite(test_file: str):
    """Run a specific pytest file and return results."""
    logger.info(f"Running test suite: {test_file}...")
    try:
        result = subprocess.run(
            ["pytest", test_file, "--tb=short"],
            cwd=ORCHESTRATOR_DIR,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            logger.info(f"✅ {test_file} PASSED.")
            return True, result.stdout
        else:
            logger.error(f"❌ {test_file} FAILED.")
            return False, result.stdout
    except Exception as e:
        logger.error(f"Failed to execute tests: {e}")
        return False, str(e)

def perform_self_healing():
    """Identify and fix environment issues."""
    healed = []
    
    # 1. Ensure required directories exist
    for d in [VOICE_SEEDS_DIR, SETTINGS_DIR, VOICE_SEEDS_DIR / "_palette"]:
        if not d.exists():
            logger.warning(f"Missing directory detected: {d}. Healing...")
            d.mkdir(parents=True, exist_ok=True)
            healed.append(f"Created directory: {d.name}")

    # 2. Check for stale registry
    reg_path = SETTINGS_DIR / "voice_registry.json"
    if reg_path.exists():
        try:
            with open(reg_path, "r") as f:
                json.load(f)
        except json.JSONDecodeError:
            logger.warning(f"Corrupt voice registry detected. Healing...")
            reg_path.write_text("{}")
            healed.append("Reset corrupt voice_registry.json")

    return healed

def main():
    print("=" * 60)
    print("      VOX CONJURATA — SYSTEM DIAGNOSTIC ENGINE")
    print("=" * 60)

    # --- Phase 1: Environment & Self-Healing ---
    logger.info("Phase 1: Environment Integrity Check...")
    healed_actions = perform_self_healing()
    if healed_actions:
        for action in healed_actions:
            print(f"  [HEALED] {action}")
    else:
        logger.info("  Environment is structurally sound.")

    # --- Phase 2: Internal Logic Tests ---
    logger.info("Phase 2: Logic & Feature Verification...")
    
    overall_success = True
    test_files = ["test_orchestrator_unit.py", "test_feature_matrix.py"]
    
    for tf in test_files:
        success, output = run_test_suite(tf)
        if not success:
            overall_success = False
            # Print failure summary
            print("-" * 40)
            print(f"SUMMARY OF FAILURES IN {tf}:")
            for line in output.splitlines():
                if "FAILED" in line or "AssertionError" in line:
                    print(f"  {line}")
            print("-" * 40)

    # --- Phase 3: Service Availability (Informational) ---
    logger.info("Phase 3: Network Service Discovery...")
    services = {
        "Orchestrator (Local)": 8080,
        "LLM Core (vLLM Proxy)": 8080, # Note: this might clash if checked on localhost
        "Audio Core (VoxCPM2)": 8000,
        "Vision Reader": 8000
    }
    
    # We check internal docker names if possible, but here we just list expectations
    # Since we are in the orchestrator env, we check what we can.

    print("\n" + "=" * 60)
    if overall_success:
        print("✅ ALL SYSTEMS NOMINAL: Vox Conjurata logic is consistent.")
    else:
        print("❌ SYSTEM ALERT: Logic inconsistencies detected. See summary above.")
    print("=" * 60)
    
    if not overall_success:
        sys.exit(1)

if __name__ == "__main__":
    main()
