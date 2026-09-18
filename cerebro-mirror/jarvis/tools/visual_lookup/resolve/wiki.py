"""The encyclopedia leg: what is this thing?

Three tiers, cheapest and most precise first:

1. an exact title on cerebro's abstract service — a part number or a real article
   name lands here, and it is one cheap local call;
2. full-text search on the live Wikipedia API — the tier that actually answers a
   *description*. Asking only for exact titles meant "brown spider with long thin
   legs" matched nothing, because no article is called that, while the same string
   as a search returns Pholcidae and Pholcus phalangioides immediately. This tier
   also carries thumbnails, which is what lets a human confirm the identification
   rather than trust it;
3. full-text search on the local abstract service, for when the live API is
   unreachable — no thumbnails, but it still answers.

Both URLs are environment-overridable and read per call: verifies run with
--network=none and reach these through a loopback stub instead.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

WIKI_SERVICE_URL = "http://127.0.0.1:8090"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
CANDIDATES = int(os.environ.get("VISUAL_LOOKUP_CANDIDATES", "4"))


class ServiceUnavailable(Exception):
    """The service could not be reached at all — which is not the same as
    "it does not know this term", and must not be reported as though it were."""


def wiki_service_url() -> str:
    return os.environ.get("JARVIS_WIKI_URL", WIKI_SERVICE_URL).rstrip("/")


def wikipedia_api_url() -> str:
    return os.environ.get("WIKIPEDIA_API_URL", WIKIPEDIA_API_URL).rstrip("/")


def http_timeout() -> float:
    return float(os.environ.get("VISUAL_LOOKUP_HTTP_TIMEOUT", "15"))


def user_agent() -> str:
    """Wikimedia answers 403 to the default python-urllib User-Agent, so the live tier
    must identify itself. The symptom is silent: both wikis look like they have never
    heard of the term, and every lookup quietly ends up in the image fallback."""
    return os.environ.get("VISUAL_LOOKUP_USER_AGENT",
                          "visual-lookup/0.1 (Jarvis fleet eyes)")


def _get_json(url: str, params: dict):
    encoded = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{encoded}",
                                     headers={"User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(request, timeout=http_timeout()) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError) as exc:
        raise ServiceUnavailable(f"{url}: {exc}") from exc
    except ValueError:
        return None  # answered, but not with JSON we can use


def _article_url(title: str) -> str:
    return "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))


def _candidate(title: str, extract: str, thumbnail: str = "") -> dict:
    return {"title": title, "extract": extract.strip(),
            "url": _article_url(title), "thumbnail": thumbnail}


def service_lookup(term: str) -> dict | None:
    """An exact title on cerebro's abstract service."""
    data = _get_json(f"{wiki_service_url()}/lookup", {"title": term})
    result = (data or {}).get("result") or {}
    text = (result.get("abstract") or "").strip()
    return _candidate(result.get("title") or term, text) if text else None


def wikipedia_search(term: str, limit: int | None = None) -> list[dict]:
    """Full-text search on the live API, with a representative image per hit."""
    data = _get_json(wikipedia_api_url(), {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": term, "gsrlimit": limit or CANDIDATES,
        "prop": "pageimages|extracts", "exintro": "1", "explaintext": "1",
        "piprop": "thumbnail", "pithumbsize": 320,
    })
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    return [_candidate(page.get("title", ""), page.get("extract") or "",
                       (page.get("thumbnail") or {}).get("source", ""))
            for page in sorted(pages.values(), key=lambda p: p.get("index", 99))
            if page.get("title")]


def service_search(term: str, limit: int | None = None) -> list[dict]:
    """Full-text search on the local dump — no images, but it works without the WAN."""
    data = _get_json(f"{wiki_service_url()}/search",
                     {"q": term, "k": limit or CANDIDATES})
    return [_candidate(item.get("title", ""), item.get("abstract") or "")
            for item in ((data or {}).get("results") or [])
            if item.get("title")]


def lookup(term: str) -> tuple[list[dict], list[str]]:
    """Candidates for a term, best first, and which tiers could not be asked.

    Never raises. An empty list means every tier that could be reached was asked and
    none of them knew the term — which is a different fact from a tier being down, and
    the caller needs to be able to tell them apart.
    """
    unreachable: list[str] = []

    try:
        exact = service_lookup(term)
        if exact:
            return [exact], unreachable
    except ServiceUnavailable:
        unreachable.append("wiki-service")

    try:
        hits = wikipedia_search(term)
        if hits:
            return hits, unreachable
    except ServiceUnavailable:
        unreachable.append("wikipedia")

    try:
        hits = service_search(term)
        if hits:
            return hits, unreachable
    except ServiceUnavailable:
        unreachable.append("wiki-service")

    return [], unreachable
