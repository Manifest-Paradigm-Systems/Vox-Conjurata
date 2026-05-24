from fastapi import FastAPI
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-voice")

app = FastAPI(title="vox-voice")

@app.get("/")
async def root():
    return {"service": "vox-voice", "status": "running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
