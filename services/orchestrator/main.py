from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import os
import logging
import asyncio

# --- vox-conjurata Orchestrator Service ---
# Asynchronous FastAPI application for handling dialogue finalization

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - vox-conjurata - %(levelname)s - %(message)s"
)
logger = logging.getLogger("vox-conjurata")

app = FastAPI(title="vox-conjurata-orchestrator", version="1.0.0")

# Configuration from environment variables
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://vox-llm-core:11434")
FOUNDRY_API_URL = os.getenv("FOUNDRY_API_URL", "http://foundry-vtt:30000/api")
FOUNDRY_API_KEY = os.getenv("FOUNDRY_API_KEY", "")

class DialogueEndRequest(BaseModel):
    npcName: str
    transcript: str

async def generate_summary(transcript: str) -> str:
    """Asynchronously calls Ollama to generate a condensed markdown summary."""
    prompt = f"Summarize the following conversation transcript in a condensed markdown format:\n\n{transcript}"
    
    payload = {
        "model": "qwen:latest",
        "prompt": prompt,
        "stream": False
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(f"{OLLAMA_URL}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "Summary generation failed.")
        except Exception as e:
            logger.error(f"Error calling Ollama: {e}")
            return f"Error generating summary: {str(e)}"

async def log_to_foundry(npc_name: str, summary: str):
    """Fires a secure POST request to Foundry VTT REST API to trigger a macro."""
    payload = {
        "macroName": "LogNPCSession",
        "args": [{"npcName": npc_name, "summary": summary}]
    }
    
    headers = {
        "Authorization": f"Bearer {FOUNDRY_API_KEY}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{FOUNDRY_API_URL}/macros/execute", 
                json=payload, 
                headers=headers
            )
            response.raise_for_status()
            logger.info(f"Successfully logged session for {npc_name} to Foundry.")
        except Exception as e:
            logger.error(f"Error calling Foundry API: {e}")

@app.post("/api/v1/dialogue/end")
async def end_dialogue(request: DialogueEndRequest):
    """
    Endpoint triggered when a dialogue session ends.
    Orchestrates summary generation and Foundry logging.
    """
    logger.info(f"Received dialogue end request for NPC: {request.npcName}")
    
    # 1. Generate summary from Ollama
    summary = await generate_summary(request.transcript)
    
    # 2. Log to Foundry
    await log_to_foundry(request.npcName, summary)
    
    return {
        "status": "success",
        "npcName": request.npcName,
        "summary": summary
    }

# ==========================================
# DIAGNOSTICS BUFFER INTEGRATION
# ==========================================
from pydantic import BaseModel
from typing import List, Optional

error_buffer: List[dict] = []

class DiagnosticLog(BaseModel):
    type: str
    message: str
    source: Optional[str] = None
    lineno: Optional[int] = None
    error: Optional[str] = None

@app.post("/api/v1/diagnostics/logs")
async def receive_logs(log: DiagnosticLog):
    error_buffer.append(log.dict())
    if len(error_buffer) > 10:
        error_buffer.pop(0)
    return {"status": "cached"}

@app.get("/api/v1/diagnostics/latest")
async def get_latest_error():
    return error_buffer[-1] if error_buffer else {"status": "nominal"}
