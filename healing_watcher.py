import httpx
import time
import json
import os
import subprocess
import glob
from pathlib import Path

# Telemetry and safety configurations
ORCHESTRATOR_URL = "http://localhost:8080/api/v1/diagnostics/latest"
VRAM_CRITICAL_GB = 30.0
POLL_INTERVAL_SECONDS = 5
INCIDENT_LOG_PATH = "/var/home/EvokeStudio/vox-conjurata/cache/gpu_incident.json"

def get_vram_used_gb() -> float:
    """Finds and reads active GPU VRAM utilization from Host Linux sysfs dynamically."""
    paths = glob.glob("/sys/class/drm/card*/device/mem_info_vram_used")
    for path in paths:
        try:
            with open(path, "r") as f:
                used_bytes = int(f.read().strip())
                return used_bytes / (1024 ** 3)
        except Exception:
            pass
    return 0.0

def check_orchestrator_errors():
    """Queries the orchestrator diagnostics endpoint and filters noisy telemetry.

    Only returns data for actual errors (type='error' or non-'nominal' status
    that is NOT a routine client-side telemetry ping).  Startup pings and
    browser console-error spam are suppressed.
    """
    try:
        response = httpx.get(ORCHESTRATOR_URL, timeout=2.0)
        if response.status_code == 200:
            data = response.json()
            status = data.get("status", "unknown")
            if status != "nominal":
                # Distinguish real errors from telemetry noise
                log_type = data.get("type", "")
                if log_type in ("startup", "console-error"):
                    return None  # suppress routine telemetry
                return data
    except Exception:
        pass
    return None

def check_container_logs() -> tuple[bool, str]:
    """Scans all active containers in the compose stack for crash keywords."""
    try:
        # Get active container names in the user's podman environment
        cmd = "podman ps --format '{{.Names}}'"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if res.returncode != 0:
            return False, ""
        
        containers = [c.strip() for c in res.stdout.split("\n") if c.strip()]
        crash_keywords = ["GPU Hang", "HW Exception", "Aborted (core dumped)", "Segmentation fault"]
        
        for container in containers:
            # We check logs from the last 10 seconds to avoid repeating older alerts
            log_cmd = f"podman logs --since 10s {container}"
            log_res = subprocess.run(log_cmd, shell=True, capture_output=True, text=True)
            
            # Combine stdout and stderr
            logs = log_res.stdout + log_res.stderr
            for kw in crash_keywords:
                if kw in logs:
                    return True, f"Found critical crash pattern '{kw}' in container '{container}' logs: {logs[-300:]}"
    except Exception as e:
        print(f"Error checking container logs: {e}")
    return False, ""

def trigger_emergency_shutdown(reason: str, vram_gb: float):
    """Stop only GPU-resident containers to free VRAM.

    Non-GPU services (cloudflared, Caddy, foundry-vtt, vox-llm-core) are
    left running — killing them doesn't free VRAM and interrupts unrelated
    infrastructure.
    """
    gpu_containers = [
        "vox-vision-gen",
        "vox-vision-reader",
        "vox-actor",
        "vox-voice",
        "vox-monster-fish",
        "vox-audio-generation",
    ]
    print(f"\n🚨 [SELF-HEALING DAEMON] EMERGENCY DETECTED: {reason}")
    print(f"Current VRAM Usage: {vram_gb:.2f} GB / 32.00 GB")

    # Save the incident details to cache
    incident_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reason": reason,
        "vram_used_gb": vram_gb,
    }

    try:
        os.makedirs(os.path.dirname(INCIDENT_LOG_PATH), exist_ok=True)
        with open(INCIDENT_LOG_PATH, "w") as f:
            json.dump(incident_data, f, indent=2)
        print(f"📝 Incident logged successfully to {INCIDENT_LOG_PATH}")
    except Exception as e:
        print(f"Could not save incident log: {e}")

    # Stop only GPU containers to release all VRAM without killing
    # unrelated infrastructure (tunnel, proxy, foundry).
    print("🧹 Stopping GPU container stack to protect system integrity...")
    for container in gpu_containers:
        subprocess.run(
            ["podman", "stop", container],
            capture_output=True, text=True,
        )
    print("✅ Emergency shutdown complete. GPU containers stopped and VRAM purged.")

def main():
    print("🧠 [SELF-HEALING WATCHER] Active. Monitoring VRAM, GPU state, and container logs...")
    
    while True:
        vram_gb = get_vram_used_gb()
        
        # 1. Protect against VRAM oversubscription exceeding physical card capabilities
        if vram_gb > VRAM_CRITICAL_GB:
            trigger_emergency_shutdown(f"VRAM usage exceeded safety threshold: {vram_gb:.2f} GB", vram_gb)
            break
            
        # 2. Monitor for active GPU Hangs/Exceptions in container stderr/stdout
        has_crash, crash_detail = check_container_logs()
        if has_crash:
            trigger_emergency_shutdown(f"GPU/Container crash detected: {crash_detail}", vram_gb)
            break
            
        # 3. Log warnings if client-side telemetry returns non-nominal status
        orch_err = check_orchestrator_errors()
        if orch_err:
            print(f"⚠️ [SELF-HEALING WATCHER] Orchestrator warning: {json.dumps(orch_err)}")
            
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
