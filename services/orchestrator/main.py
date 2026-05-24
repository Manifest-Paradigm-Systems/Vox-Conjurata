from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import os

app = FastAPI(title="vox-conjurata-orchestrator")

# Enable CORS so Foundry running in your browser can talk directly to this local backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class DialogueRequest(BaseModel):
    npcName: str
    transcript: str

# Points directly to your neighboring Ollama container service inside the Docker network
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://vox-llm-core:11434")

@app.post("/api/v1/dialogue/summary")
async def generate_summary(payload: DialogueRequest):
    # Craft the clean context instruction for Qwen
    prompt = (
        f"Summarize the following conversation with the NPC {payload.npcName} "
        f"in a brief, narrative style suitable for an RPG campaign memory log:\n\n"
        f"{payload.transcript}"
    )
    
    async with httpx.AsyncClient() as client:
        try:
            # Fire the request over to your local Qwen container core
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": "qwen", "prompt": prompt, "stream": False},
                timeout=60.0
            )
            response.raise_for_status()
            result = response.json()
            
            # Extract and return the clean text payload back to the Foundry browser client
            summary_text = result.get("response", "No summary generated.")
            return {"summary": summary_text}
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ollama container connection error: {str(e)}")