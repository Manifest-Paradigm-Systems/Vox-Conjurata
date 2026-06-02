#!/usr/bin/env python3
"""vox-llm-core — OpenRouter proxy server.

Replaces the local llama-server/Qwen container with a lightweight FastAPI
proxy that forwards requests to OpenRouter.  The ``model`` field in each
request selects a routing strategy (latency vs cost) and a target model,
both defined in ``openrouter_routing.json``.
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
CONFIG_PATH = Path(__file__).parent / "openrouter_routing.json"

with open(CONFIG_PATH) as f:
    routing_config: dict[str, Any] = json.load(f)

OPENROUTER_BASE = routing_config["openrouter_base_url"]
MODEL_ROUTES: dict[str, dict[str, str]] = routing_config["models"]
DEFAULT_ROUTE: dict[str, str] = routing_config["default"]

# Map a route's "strategy" to an OpenRouter provider-sort preference.
# https://openrouter.ai/docs/features/provider-routing
_STRATEGY_PROVIDER_SORT: dict[str, str] = {
    "cost": "price",        # cheapest provider first
    "latency": "throughput",  # highest-throughput provider first
}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="vox-llm-core (OpenRouter proxy)", version="1.0.0")

# Shared HTTP client (connection pooling)
client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def startup() -> None:
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    logger.info("OpenRouter proxy started (key present: %s)", bool(OPENROUTER_API_KEY))


@app.on_event("shutdown")
async def shutdown() -> None:
    if client is not None:
        await client.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_route(model_name: str) -> dict[str, str]:
    """Pick a routing entry based on the ``model`` field.

    Exact match is tried first; if the model name starts with a known key
    followed by ``/``, that key is used (e.g. ``cheapest/gpt-4o-mini``
    matches the ``cheapest`` entry).  Falls back to the default route.
    """
    if model_name in MODEL_ROUTES:
        return MODEL_ROUTES[model_name]

    for prefix, route in MODEL_ROUTES.items():
        if model_name.startswith(prefix + "/"):
            return route

    logger.info("No explicit route for '%s'; using default", model_name)
    return DEFAULT_ROUTE


def strip_model_prefix(model_name: str) -> str:
    """Strip a known routing prefix, returning the remainder as user-hint."""
    for prefix in MODEL_ROUTES:
        if model_name == prefix:
            return ""  # no user override
        if model_name.startswith(prefix + "/"):
            return model_name[len(prefix) + 1:]  # e.g. "gpt-4o-mini"
    return model_name


async def _proxy_request(
    body: dict[str, Any],
    openrouter_path: str = "/v1/chat/completions",
) -> dict[str, Any]:
    """Forward *body* to OpenRouter and return the parsed JSON response."""
    if client is None:
        raise RuntimeError("HTTP client not initialised")

    model_name = body.get("model", "")
    route = resolve_route(model_name)
    target_model = route["model"]

    # Allow user to override the OpenRouter model by appending it after a
    # slash: e.g. "coding-cheap/openai/gpt-4o-mini"
    user_model_hint = strip_model_prefix(model_name)
    if user_model_hint:
        target_model = user_model_hint

    # Build the OpenRouter payload — override model, keep everything else
    payload = {k: v for k, v in body.items() if k != "model"}
    payload["model"] = target_model

    # Enforce the route's strategy via OpenRouter provider preferences.
    # "cost" -> cheapest provider first; "latency" -> highest throughput.
    # A caller-supplied "provider" block always wins and is left untouched.
    if "provider" not in payload:
        provider_sort = _STRATEGY_PROVIDER_SORT.get(route.get("strategy", ""))
        if provider_sort is not None:
            payload["provider"] = {"sort": provider_sort}

    # Convert /v1/completions style requests to chat format (OpenRouter
    # deprecated the raw completions endpoint).
    if openrouter_path == "/v1/completions" and "prompt" in payload:
        prompt = payload.pop("prompt")
        messages = payload.pop("messages", [])
        chat_payload = {
            **payload,
            "messages": [
                *messages,
                {"role": "user", "content": prompt},
            ],
        }
        payload = chat_payload
        openrouter_path = "/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://vox-conjurata.local",
        "X-Title": "vox-llm-core",
    }

    url = f"{OPENROUTER_BASE}{openrouter_path}"
    logger.info(
        "→ %s  model=%s  strategy=%s",
        url,
        target_model,
        route.get("strategy", "unknown"),
    )

    try:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        detail = await _safe_error_body(exc.response)
        logger.error("OpenRouter %s: %s", exc.response.status_code, detail)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"OpenRouter error: {detail}",
        ) from exc
    except httpx.RequestError as exc:
        logger.error("Request to OpenRouter failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"OpenRouter unreachable: {exc}") from exc


async def _safe_error_body(response: httpx.Response) -> str:
    try:
        return response.text[:500]
    except Exception:
        return "(no body)"


# ---------------------------------------------------------------------------
# Endpoints  (OpenAI-compatible, same signatures as llama-server)
# ---------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    body = await request.json()
    result = await _proxy_request(body, "/v1/chat/completions")
    return JSONResponse(content=result)


@app.post("/v1/completions")
async def completions(request: Request) -> JSONResponse:
    body = await request.json()
    result = await _proxy_request(body, "/v1/completions")
    return JSONResponse(content=result)


# Health-check (used by Docker health checks / monitoring)
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "proxy": "openrouter"}


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    """Return the configured route names as pseudo-models for discovery."""
    models = [
        {
            "id": name,
            "object": "model",
            "created": 0,
            "owned_by": "vox-llm-core",
            "permission": [],
        }
        for name in MODEL_ROUTES
    ]
    models.append({
        "id": "default",
        "object": "model",
        "created": 0,
        "owned_by": "vox-llm-core",
        "permission": [],
    })
    return JSONResponse(content={"object": "list", "data": models})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("proxy:app", host="0.0.0.0", port=port, log_level="info")