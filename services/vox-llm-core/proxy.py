#!/usr/bin/env python3
"""vox-llm-core — SGLang + OpenRouter fallback server.

This service primarily routes requests to a local SGLang server running Qwen 2.5 7B.
If the local server is unavailable or fails, it falls back to OpenRouter.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - vox-llm-core - %(levelname)s - %(message)s",
)
logger = logging.getLogger("vox-llm-core")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
LOCAL_SGLANG_URL = os.getenv("LOCAL_SGLANG_URL", "http://localhost:8000")
CONFIG_PATH = Path(__file__).parent / "openrouter_routing.json"

if CONFIG_PATH.exists():
    with open(CONFIG_PATH) as f:
        routing_config: dict[str, Any] = json.load(f)
else:
    routing_config = {
        "openrouter_base_url": "https://openrouter.ai/api",
        "models": {
            "default": {"model": "qwen/qwen-2.5-7b-instruct", "strategy": "latency"}
        },
        "default": {"model": "qwen/qwen-2.5-7b-instruct", "strategy": "latency"}
    }

OPENROUTER_BASE = routing_config["openrouter_base_url"]
MODEL_ROUTES: dict[str, dict[str, str]] = routing_config.get("models", {})
DEFAULT_ROUTE: dict[str, str] = routing_config.get("default", {"model": "qwen/qwen-2.5-7b-instruct", "strategy": "latency"})

_STRATEGY_PROVIDER_SORT: dict[str, str] = {
    "cost": "price",
    "latency": "throughput",
}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="vox-llm-core (SGLang + OpenRouter)", version="2.0.0")

# Shared HTTP client
client: httpx.AsyncClient | None = None

@app.on_event("startup")
async def startup() -> None:
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    logger.info("vox-llm-core started. Local SGLang: %s, OpenRouter Key: %s", LOCAL_SGLANG_URL, bool(OPENROUTER_API_KEY))

@app.on_event("shutdown")
async def shutdown() -> None:
    if client is not None:
        await client.aclose()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _proxy_request(body: dict[str, Any], path: str) -> dict[str, Any]:
    if client is None: raise RuntimeError("HTTP client not initialised")

    # 1. Attempt Local SGLang
    try:
        logger.info(f"Attempting local SGLang: {LOCAL_SGLANG_URL}{path}")
        resp = await client.post(f"{LOCAL_SGLANG_URL}{path}", json=body)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"Local SGLang returned {resp.status_code}, falling back to OpenRouter")
    except Exception as e:
        logger.warning(f"Local SGLang failed: {e}, falling back to OpenRouter")

    # 2. OpenRouter Fallback
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=503, detail="Local LLM failed and no OpenRouter API key provided.")

    model_name = body.get("model", "")
    route = MODEL_ROUTES.get(model_name, DEFAULT_ROUTE)
    target_model = route["model"]

    payload = {k: v for k, v in body.items() if k != "model"}
    payload["model"] = target_model
    if "provider" not in payload:
        provider_sort = _STRATEGY_PROVIDER_SORT.get(route.get("strategy", ""))
        if provider_sort: payload["provider"] = {"sort": provider_sort}

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vox-conjurata.local",
        "X-Title": "vox-llm-core",
    }

    url = f"{OPENROUTER_BASE}{path}"
    logger.info(f"→ OpenRouter: {url} model={target_model}")

    try:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error(f"OpenRouter failed: {exc}")
        raise HTTPException(status_code=502, detail=f"All LLM backends failed: {exc}")

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    result = await _proxy_request(body, "/v1/chat/completions")
    return JSONResponse(content=result)

@app.post("/v1/completions")
async def completions(request: Request):
    body = await request.json()
    result = await _proxy_request(body, "/v1/completions")
    return JSONResponse(content=result)

@app.get("/health")
async def health():
    return {"status": "ok", "local_url": LOCAL_SGLANG_URL}

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("proxy:app", host="0.0.0.0", port=port, log_level="info")
