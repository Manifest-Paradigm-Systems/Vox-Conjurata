"""The fallback leg: show the thing when no encyclopedia knows its name.

Searches the fleet's own SearXNG, so this needs no API key and no per-query cost, and it
is the same `SEARXNG_URL` the Jarvis brain already uses.

Queries carry the `!goi` bang on purpose. Left to its own defaults SearXNG blends every
image engine it has, and on this instance two icon libraries (lucide, devicons) supply
about 90% of the results — pictures of nothing, named things like "aarch64". The bang
asks Google Images directly, which returns real photographs and, for a part number,
actual pictures of the part. The aggregate is still reachable by clearing the bang.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

SEARXNG_URL = "http://127.0.0.1:8888/search"


def searxng_url() -> str:
    return os.environ.get("SEARXNG_URL", SEARXNG_URL)


def image_bang() -> str:
    return os.environ.get("VISUAL_LOOKUP_IMAGE_BANG", "!goi").strip()


def image_timeout() -> float:
    return float(os.environ.get("VISUAL_LOOKUP_IMAGE_TIMEOUT", "20"))


def image_limit() -> int:
    return int(os.environ.get("VISUAL_LOOKUP_IMAGE_LIMIT", "5"))


def search(term: str, limit: int | None = None) -> tuple[list[dict], bool]:
    """Image hits for a term as [{title, url}], plus whether search was reachable.

    Mirrors wiki.lookup: never raises, and says whether it could be asked at all.
    """
    term = f"{image_bang()} {term}".strip()
    params = urllib.parse.urlencode({
        "q": term, "format": "json", "categories": "images", "language": "en"})
    try:
        with urllib.request.urlopen(f"{searxng_url()}?{params}",
                                    timeout=image_timeout()) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError):
        return [], False
    except ValueError:
        return [], True  # reachable, but not with JSON we can use

    out: list[dict] = []
    for item in body.get("results", []):
        link = item.get("img_src") or item.get("url")
        if not link:
            continue
        out.append({"title": (item.get("title") or link)[:140], "url": link})
        if len(out) >= (limit or image_limit()):
            break
    return out, True
