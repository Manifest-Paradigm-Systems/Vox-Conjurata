"""Turn what the eyes saw into an answer: what the thing is, and where to read more.

Identification is a *search* problem, not a lookup one. An object the model describes as
"a brown spider with long thin legs" is not an article title and never will be — asking
the encyclopedia for a title that matches only ever worked for things already named
after themselves, like part numbers. The resolver asks what best matches, and returns
several candidates rather than one confident guess, because for a species the cost of a
confident wrong answer is the whole point of showing the pictures.

When no encyclopedia knows it at all, fall back to pictures of the thing.
"""
from .images import search as image_search
from .wiki import CANDIDATES as CANDIDATES_WANTED
from .wiki import lookup as wiki_lookup

MAX_QUERIES = 3


def _markings(parsed: dict) -> list[str]:
    markings = parsed.get("markings") or []
    if not isinstance(markings, list):
        markings = [markings]
    return [str(marking).strip() for marking in markings if str(marking).strip()]


def _queries(parsed: dict) -> list[str]:
    """What to ask the encyclopedias, best first: the model's own name for the thing,
    then any markings, then the description as a last resort."""
    queries: list[str] = []

    def add(value):
        value = str(value or "").strip()
        if value and value not in queries:
            queries.append(value)

    add(parsed.get("identification"))
    for marking in _markings(parsed):
        add(marking)
    add(parsed.get("description"))
    return queries[:MAX_QUERIES]


def resolve_candidate(parsed: dict) -> dict:
    """Look the object up, and say what could not be looked up.

    Never raises: with the resolution services down this degrades to the bare markings
    rather than failing the whole reading. What it will not do is claim the encyclopedias
    had nothing to say when in fact it never managed to ask them — that is what
    `unavailable` is for.
    """
    if not isinstance(parsed, dict):
        parsed = {}

    markings = _markings(parsed)
    candidates: list[dict] = []
    unavailable: list[str] = []
    matched = ""
    sources: list[dict] = []

    # Ask every phrasing until there are enough candidates, rather than stopping at the
    # first query that returns anything: a vague name returns *something*, and that
    # something is how "Spider" ends up answered by a 1991 television series.
    for term in _queries(parsed):
        found, unreachable = wiki_lookup(term)
        unavailable.extend(source for source in unreachable if source not in unavailable)
        for candidate in found:
            if not any(seen["title"] == candidate["title"] for seen in candidates):
                candidates.append(candidate)
        if found and not matched:
            matched = term
        if len(candidates) >= CANDIDATES_WANTED:
            break

    if candidates:
        sources = [{"title": f"Wiki: {c['title']}", "url": c["url"]} for c in candidates]
        best = candidates[0]
        specs = {"title": best["title"], "extract": best["extract"],
                 "thumbnail": best["thumbnail"], "matched": matched}
    else:
        # No encyclopedia knows it. Show the thing itself instead.
        queries = _queries(parsed)
        if queries:
            hits, reachable = image_search(queries[0])
            if not reachable and "searxng" not in unavailable:
                unavailable.append("searxng")
            for hit in hits:
                sources.append({"title": hit["title"], "url": hit["url"], "kind": "image"})
        specs = {}

    return {"specs": specs, "markings": markings, "candidates": candidates,
            "sources": sources, "unavailable": unavailable}


def format_answer(data: dict) -> dict:
    # Ensure the final answer always contains the keys specs, markings, and sources
    return {
        'specs': data.get('specs', {}),
        'markings': data.get('markings', {}),
        'candidates': data.get('candidates', []),
        'sources': data.get('sources', {}),
        'unavailable': data.get('unavailable', []),
    }
