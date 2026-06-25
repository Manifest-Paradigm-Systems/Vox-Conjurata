"""Proxy for AoN images — bypasses CORS restrictions."""
import httpx
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

logger = logging.getLogger("vox-aon-proxy")
router = APIRouter()
HEADERS = {"User-Agent": "VoxPDFImporter/1.0"}


@router.get("/api/v1/aon-image")
async def aon_image(url: str):
    """Fetch an image from Archives of Nethys and return it."""
    if not url.startswith("https://2e.aonprd.com/Images/"):
        raise HTTPException(400, "Invalid URL")
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
        resp = await client.get(url)
        if resp.status_code == 404:
            raise HTTPException(404)
        resp.raise_for_status()
    return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/webp"))
