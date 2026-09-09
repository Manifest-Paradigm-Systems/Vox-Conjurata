#!/usr/bin/env python3
"""vox-llm-core — role router / gateway (OpenRouter + local llama.cpp roles).

Apps dial one host and name a *role* ("director", "coder", "actor", ...).
The gateway resolves the role to a route:

  - type "local"      → an OpenAI-compatible llama.cpp server (e.g. cerebro),
                        with optional "fallback" to an OpenRouter route when
                        the local brain is down or saturated (5xx/503).
  - type "openrouter" → OpenRouter model + strategy (legacy behaviour;
                        strategy maps to provider.sort unless caller sent one).
  - alias entries     → old names resolve to their role's route during
                        migration (kunou/vox-llm-kunou → actor, r1 → director,
                        qwen-coder → coder, ...).

Humans dial model endpoints directly; programs go through this gateway.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
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
CONFIG_PATH = Path(__file__).parent / "openrouter_routing.json"

with open(CONFIG_PATH) as f:
    routing_config: dict[str, Any] = json.load(f)

OPENROUTER_BASE = routing_config["openrouter_base_url"]
MODEL_ROUTES: dict[str, dict[str, str]] = routing_config.get("models", {})
DEFAULT_ROUTE: dict[str, str] = routing_config.get(
    "default", {"type": "openrouter", "model": "qwen/qwen-2.5-7b-instruct", "strategy": "latency"}
)

_STRATEGY_PROVIDER_SORT: dict[str, str] = {
    "cost": "price",
    "latency": "throughput",
}

# Local brains can think for minutes (R1 reasoning) — generous read timeout.
LOCAL_TIMEOUT = httpx.Timeout(600.0, connect=10.0)
OR_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="vox-llm-core (role gateway)", version="3.0.0")

client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def startup() -> None:
    global client
    client = httpx.AsyncClient()
    logger.info(
        "vox-llm-core gateway started. routes=%s, OpenRouter key: %s",
        sorted(MODEL_ROUTES),
        bool(OPENROUTER_API_KEY),
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    if client is not None:
        await client.aclose()


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def resolve_route(name: str, depth: int = 0) -> dict[str, str]:
    """Resolve aliases to a concrete route (openrouter|local)."""
    route = MODEL_ROUTES.get(name, DEFAULT_ROUTE)
    if depth > 4:
        return DEFAULT_ROUTE
    if "alias" in route:
        return resolve_route(str(route["alias"]), depth + 1)
    return route


def _local_failure_status(exc: Exception) -> int:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return 502  # connection error / timeout etc.


async def _dispatch_local(body: dict[str, Any], path: str, route: dict[str, str]) -> dict[str, Any]:
    """POST to a local llama.cpp-style OpenAI endpoint. Raises on failure."""
    if client is None:
        raise RuntimeError("HTTP client not initialised")
    url = f"{route['url'].rstrip('/')}{path}"
    payload = {k: v for k, v in body.items() if k != "model"}
    payload["model"] = "local"
    logger.info("→ local: %s", url)
    resp = await client.post(url, json=payload, timeout=LOCAL_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


async def _dispatch_openrouter(body: dict[str, Any], path: str, route: dict[str, str]) -> dict[str, Any]:
    """POST to OpenRouter. Raises on failure."""
    if client is None:
        raise RuntimeError("HTTP client not initialised")
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=503, detail="No OpenRouter API key provided.")
    target_model = route["model"]
    payload = {k: v for k, v in body.items() if k != "model"}
    payload["model"] = target_model
    if "provider" not in payload:
        provider_sort = _STRATEGY_PROVIDER_SORT.get(route.get("strategy", ""))
        if provider_sort:
            payload["provider"] = {"sort": provider_sort}
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vox-conjurata.local",
        "X-Title": "vox-llm-core",
    }
    url = f"{OPENROUTER_BASE}{path}"
    logger.info("→ OpenRouter: %s model=%s", url, target_model)
    resp = await client.post(url, json=payload, headers=headers, timeout=OR_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


async def _dispatch_direct(body: dict[str, Any], path: str, route: dict[str, str]) -> dict[str, Any]:
    """POST to a direct first-party OpenAI-compatible API (e.g. DeepSeek)."""
    if client is None:
        raise RuntimeError("HTTP client not initialised")
    key_env = route.get("api_key_env", "DEEPSEEK_API_KEY")
    api_key = os.getenv(key_env, "")
    if not api_key:
        raise HTTPException(status_code=503, detail=f"Direct route needs env var {key_env}.")
    base = route["base_url"].rstrip("/")
    target_model = route["model"]
    payload = {k: v for k, v in body.items() if k != "model"}
    payload["model"] = target_model
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{base}{path}"
    logger.info("→ direct: %s model=%s", url, target_model)
    resp = await client.post(url, json=payload, headers=headers, timeout=OR_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


async def _proxy_request(body: dict[str, Any], path: str) -> dict[str, Any]:
    if client is None:
        raise RuntimeError("HTTP client not initialised")

    requested = body.get("model", "")
    route = resolve_route(requested)

    if route.get("type") == "local":
        try:
            return await _dispatch_local(body, path, route)
        except HTTPException:
            raise
        except Exception as exc:
            fallback = route.get("fallback")
            if not fallback:
                raise HTTPException(status_code=502, detail=f"Local route failed: {exc}")
            logger.warning("Local %s failed (%s), falling back to '%s'", requested, exc, fallback)
            fb_route = resolve_route(fallback)
            if fb_route.get("type") == "local":
                raise HTTPException(status_code=502, detail=f"Local route failed and fallback is local: {exc}")
            try:
                return await _dispatch_openrouter(body, path, fb_route)
            except HTTPException:
                raise
            except Exception as exc2:
                raise HTTPException(status_code=502, detail=f"Local route failed: {exc}; fallback failed: {exc2}")

    # default: openrouter
    try:
        return await _dispatch_openrouter(body, path, route)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenRouter failed: {exc}")


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
    return {"status": "ok", "routes": sorted(MODEL_ROUTES)}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("proxy:app", host="0.0.0.0", port=port, log_level="info")
