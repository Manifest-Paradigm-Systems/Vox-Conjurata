"""
vox-bg-remover — Background removal service using rembg (RMBG 1.4).
Takes an image, removes background, returns PNG with transparency.
"""
import io
import logging
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import Response
from rembg import remove, new_session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vox-bg-remover")

app = FastAPI(title="vox-bg-remover")
session = new_session("u2net")  # Load once at startup


@app.post("/remove-background")
async def remove_background(image: UploadFile = File(...)):
    """Remove background from uploaded image, return PNG with transparency."""
    try:
        input_bytes = await image.read()
        if not input_bytes:
            raise HTTPException(400, "Empty image")

        result_bytes = remove(input_bytes, session=session)

        return Response(content=result_bytes, media_type="image/png")
    except Exception as e:
        logger.error(f"Background removal failed: {e}")
        raise HTTPException(500, str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}
