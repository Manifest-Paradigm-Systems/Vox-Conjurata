"""
PDF Import Vision — Orchestrator endpoint for vox-pdf-importer.
Takes a rendered PDF page image, sends it to MiniCPM-V for stat block / text extraction,
then refines the response into structured JSON using vox-llm-core.
"""

import base64
import httpx
import json
import logging
import os
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger("vox-pdf-import-vision")

router = APIRouter()

VISION_READER_URL = os.getenv("VISION_READER_URL", "http://vox-vision-reader:8000")
LLM_CORE_URL = os.getenv("OLLAMA_URL", "http://vox-llm-core:8081")


class PdfImportVisionRequest(BaseModel):
    page_image: str  # base64-encoded data URI
    page_number: int
    game_system: str = "pf2e"  # "pf2e" or "dnd5e"
    previous_context: str = ""
    max_tokens: int = 2048
    temperature: float = 0.1


@router.post("/api/v1/pdf-import-vision")
async def pdf_import_vision(req: PdfImportVisionRequest):
    """
    Receives a rendered PDF page image, extracts structured Pathfinder 2e data
    via vox-vision-reader, and optionally refines to clean JSON via vox-llm-core.

    Returns:
        { page, raw_extraction, structured_data, has_content }
    """
    # Step 1: Build vision prompt for the selected game system
    if req.game_system == "dnd5e":
        vision_prompt = _build_dnd5e_prompt(req.previous_context)
    else:
        vision_prompt = _build_pf2e_prompt(req.previous_context)

    # Step 2: Strip data URI prefix if present
    image_b64 = req.page_image
    if image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[1]

    # Step 3: Call vox-vision-reader (MiniCPM-V 2.6)
    vision_payload = {
        "model": "MiniCPM-V-2_6-Int4",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{image_b64}"}}
                ]
            }
        ],
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{VISION_READER_URL}/v1/chat/completions",
                json=vision_payload
            )
            resp.raise_for_status()
            raw_text = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Vision reader failed for page {req.page_number}: {e}")
        raise HTTPException(status_code=502, detail=f"Vision API failed: {str(e)}")

    # Step 4: Refine into structured JSON via vox-llm-core
    json_result = None
    if raw_text.strip() and "NO_CONTENT" not in raw_text:
        try:
            json_result = await _refine_to_json(raw_text, req.page_number)
        except Exception as e:
            logger.warning(f"JSON refinement failed for page {req.page_number}: {e}")
            json_result = _fallback_parse(raw_text)

    return {
        "page": req.page_number,
        "raw_extraction": raw_text,
        "structured_data": json_result or {},
        "has_content": json_result is not None and json_result.get("type") != "empty",
    }


def _build_pf2e_prompt(previous_context: str) -> str:
    context_block = ""
    if previous_context:
        context_block = f"\nCONTEXT FROM PREVIOUS PAGE:\n{previous_context}\n\n"

    return (
        "You are a Pathfinder 2e PDF reader. Extract ALL game statistics and text from this page image.\n"
        f"{context_block}"
        "TASKS (in order of priority):\n"
        "1. If this page contains an NPC or creature stat block, extract ALL of the following:\n"
        "   - NPC/Creature Name\n"
        "   - Level (e.g., Creature 5, Level 5)\n"
        "   - Alignment and Size (e.g., LE Large, N Medium)\n"
        "   - Traits (e.g., human, undead, fire)\n"
        "   - Perception, Languages\n"
        "   - Skills with modifiers (e.g., Stealth +12, Athletics +8)\n"
        "   - Ability Scores: Str, Dex, Con, Int, Wis, Cha\n"
        "   - HP, AC, Fort, Ref, Will saves\n"
        "   - Speed (e.g., 30 feet, fly 40 feet)\n"
        "   - Attacks (name, bonus, damage dice, damage type)\n"
        "   - Special abilities (name, description)\n"
        "   - Spells (list with levels and names)\n"
        "   - Items carried\n"
        "   - Source book name if visible\n"
        "5. If the NPC has a character portrait or illustration on this page, estimate its bounding box as `portrait: {x, y, w, h}` in pixel coordinates (0,0 = top-left of page, use page dimensions to estimate). Skip if no portrait visible.\n\n"
        "2. If this page contains narrative text, extract the full text preserving paragraphs.\n"
        "3. If this page is a map, describe it, estimate width/height, and list any visible NPC names with their x,y pixel positions as `tokens: [{name, x, y}]`.\n"
        "4. If this page is art only, respond with just: NO_CONTENT\n\n"
        "OUTPUT FORMAT:\n"
        "For stat blocks, output JSON in code fences:\n"
        '```json\n{"type":"npc","name":"...","level":N,"alignment":"...","size":"...","traits":[...],'
        '"perception":N,"languages":[...],"skills":{"skill_name":N,...},'
        '"abilities":{"str":N,"dex":N,"con":N,"int":N,"wis":N,"cha":N},'
        '"hp":N,"ac":N,"saves":{"fort":N,"ref":N,"will":N},'
        '"speed":"...","attacks":[{"name":"...","bonus":N,"damage":"...","traits":[...]}],'
        '"abilities_list":[{"name":"...","description":"..."}],'
        '"spells":[{"level":N,"spells":[...]}],"items":[...],'
        '"portrait":{"x":N,"y":N,"w":N,"h":N}}\n```\n'
        "For narrative pages:\n"
        '```json\n{"type":"narrative","title":"...","text":"..."}\n```\n'
        "For maps:\n"
        '```json\n{"type":"map","name":"...","description":"..."}\n```\n'
        "Be thorough - extract EVERY number and modifier visible on the page."
    )


def _build_dnd5e_prompt(previous_context: str) -> str:
    """D&D 5e stat block extraction prompt for MiniCPM-V."""
    context_block = ""
    if previous_context:
        context_block = f"\nCONTEXT FROM PREVIOUS PAGE:\n{previous_context}\n\n"

    return (
        "You are a D&D 5e PDF reader. Extract ALL game statistics and text from this page image.\n"
        f"{context_block}"
        "TASKS (in order of priority):\n"
        "1. If this page contains an NPC or creature stat block, extract ALL of the following:\n"
        "   - NPC/Creature Name\n"
        "   - Challenge Rating (CR, e.g., 1/4, 3, 8)\n"
        "   - Armor Class (AC)\n"
        "   - Hit Points (HP)\n"
        "   - Speed (e.g., 30 ft., fly 60 ft.)\n"
        "   - Ability Scores: Strength, Dexterity, Constitution, Intelligence, Wisdom, Charisma\n"
        "   - Saving Throws (e.g., Str +5, Dex +3)\n"
        "   - Skills (e.g., Stealth +5, Perception +3)\n"
        "   - Damage Resistances, Immunities, Vulnerabilities\n"
        "   - Condition Immunities\n"
        "   - Senses (e.g., darkvision 60 ft., passive Perception 13)\n"
        "   - Languages\n"
        "   - Size and Type (e.g., Medium humanoid, Huge dragon)\n"
        "   - Alignment\n"
        "   - Traits/Abilities (name and description)\n"
        "   - Actions (name, attack bonus, damage dice, damage type)\n"
        "   - Bonus Actions, Reactions, Legendary Actions\n"
        "   - Spellcasting (spell list with levels)\n"
        "   - Equipment carried\n"
        "5. If the NPC has a character portrait or illustration on this page, estimate its bounding box as `portrait: {x, y, w, h}` in pixel coordinates (0,0 = top-left of page). Skip if no portrait visible.\n\n"
        "2. If this page contains narrative text, extract the full text preserving paragraphs.\n"
        "3. If this page is a map, describe it, estimate width/height, and list any visible NPC names with their x,y pixel positions as `tokens: [{name, x, y}]`.\n"
        "4. If this page is art only, respond with just: NO_CONTENT\n\n"
        "OUTPUT FORMAT:\n"
        "For stat blocks, output JSON in code fences:\n"
        '```json\n{"type":"npc","name":"...","challenge":N,"ac":N,"hp":N,'
        '"speed":"...","abilities":{"str":N,"dex":N,"con":N,"int":N,"wis":N,"cha":N},'
        '"saves":{"str":N,"dex":N,"con":N,"int":N,"wis":N,"cha":N},'
        '"skills":{"skill_name":N,...},'
        '"damage_resistances":[...],"damage_immunities":[...],"condition_immunities":[...],'
        '"senses":"...","languages":[...],"size":"...","alignment":"...","type":"...",'
        '"traits":[{"name":"...","description":"..."}],'
        '"attacks":[{"name":"...","bonus":N,"damage":"...","damage_type":"..."}],'
        '"spells":[{"level":N,"spells":[...]}],"equipment":[...],'
        '"portrait":{"x":N,"y":N,"w":N,"h":N}}\n```\n'
        "For narrative pages:\n"
        '```json\n{"type":"narrative","title":"...","text":"..."}\n```\n'
        "For maps:\n"
        '```json\n{"type":"map","name":"...","description":"..."}\n```\n'
        "Be thorough - extract EVERY number and modifier visible on the page."
    )


async def _refine_to_json(raw_text: str, page_number: int) -> dict:
    """Send vision raw output to vox-llm-core for structured JSON refinement."""
    prompt = (
        "Convert the following stat block extraction into a clean JSON object. "
        "Fix any formatting issues. "
        "If content is narrative, wrap as {\"type\":\"narrative\",\"text\":\"...\"}. "
        "If no useful content, return {\"type\":\"empty\"}.\n\n"
        f"RAW EXTRACTION:\n{raw_text}"
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{LLM_CORE_URL}/v1/chat/completions",
            json={
                "model": "cheapest",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
                "max_tokens": 2048,
            }
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)


def _fallback_parse(raw_text: str) -> dict:
    """Fallback if LLM refinement fails — try regex extraction of JSON."""
    # Extract JSON from markdown code fences
    json_match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', raw_text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try bare JSON
    json_match2 = re.search(r'(\{.*"type".*?\})', raw_text, re.DOTALL)
    if json_match2:
        try:
            return json.loads(json_match2.group(1))
        except json.JSONDecodeError:
            pass

    return {"type": "unstructured", "raw_text": raw_text[:5000]}
