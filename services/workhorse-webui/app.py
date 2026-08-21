from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import psutil
import subprocess
import os
import json
import httpx
import asyncio
import logging
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("workhorse-webui")

app = FastAPI(title="Vox Workhorse WebUI")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
LORA_DIR = "/var/home/EvokeStudio/vox-conjurata/loras"
MODEL_DATA_DIR = "/var/home/EvokeStudio/.local/share/containers/storage/volumes/vox-conjurata_model_storage/_data"

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# VRAM Telemetry State
vram_state = {
    "current_used": 0,
    "total": 34208743424, # 32GB approx
    "peaks": [] # List of {"timestamp": "...", "value": 123}
}

async def track_vram_spikes():
    """Background task to poll VRAM and record spikes."""
    global vram_state
    vram_path = "/sys/class/drm/card1/device/mem_info_vram_used"
    oom_threshold = 0.92 * vram_state["total"]  # 92% of 32GB is ~29.4GB
    
    while True:
        try:
            if os.path.exists(vram_path):
                with open(vram_path, "r") as f:
                    used = int(f.read().strip())
                    vram_state["current_used"] = used
                    
                    # OOM Safety Protocol
                    if used > oom_threshold:
                        logger.warning(f"CRITICAL VRAM SPIKE DETECTED ({used / (1024**3):.2f} GB). INITIATING EMERGENCY SHUTDOWN OF HEAVY CONTAINERS.")
                        subprocess.run(["podman", "stop", "vox-audio-generation-music", "vox-vision-gen"], capture_output=True)
                        
                        # Add a distinct marker to the UI logs
                        vram_state["peaks"].append({
                            "timestamp": datetime.now().strftime("%H:%M:%S") + " (OOM SHUTDOWN)",
                            "value": used
                        })
                    
                    # Record a spike if it's significantly higher than the last recorded peak
                    elif not vram_state["peaks"] or used > vram_state["peaks"][-1]["value"] + 100 * 1024 * 1024:
                         vram_state["peaks"].append({
                            "timestamp": datetime.now().strftime("%H:%M:%S"),
                            "value": used
                        })
                    
                    # Keep only last 10 peaks
                    vram_state["peaks"] = sorted(vram_state["peaks"], key=lambda x: x["value"], reverse=True)[:10]
        except Exception as e:
            pass
        await asyncio.sleep(2)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(track_vram_spikes())

@app.get("/api/telemetry")
async def get_telemetry():
    return vram_state

# Service URLs from Orchestrator config logic
SERVICES = {
    "vox-llm-llama": "http://127.0.0.1:11435", # Using the core wrapper
    "vox-llm-core": "http://127.0.0.1:11435", # Legacy alias (pre-rename)
    "vox-llm-openrouter": "http://127.0.0.1:11435", # Direct mapping for the core wrapper
    "vox-vision-gen": "http://127.0.0.1:8003",
    "vox-audio-core": "http://127.0.0.1:8004",
    "vox-audio-generation-sfx": "http://127.0.0.1:8001",
    "vox-audio-generation-music": "http://127.0.0.1:8000",
}

# Project classification for container tabs (Workhorse UI v3)
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"

def load_project_registry():
    """Load the per-repo project registry (containers + AI stack) from projects.json."""
    path = os.path.join(BASE_DIR, "projects.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        data = json.load(f)
    registry = {}
    for name, cfg in (data or {}).items():
        registry[name] = {
            "repo": cfg.get("repo", ""),
            "ai": cfg.get("ai", ""),
            "containers": list(cfg.get("containers") or []),
        }
    return registry

PROJECT_REGISTRY = load_project_registry()

def projects_for_container(name: str):
    """Return ALL registered projects that own a container (multi-project support)."""
    matches = []
    for proj, cfg in PROJECT_REGISTRY.items():
        if name in cfg["containers"]:
            matches.append(proj)
    return matches

def assign_project(name: str, labels: dict) -> str:
    """Classify a container into project tab groups (comma-separated for multi-project).

    Priority: project registry container lists (explicit), then the
    compose project label (vox-conjurata stack), then the vox-*/foundry
    name convention, finally "Other" for everything else.
    """
    reg = projects_for_container(name)
    if reg:
        return ",".join(reg)
    proj = (labels or {}).get(COMPOSE_PROJECT_LABEL)
    if proj:
        return proj
    if name.startswith("vox-") or "foundry" in name:
        return "vox-conjurata"
    return "Other"

class QueryRequest(BaseModel):
    service: str
    prompt: str
    params: Optional[dict] = {}

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/containers")
async def get_containers():
    try:
        # Get container stats from Podman
        cmd = ["podman", "ps", "-a", "--format", "json"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Podman error: {result.stderr}")
            return JSONResponse(status_code=500, content={"error": result.stderr})
        
        containers = json.loads(result.stdout)
        
        # Enrich with VRAM info
        vram_map = {
            "vox-vision-gen": "5.50GB",
            "vox-audio-core": "4.20GB",
            "vox-audio-generation-sfx": "0.80GB",
            "vox-vision-reader": "4.80GB",
            "vox-llm-llama": "Shared/Auto",
        }
        
        enriched = []
        stats = fetch_container_stats()

        names = []
        for c in containers:
            ns = c.get("Names")
            names.append(ns[0] if isinstance(ns, list) and len(ns) > 0 else (ns if isinstance(ns, str) else "Unknown"))
        details = fetch_container_details(names)
        peaks = fetch_cgroup_peaks(details)

        for c in containers:
            ns = c.get("Names")
            name = ns[0] if isinstance(ns, list) and len(ns) > 0 else (ns if isinstance(ns, str) else "Unknown")
            labels = c.get("Labels") or {}
            s = stats.get(name, {})
            d = details.get(name, {})
            enriched.append({
                "id": (c.get("Id") or "N/A")[:12],
                "name": name,
                "status": c.get("Status", "N/A"),
                "image": c.get("Image", "N/A"),
                "vram": vram_map.get(name, "N/A"),
                "stack": "vox-conjurata" if "vox-" in name or "foundry" in name else "External",
                "role": name.replace("vox-", "").replace("-", " ").title(),
                "project": assign_project(name, labels),
                "cpu_percent": s.get("cpu_percent"),
                "mem_usage": s.get("mem_usage"),
                "mem_gb": s.get("mem_gb"),
                "mem_percent": s.get("mem_percent"),
                "pids": s.get("pids"),
                "peak_mem_gb": peaks.get(name),
                "settings": {
                    "cpu_cores": d.get("cpu_cores", 0),
                    "mem_limit_gb": d.get("mem_limit_gb", 0),
                    "restart_policy": d.get("restart_policy", "no"),
                    "pids_limit": d.get("pids_limit", 0),
                },
            })
        return enriched
    except Exception as e:
        logger.exception("Error in get_containers")
        return JSONResponse(status_code=500, content={"error": str(e)})

C3PO_MUTE_FILE = os.path.expanduser("~/.config/vox/c3po-muted")

@app.get("/api/c3po-voice")
async def c3po_voice_state():
    """C-3PO voice mute switch state (file existence = muted)."""
    return {"muted": os.path.exists(C3PO_MUTE_FILE)}

@app.post("/api/c3po-voice")
async def set_c3po_voice(state: dict):
    """Set C-3PO voice mute switch: POST {"muted": true|false}."""
    muted = bool(state.get("muted", False))
    if muted:
        os.makedirs(os.path.dirname(C3PO_MUTE_FILE), exist_ok=True)
        open(C3PO_MUTE_FILE, "a").close()
    else:
        try:
            os.remove(C3PO_MUTE_FILE)
        except FileNotFoundError:
            pass
    return {"muted": muted}

@app.get("/api/models")
async def list_models():
    """Lists downloaded models and LORAs."""
    models = []
    if os.path.exists(MODEL_DATA_DIR):
        for f in os.listdir(MODEL_DATA_DIR):
            if f.endswith(".gguf") or f.endswith(".safetensors"):
                models.append({"name": f, "type": "base"})
    
    loras = []
    if os.path.exists(LORA_DIR):
        for f in os.listdir(LORA_DIR):
            if f.endswith(".safetensors") or f.endswith(".bin"):
                loras.append({"name": f, "type": "lora"})
                
    return {"models": models, "loras": loras}

@app.get("/api/projects")
async def list_projects():
    """Per-repo project registry with live per-container state."""
    try:
        out = subprocess.run(["podman", "ps", "-a", "--format", "json"], capture_output=True, text=True)
        states = {}
        if out.returncode == 0:
            for c in json.loads(out.stdout):
                names = c.get("Names")
                name = names[0] if isinstance(names, list) and len(names) > 0 else (names if isinstance(names, str) else "Unknown")
                states[name] = c.get("Status", "N/A")

        projects = []
        for name, cfg in PROJECT_REGISTRY.items():
            containers = [{"name": cn, "status": states.get(cn, "missing")} for cn in cfg["containers"]]
            projects.append({
                "name": name,
                "repo": cfg["repo"],
                "ai": cfg["ai"],
                "containers": containers,
                "all_up": bool(containers) and all("Up" in (states.get(cn, "") or "") for cn in cfg["containers"]),
            })
        return {"projects": projects}
    except Exception as e:
        logger.exception("Error in list_projects")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/project/{project_name}/{action}")
async def bulk_container_action(project_name: str, action: str):
    """Start or stop every container registered to a project (bulk power)."""
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="Action must be 'start' or 'stop'")
    cfg = PROJECT_REGISTRY.get(project_name)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Unknown project '{project_name}'")

    results = []
    for name in cfg["containers"]:
        r = subprocess.run(["podman", action, name], capture_output=True, text=True)
        results.append({
            "name": name,
            "ok": r.returncode == 0,
            "detail": (r.stderr or r.stdout or "").strip(),
        })
    return {
        "project": project_name,
        "action": action,
        "results": results,
        "ok": sum(1 for x in results if x["ok"]),
        "total": len(results),
    }

VALID_RESTART_POLICIES = {"no", "on-failure", "always", "unless-stopped", "never"}

class SettingsRequest(BaseModel):
    cpu_cores: Optional[float] = None
    mem_limit_gb: Optional[float] = None
    restart_policy: Optional[str] = None
    pids_limit: Optional[int] = None

@app.post("/api/container/{name}/settings")
async def update_container_settings(name: str, req: SettingsRequest):
    """Apply resource limits to a container via podman update.

    Values of 0 mean 'unlimited' for CPU/memory limits.
    """
    flags = []
    if req.cpu_cores is not None:
        if not (0 <= req.cpu_cores <= 128):
            raise HTTPException(status_code=400, detail="cpu_cores must be between 0 and 128")
        flags += ["--cpus", f"{req.cpu_cores}"]
    if req.mem_limit_gb is not None:
        if not (0 <= req.mem_limit_gb <= 512):
            raise HTTPException(status_code=400, detail="mem_limit_gb must be between 0 and 512")
        flags += ["--memory", f"{req.mem_limit_gb}g"]
    if req.restart_policy is not None:
        if req.restart_policy not in VALID_RESTART_POLICIES:
            raise HTTPException(status_code=400, detail=f"restart_policy must be one of {sorted(VALID_RESTART_POLICIES)}")
        flags += ["--restart", req.restart_policy]
    if req.pids_limit is not None:
        if req.pids_limit < -1:
            raise HTTPException(status_code=400, detail="pids_limit must be >= -1 (or 0 for unlimited)")
        flags += ["--pids-limit", f"{req.pids_limit}"]
    if not flags:
        raise HTTPException(status_code=400, detail="no settings provided")

    try:
        r = subprocess.run(["podman", "update", *flags, name], capture_output=True, text=True)
        if r.returncode != 0:
            return JSONResponse(status_code=500, content={"error": r.stderr.strip()})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": repr(e)})

    d = fetch_container_details([name]).get(name, {})
    settings = {k: d.get(k) for k in ("cpu_cores", "mem_limit_gb", "restart_policy", "pids_limit")}
    return {"name": name, "settings": settings, "message": f"Settings applied to {name}"}

@app.post("/api/container/{name}/{action}")
async def manage_container(name: str, action: str):
    if action not in ["start", "stop", "restart"]:
        raise HTTPException(status_code=400, detail="Invalid action")
    
    try:
        # Podman actions for start/stop/restart
        cmd = ["podman", action, name]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return JSONResponse(status_code=500, content={"error": result.stderr})
        return {"status": "success", "message": f"Container {name} {action}ed"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

import base64
import re

_MEM_UNITS = {"B": 1e-9, "KB": 1e-6, "MB": 1e-3, "GB": 1.0, "TB": 1e3}

def parse_mem_usage(s):
    """Parse podman stats mem_usage strings like '9.866GB / 64.53GB' or
    '415.8MB / 64.53GB' into GB as a float (or None)."""
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?B)", s or "")
    if not m:
        return None
    return round(float(m.group(1)) * _MEM_UNITS.get(m.group(2), 1.0), 2)

def fetch_container_details(names):
    """One podman inspect call for all containers: resource limits + pid."""
    if not names:
        return {}
    try:
        r = subprocess.run(["podman", "inspect"] + list(names), capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            logger.error(f"Podman inspect error: {r.stderr[:200]}")
            return {}
        details = {}
        for d in json.loads(r.stdout):
            name = (d.get("Name") or "").lstrip("/")
            hc = d.get("HostConfig") or {}
            state = d.get("State") or {}
            details[name] = {
                "cpu_cores": round((hc.get("NanoCpus") or 0) / 1e9, 2),
                "mem_limit_gb": round((hc.get("Memory") or 0) / (1024 ** 3), 2),
                "restart_policy": (hc.get("RestartPolicy") or {}).get("Name", "no"),
                "pids_limit": hc.get("PidsLimit") or 0,
                "pid": state.get("Pid") or 0,
            }
        return details
    except Exception as e:
        logger.warning(f"fetch_container_details failed: {repr(e)}")
        return {}

def fetch_cgroup_peaks(details):
    """RAM high-watermark per running container via cgroup v2 memory.peak."""
    peaks = {}
    for name, d in details.items():
        pid = d.get("pid") or 0
        if pid <= 0:
            continue
        try:
            with open(f"/proc/{pid}/cgroup", "r") as f:
                cgp = f.read().split(":")[-1].strip()
            with open(f"/sys/fs/cgroup{cgp}/memory.peak", "r") as f:
                peaks[name] = round(int(f.read().strip()) / (1024 ** 3), 2)
        except Exception:
            continue
    return peaks

def fetch_container_stats():
    """podman stats --no-stream: name -> {cpu_percent, mem_usage, mem_gb,
    mem_percent, pids}. Returns {} on any failure."""
    try:
        r = subprocess.run(
            ["podman", "stats", "--no-stream", "--format", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            logger.error(f"Podman stats error: {r.stderr}")
            return {}
        stats = {}
        for s in json.loads(r.stdout):
            name = s.get("name")
            if not name:
                continue
            stats[name] = {
                "cpu_percent": s.get("cpu_percent"),
                "mem_usage": s.get("mem_usage"),
                "mem_gb": parse_mem_usage(s.get("mem_usage")),
                "mem_percent": s.get("mem_percent"),
                "pids": s.get("pids"),
            }
        return stats
    except Exception as e:
        logger.warning(f"fetch_container_stats failed: {repr(e)}")
        return {}

@app.post("/api/query")
async def query_service(req: QueryRequest):
    url = SERVICES.get(req.service)
    if not url:
        raise HTTPException(status_code=404, detail="Service not found")
    
    endpoint = "/v1/chat/completions" if "llm" in req.service else "/generate"
    
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            # Construct payload based on service type
            if "llm" in req.service:
                payload = {
                    "messages": [{"role": "user", "content": req.prompt}],
                    "max_tokens": 100,
                    **req.params
                }
            elif "audio-core" in req.service:
                # Mock a request for vox-audio-core since it expects specific fields
                payload = {"npc_id": "test", "dialogue_text": req.prompt, "dsp_presets": {}}
            else:
                payload = {"prompt": req.prompt, **req.params}
                
            resp = await client.post(f"{url}{endpoint}", json=payload)
            
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                return resp.json()
            elif "audio" in content_type:
                b64_audio = base64.b64encode(resp.content).decode('utf-8')
                return {"status": "media", "type": "audio", "mime": content_type, "data": b64_audio}
            elif "image" in content_type:
                b64_image = base64.b64encode(resp.content).decode('utf-8')
                return {"status": "media", "type": "image", "mime": content_type, "data": b64_image}
            else:
                return {"status": "success", "message": f"Binary data received: {content_type}"}
                
        except Exception as e:
            logger.error(f"Query error: {repr(e)}")
            return JSONResponse(status_code=500, content={"error": repr(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
