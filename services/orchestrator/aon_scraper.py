"""
AoN Scraper — Scrapes Archives of Nethys for PF2e monster art.
"""
import httpx
import logging
import re
import asyncio
from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger("vox-aon-scraper")
router = APIRouter()
AON_BASE = "https://2e.aonprd.com"
MONSTER_LIST = f"{AON_BASE}/Monsters.aspx"
HEADERS = {"User-Agent": "VoxPDFImporter/1.0"}


class AonScanRequest(BaseModel):
    letters: str = "A"
    max_monsters: int = 50


class MonsterEntry(BaseModel):
    name: str
    aon_url: str
    image_url: str | None = None


@router.post("/api/v1/scan-aon-monsters")
async def scan_aon_monsters(req: AonScanRequest):
    letters = [chr(i) for i in range(65, 91)] if req.letters == "all" else [req.letters.upper()]
    all_monsters = []
    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0) as client:
        for letter in letters:
            if len(all_monsters) >= req.max_monsters:
                break
            try:
                resp = await client.get(f"{MONSTER_LIST}?Letter={letter}")
                ids = set(re.findall(r'/Monsters\.aspx\?ID=(\d+)["\']', resp.text))
                names = re.findall(r'<a href="/Monsters\.aspx\?ID=\d+">([^<]+)</a>', resp.text)
                for name, mid in zip(names[:50], list(ids)[:50]):
                    all_monsters.append(MonsterEntry(name=name.strip(), aon_url=f"{AON_BASE}/Monsters.aspx?ID={mid}"))
                    if len(all_monsters) >= req.max_monsters:
                        break
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.warning(f"Letter {letter}: {e}")
    for i in range(0, len(all_monsters), 5):
        await asyncio.gather(*[fetch_art(client, m) for m in all_monsters[i:i+5]])
        await asyncio.sleep(1.0)
    return {"monsters": [m.dict() for m in all_monsters if m.image_url]}


async def fetch_art(client, monster):
    try:
        resp = await client.get(monster.aon_url, timeout=15.0)
        m = re.search(r'<img[^>]+src=["\'](/Images/[^"\']+)["\']', resp.text, re.I)
        if m:
            monster.image_url = f"{AON_BASE}{m.group(1)}"
    except:
        pass
