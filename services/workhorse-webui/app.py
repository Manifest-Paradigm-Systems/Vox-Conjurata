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
    "vox-llm-core": "http://127.0.0.1:11435", # Direct mapping for the core wrapper
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

def project_for_container(name: str):
    """Return the registered project owning a container by name, or None."""
    for proj, cfg in PROJECT_REGISTRY.items():
        if name in cfg["containers"]:
            return proj
    return None

def assign_project(name: str, labels: dict) -> str:
    """Classify a container into a project tab group.

    Priority: project registry container lists (explicit), then the
    compose project label (vox-conjurata stack), then the vox-*/foundry
    name convention, finally "Other" for everything else.
    """
    reg = project_for_container(name)
    if reg:
        return reg
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
        for c in containers:
            names = c.get("Names")
            name = names[0] if isinstance(names, list) and len(names) > 0 else (names if isinstance(names, str) else "Unknown")
            labels = c.get("Labels") or {}
            enriched.append({
                "id": c.get("ID", "N/A")[:12],
                "name": name,
                "status": c.get("Status", "N/A"),
                "image": c.get("Image", "N/A"),
                "vram": vram_map.get(name, "N/A"),
                "stack": "vox-conjurata" if "vox-" in name or "foundry" in name else "External",
                "role": name.replace("vox-", "").replace("-", " ").title(),
                "project": assign_project(name, labels)
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
