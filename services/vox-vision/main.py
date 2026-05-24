from fastapi import FastAPI
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-vision")

app = FastAPI(title="vox-vision")

@app.get("/")
async def root():
    return {"service": "vox-vision", "status": "running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
