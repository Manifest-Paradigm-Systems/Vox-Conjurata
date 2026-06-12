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
    while True:
        try:
            if os.path.exists(vram_path):
                with open(vram_path, "r") as f:
                    used = int(f.read().strip())
                    vram_state["current_used"] = used
                    
                    # Record a spike if it's significantly higher than the last recorded peak
                    if not vram_state["peaks"] or used > vram_state["peaks"][-1]["value"] + 100 * 1024 * 1024:
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
    "vox-llm-llama": "http://127.0.0.1:11435", # Using the core wrapper since llama is not exposed
    "vox-vision-gen": "http://127.0.0.1:8003",
    "vox-audio-core": "http://127.0.0.1:8004",
    "vox-audio-generation-sfx": "http://127.0.0.1:8001",
    "vox-audio-generation-music": "http://127.0.0.1:8000",
}

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
            "vox-vision-gen": "12.00GB",
            "vox-audio-core": "4.20GB",
            "vox-audio-generation-sfx": "0.80GB",
            "vox-vision-reader": "4.80GB",
            "vox-llm-llama": "Shared/Auto",
        }
        
        enriched = []
        for c in containers:
            names = c.get("Names")
            name = names[0] if isinstance(names, list) and len(names) > 0 else (names if isinstance(names, str) else "Unknown")
            enriched.append({
                "id": c.get("ID", "N/A")[:12],
                "name": name,
                "status": c.get("Status", "N/A"),
                "image": c.get("Image", "N/A"),
                "vram": vram_map.get(name, "N/A"),
                "stack": "vox-conjurata" if "vox-" in name or "foundry" in name else "External",
                "role": name.replace("vox-", "").replace("-", " ").title()
            })
        return enriched
    except Exception as e:
        logger.exception("Error in get_containers")
        return JSONResponse(status_code=500, content={"error": str(e)})

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

@app.post("/api/query")
async def query_service(req: QueryRequest):
    url = SERVICES.get(req.service)
    if not url:
        raise HTTPException(status_code=404, detail="Service not found")
    
    endpoint = "/v1/chat/completions" if "llm" in req.service else "/generate"
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            # Construct payload based on service type
            if "llm" in req.service:
                payload = {
                    "messages": [{"role": "user", "content": req.prompt}],
                    "max_tokens": 100,
                    **req.params
                }
            else:
                payload = {"prompt": req.prompt, **req.params}
                
            resp = await client.post(f"{url}{endpoint}", json=payload)
            return resp.json() if resp.headers.get("content-type") == "application/json" else {"status": "success", "message": "Binary data received"}
        except Exception as e:
            logger.error(f"Query error: {repr(e)}")
            return JSONResponse(status_code=500, content={"error": repr(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
