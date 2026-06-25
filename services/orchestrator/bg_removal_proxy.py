"""
Background Removal Proxy — Proxies removal requests to vox-bg-remover service.
"""
import httpx
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import Response

logger = logging.getLogger("vox-bg-removal")

router = APIRouter()
BG_REMOVER_URL = "http://vox-bg-remover:8000"


@router.post("/api/v1/remove-background")
async def remove_background(image: UploadFile = File(...)):
    """Proxy to vox-bg-remover service."""
    try:
        input_bytes = await image.read()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{BG_REMOVER_URL}/remove-background",
                files={"image": (image.filename, input_bytes, image.content_type)},
            )
            resp.raise_for_status()
            return Response(content=resp.content, media_type="image/png")
    except Exception as e:
        logger.error(f"Background removal proxy failed: {e}")
        raise HTTPException(502, "Background removal failed")
