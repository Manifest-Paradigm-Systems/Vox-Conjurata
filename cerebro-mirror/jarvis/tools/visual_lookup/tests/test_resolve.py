"""Resolution, exercised for real against stub services on loopback.

Verifies run with --network=none, so the three services (cerebro's wiki service, the
live Wikipedia API, SearXNG) are replaced by one stub server routing by path. Every line
of our own code runs for real; only the remote endpoints are faked.
"""
import http.server
import json
import threading
import urllib.parse

from visual_lookup.resolve import resolve_candidate


def _stub(routes: dict, seen: dict | None = None):
    """Routes keyed by path. A route returns a body dict (200), or a (status, dict)."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if seen is not None:
                seen.update(self.headers)
            path, _, raw = self.path.partition("?")
            query = dict(urllib.parse.parse_qsl(raw))
            result = routes.get(path, lambda q: {})(query)
            status, body = result if isinstance(result, tuple) else (200, result)
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _point_at(monkeypatch, port: int):
    monkeypatch.setenv("JARVIS_WIKI_URL", f"http://127.0.0.1:{port}/wiki")
    monkeypatch.setenv("WIKIPEDIA_API_URL", f"http://127.0.0.1:{port}/w/api.php")
    monkeypatch.setenv("SEARXNG_URL", f"http://127.0.0.1:{port}/search")


def _search_page(title, extract, thumbnail=""):
    page = {"title": title, "extract": extract, "index": 1}
    if thumbnail:
        page["thumbnail"] = {"source": thumbnail}
    return {"query": {"pages": {"1": page}}}


def _nothing(path="/w/api.php"):
    return lambda q: {"query": {"pages": {}}}


def test_an_exact_title_is_answered_locally_first(monkeypatch):
    server = _stub({"/wiki/lookup": lambda q: {
        "result": {"title": "Voltage regulator", "abstract": "Holds a voltage steady."}}})
    try:
        _point_at(monkeypatch, server.server_address[1])
        result = resolve_candidate({"markings": ["Voltage regulator"], "description": ""})
    finally:
        server.shutdown()

    assert result["candidates"][0]["title"] == "Voltage regulator"
    assert result["specs"]["matched"] == "Voltage regulator"
    assert result["unavailable"] == []


def test_a_description_is_searched_not_looked_up(monkeypatch):
    """The point of the whole exercise: nobody named the spider, so no title matches.

    An exact-title lookup returns nothing for "brown spider with long thin legs".
    Full-text search returns the cellar spider, with the picture to confirm it.
    """
    server = _stub({
        "/wiki/lookup": lambda q: {"result": None},
        "/w/api.php": lambda q: _search_page(
            "Pholcus phalangioides",
            "commonly known as the cosmopolitan cellar spider",
            "https://upload.wikimedia.org/thumb/Pholcus.phalangioides.jpg"),
    })
    try:
        _point_at(monkeypatch, server.server_address[1])
        result = resolve_candidate(
            {"identification": "cellar spider with long thin legs",
             "markings": [], "description": "a brown spider with long legs"})
    finally:
        server.shutdown()

    best = result["candidates"][0]
    assert best["title"] == "Pholcus phalangioides"
    assert best["thumbnail"].endswith("Pholcus.phalangioides.jpg")
    assert "cellar spider" in best["extract"]


def test_identification_is_asked_before_the_description(monkeypatch):
    asked: list[str] = []
    server = _stub({
        "/wiki/lookup": lambda q: {"result": None},
        "/w/api.php": lambda q: (asked.append(q.get("gsrsearch", "")), _search_page("X", "y"))[1],
    })
    try:
        _point_at(monkeypatch, server.server_address[1])
        resolve_candidate({"identification": "cellar spider", "markings": ["LM2596S"],
                           "description": "a brown spider"})
    finally:
        server.shutdown()

    assert asked and asked[0] == "cellar spider", asked


def test_a_vague_name_does_not_end_the_search(monkeypatch):
    """A vague identification returns *something*, and that something is how "Spider"
    gets answered by a 1991 television series. Keep asking the other phrasings."""
    def pages(query):
        if query == "cellar spider":
            return _search_page("Pholcus phalangioides", "a long-bodied cellar spider")
        return _search_page("Spider-Man", "a comic book character")

    server = _stub({
        "/wiki/lookup": lambda q: {"result": None},
        "/w/api.php": lambda q: pages(q.get("gsrsearch", "")),
    })
    try:
        _point_at(monkeypatch, server.server_address[1])
        result = resolve_candidate({"identification": "spider", "markings": [],
                                    "description": "cellar spider"})
    finally:
        server.shutdown()

    titles = [c["title"] for c in result["candidates"]]
    assert "Spider-Man" in titles
    assert "Pholcus phalangioides" in titles


def test_local_search_answers_when_the_wire_is_down(monkeypatch):
    """No WAN means no live tier and no thumbnails, but the 7M dump still answers."""
    server = _stub({
        "/wiki/lookup": lambda q: {"result": None},
        "/w/api.php": lambda q: (503, {"error": "down"}),
        "/wiki/search": lambda q: {"results": [
            {"title": "Pholcidae", "abstract": "The Pholcidae are a family of spiders."}]},
    })
    try:
        _point_at(monkeypatch, server.server_address[1])
        result = resolve_candidate({"identification": "cellar spider", "markings": [],
                                    "description": ""})
    finally:
        server.shutdown()

    assert result["candidates"][0]["title"] == "Pholcidae"
    assert result["candidates"][0]["thumbnail"] == ""
    assert result["unavailable"] == ["wikipedia"]


def test_falls_back_to_images_when_no_wiki_knows_it(monkeypatch):
    server = _stub({
        "/wiki/lookup": lambda q: {"result": None},
        "/w/api.php": _nothing(),
        "/wiki/search": lambda q: {"results": []},
        "/search": lambda q: {"results": [
            {"title": "LM2596 module", "img_src": "http://example.invalid/a.jpg"}]},
    })
    try:
        _point_at(monkeypatch, server.server_address[1])
        result = resolve_candidate({"markings": ["LM2596S"], "description": ""})
    finally:
        server.shutdown()

    assert result["candidates"] == []
    assert result["specs"] == {}
    assert result["sources"][0]["kind"] == "image"
    assert result["sources"][0]["url"] == "http://example.invalid/a.jpg"


def test_image_search_asks_google_images_directly(monkeypatch):
    """SearXNG's default image blend is ~90% icon libraries on this instance, so the
    query carries a bang targeting one real engine rather than the aggregate."""
    asked: list[str] = []
    server = _stub({
        "/wiki/lookup": lambda q: {"result": None},
        "/w/api.php": _nothing(),
        "/wiki/search": lambda q: {"results": []},
        "/search": lambda q: (asked.append(q.get("q", "")), {"results": []})[1],
    })
    try:
        _point_at(monkeypatch, server.server_address[1])
        resolve_candidate({"markings": ["LM2596S"], "description": ""})
    finally:
        server.shutdown()

    assert asked and asked[0].startswith("!goi "), asked


def test_unreachable_services_are_named_not_guessed(monkeypatch):
    """Down is not the same as "nothing found", and the reading must survive both."""
    _point_at(monkeypatch, 1)
    result = resolve_candidate({"markings": ["LM2596"], "description": ""})

    assert result["candidates"] == []
    assert result["sources"] == []
    assert set(result["unavailable"]) == {"wiki-service", "wikipedia", "searxng"}
    assert result["markings"] == ["LM2596"]


def test_the_live_tier_identifies_itself(monkeypatch):
    """Wikimedia 403s the default python-urllib agent — and the failure is silent:
    it looks exactly like the encyclopedia not having an article."""
    seen: dict = {}
    server = _stub({
        "/wiki/lookup": lambda q: {"result": None},
        "/w/api.php": lambda q: _search_page("Voltage regulator", "Holds a voltage steady."),
    }, seen=seen)
    try:
        _point_at(monkeypatch, server.server_address[1])
        result = resolve_candidate({"markings": ["LM2596"], "description": ""})
    finally:
        server.shutdown()

    assert result["candidates"][0]["title"] == "Voltage regulator"
    agent = seen.get("User-Agent", "")
    assert agent, "no User-Agent sent"
    assert not agent.lower().startswith("python-urllib")
