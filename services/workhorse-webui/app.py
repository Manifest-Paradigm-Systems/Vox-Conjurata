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
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="Vox Workhorse WebUI")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
LORA_DIR = "/var/home/EvokeStudio/vox-conjurata/loras"
MODEL_DATA_DIR = "/var/home/EvokeStudio/.local/share/containers/storage/volumes/vox-conjurata_model_storage/_data"

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Service URLs from Orchestrator config logic
SERVICES = {
    "vox-llm-llama": "http://vox-llm-llama:8000",
    "vox-vision-gen": "http://vox-vision-gen:8003",
    "vox-audio-core": "http://vox-audio-core:8000",
    "vox-audio-generation-sfx": "http://vox-audio-generation-sfx:8000",
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
            return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)
