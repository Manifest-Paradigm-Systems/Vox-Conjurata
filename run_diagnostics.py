#!/usr/bin/env python3
import os
import sys
import subprocess
import glob
import json
import socket
import urllib.request
from typing import Dict, List, Tuple, Optional

# Premium terminal formatting colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

def print_header(title: str):
    print(f"\n{BOLD}{CYAN}=== {title} ==={RESET}")

def get_vram_telemetry() -> Tuple[float, float]:
    """Finds and reads active GPU VRAM utilization from Host Linux sysfs dynamically."""
    paths_used = glob.glob("/sys/class/drm/card*/device/mem_info_vram_used")
    paths_total = glob.glob("/sys/class/drm/card*/device/mem_info_vram_total")
    
    used_gb = 0.0
    total_gb = 32.0  # Default fallback representation
    
    for path in paths_used:
        try:
            with open(path, "r") as f:
                used_bytes = int(f.read().strip())
                used_gb = used_bytes / (1024 ** 3)
                break
        except Exception:
            pass
            
    for path in paths_total:
        try:
            with open(path, "r") as f:
                total_bytes = int(f.read().strip())
                total_gb = total_bytes / (1024 ** 3)
                break
        except Exception:
            pass
            
    return used_gb, total_gb

def check_port(host: str, port: int, timeout: float = 1.0) -> bool:
    """Checks if a TCP port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError):
        return False

def analyze_logs(container_name: str, tail_lines: int = 150) -> List[str]:
    """Retrieves logs from a container and looks for known crash patterns."""
    try:
        cmd = f"podman logs --tail {tail_lines} {container_name}"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5.0)
        logs = res.stdout + res.stderr
        
        issues = []
        
        # 1. Hugging Face Gated Repo issues
        if "gated repo" in logs.lower() or "gatedrepoerror" in logs.lower() or "access to model" in logs.lower():
            if "stable-audio-3" in logs:
                issues.append("gated_stable_audio")
            else:
                issues.append("gated_hf_repo")
        
        # 2. PyTorch/HIP/ROCm errors
        if "hipError" in logs or "HIP error" in logs or "device-side assert" in logs:
            issues.append("rocm_driver_error")
        if "GPU Hang" in logs or "HW Exception" in logs:
            issues.append("gpu_hang")
            
        # 3. vLLM Cache/VRAM errors
        if "No available memory for cache blocks" in logs or "ValueError: No available memory" in logs:
            issues.append("vllm_cache_allocation_failure")
            
        # 4. Out Of Memory
        if "Out of memory" in logs or "CUDA out of memory" in logs or "RuntimeError: CUDA out of memory" in logs:
            issues.append("oom")
            
        # 5. Connection refused (dependent services not ready)
        if "ConnectionRefusedError" in logs or "Failed to establish a new connection" in logs:
            issues.append("service_connection_refused")
            
        return issues
    except Exception as e:
        return [f"log_read_error: {str(e)}"]

def main():
    print(f"\n{BOLD}{GREEN}🔍 AUTOMATED SYSTEM DIAGNOSTICS DEPLOYED{RESET}")
    print("=" * 60)
    
    # --- 1. HOST TELEMETRY ---
    print_header("Host Telemetry")
    used_vram, total_vram = get_vram_telemetry()
    vram_status = GREEN if used_vram < 26.5 else (YELLOW if used_vram < 30.0 else RED)
    print(f"• Active VRAM: {vram_status}{used_vram:.2f} GB / {total_vram:.2f} GB{RESET}")
    
    # Check GPU info
    try:
        gpu_res = subprocess.run("lspci | grep -i -E 'vga|3d|display'", shell=True, capture_output=True, text=True)
        gpus = [g.strip() for g in gpu_res.stdout.split("\n") if g.strip()]
        for gpu in gpus:
            print(f"• GPU Device: {BOLD}{gpu}{RESET}")
    except Exception:
        print("• GPU Device: Could not query via lspci")

    # --- 2. CONTAINER STACK STATUS ---
    print_header("Container Stack Status (Podman)")
    
    try:
        cmd = "podman ps -a --format '{{.Names}}|{{.Status}}'"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        containers = [c.strip().split("|") for c in res.stdout.split("\n") if c.strip()]
    except Exception as e:
        print(f"{RED}❌ Failed to query Podman: {e}{RESET}")
        sys.exit(1)
        
    ports_map = {
        "vox-conjurata-orchestrator": 8080,
        "vox-llm-core": 11435,
        "vox-vision-reader": 8002,
        "vox-vision": 7860,
        "vox-voice": 5000,
        "vox-audio-generation-music": 8000,
        "vox-audio-generation-sfx": 8001,
        "vox-actor": 5020,
        "vox-monster-fish": 5030
    }
    
    container_issues = {}
    
    for c_info in containers:
        if len(c_info) != 2:
            continue
        name, status = c_info
        is_running = "Up" in status
        
        status_color = GREEN if is_running else RED
        print(f"• Container {BOLD}{name:<30}{RESET} -> Status: {status_color}{status}{RESET}", end="")
        
        # Check port if mapped
        if name in ports_map:
            port = ports_map[name]
            port_open = check_port("localhost", port)
            port_status = f"{GREEN}Port {port} OPEN{RESET}" if port_open else f"{RED}Port {port} CLOSED{RESET}"
            print(f" | {port_status}")
            
            if is_running and not port_open:
                # Running but port closed might mean it is still initializing
                container_issues[name] = ["initializing_or_stuck_port"]
        else:
            print("")
            
        if not is_running or (name in ports_map and not check_port("localhost", ports_map[name])):
            # Scan logs for issues
            logs_issues = analyze_logs(name)
            if logs_issues:
                if name not in container_issues:
                    container_issues[name] = []
                container_issues[name].extend(logs_issues)

    # --- 3. ORCHESTRATOR DIAGNOSTICS API ---
    print_header("Orchestrator Internal Telemetry")
    try:
        req = urllib.request.Request("http://localhost:8080/api/v1/diagnostics/latest")
        with urllib.request.urlopen(req, timeout=2.0) as response:
            data = json.loads(response.read().decode())
            status = data.get("status", "unknown")
            status_color = GREEN if status == "nominal" else RED
            print(f"• Diagnostic Status: {status_color}{status.upper()}{RESET}")
            if status != "nominal":
                print(f"  Details: {data}")
    except Exception as e:
        print(f"• Orchestrator API: {RED}UNREACHABLE ({e}){RESET}")

    # --- 4. CAUSE OF ERRORS & REMEDIATION REPORT ---
    print_header("Diagnosis & Remediation Report")
    
    if not container_issues:
        print(f"{GREEN}✅ No critical service issues detected. All active endpoints are nominal.{RESET}")
    else:
        for name, issues in container_issues.items():
            print(f"\n🚨 {BOLD}{RED}Service: {name}{RESET}")
            for issue in set(issues):
                if issue == "gated_stable_audio":
                    print(f"  {YELLOW}Cause:{RESET} Hugging Face Gated Repo Access Violation.")
                    print(f"  {BOLD}Remediation:{RESET} Access to the Stable Audio 3 model is restricted.")
                    print("  1. Go to https://huggingface.co/stabilityai/stable-audio-3-small-sfx")
                    print("     and https://huggingface.co/stabilityai/stable-audio-3-small-music")
                    print("     using the Hugging Face account associated with your token and accept the terms.")
                    print("  2. If already done, verify the HF_TOKEN in ~/vox-conjurata/.env is valid and authorized.")
                elif issue == "gated_hf_repo":
                    print(f"  {YELLOW}Cause:{RESET} Unauthorized access to a gated Hugging Face repository.")
                    print(f"  {BOLD}Remediation:{RESET} Ensure the HF_TOKEN in your .env has access to all configured weights.")
                elif issue == "rocm_driver_error" or issue == "gpu_hang":
                    print(f"  {YELLOW}Cause:{RESET} GPU Hang or HIP Driver assertion failure.")
                    print(f"  {BOLD}Remediation:{RESET} The ROCm driver crashed. Run 'sudo dmesg | grep -i amdgpu' to inspect.")
                    print("  Restart the container stack via 'podman compose down && podman compose up -d' to reset the HIP runtime state.")
                elif issue == "vllm_cache_allocation_failure":
                    print(f"  {YELLOW}Cause:{RESET} vLLM memory allocation error: No available memory for cache blocks.")
                    print(f"  {BOLD}Remediation:{RESET} The '--gpu-memory-utilization' is set too low for this model length.")
                    print("  Adjust the vLLM memory utilization parameter in compose.yaml to a slightly higher percentage (e.g. 0.35 or 0.40) or reduce max-model-len.")
                elif issue == "oom":
                    print(f"  {YELLOW}Cause:{RESET} Out Of Memory (OOM) event on host or container.")
                    print(f"  {BOLD}Remediation:{RESET} Lower VRAM/memory limit configurations in compose.yaml or stop other system services.")
                elif issue == "initializing_or_stuck_port":
                    # Let's inspect logs for compilation
                    try:
                        log_check = subprocess.run(f"podman logs --tail 50 {name}", shell=True, capture_output=True, text=True)
                        if "cmake" in log_check.stdout or "g++" in log_check.stdout or "building" in log_check.stdout.lower():
                            print(f"  {YELLOW}Status:{RESET} Currently compiling/building components from source inside the container.")
                            print(f"  {BOLD}Details:{RESET} This occurs on first startup when setting GFX overrides on certain images. Let it finish compiling.")
                        else:
                            print(f"  {YELLOW}Status:{RESET} Service is up but its API port is not responding.")
                            print(f"  {BOLD}Details:{RESET} The container is running but the web server has not bound to the port. Check 'podman logs {name}' for progress.")
                    except Exception:
                        pass
                else:
                    print(f"  {YELLOW}Issue detected:{RESET} {issue}")
                    print(f"  Check 'podman logs {name}' for raw stack traces.")

    print("\n" + "=" * 60 + "\n")

if __name__ == "__main__":
    main()
