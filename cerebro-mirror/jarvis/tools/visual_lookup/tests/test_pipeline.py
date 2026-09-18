"""The whole pipeline on one photo, where nobody had named the thing first.

Only the remote services are stubbed — the vision model, the wiki tiers and SearXNG.
Every line the package owns runs for real, so this is the test that fails when the CLI
hands a dict to a JSON parser, and the one that proves the point of the exercise: an
object the model can only *describe* still comes back identified, with a picture and a
summary to confirm it.

The version of this file it replaces mocked read_image, query_vision, resolve_candidate
and format_answer — every stage it was meant to be joining up — then asserted that a
string its own mock had produced appeared in the output. It passed with all four modules
stubbed. A pipeline test that fakes both ends of the pipe tests nothing but the mocking.
"""
import http.server
import json
import threading
import urllib.parse

from click.testing import CliRunner

from visual_lookup.cli import main

THUMB = "https://upload.wikimedia.org/thumb/Pholcus.phalangioides.6905.jpg"
VISION_REPLY = json.dumps({
    "identification": "cellar spider",
    "markings": [],
    "description": "a brown spider with long thin legs"})

# The model does not answer with bare JSON, however politely the prompt asks.
VISION_CONTENT = f"According to the image, the answer is {VISION_REPLY}."


def _stub(routes: dict):
    """One server for both the POSTed vision service and the GETted lookup services."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self, body):
            payload = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self._reply(routes[self.path](None))

        def do_GET(self):
            path, _, raw = self.path.partition("?")
            self._reply(routes[path](dict(urllib.parse.parse_qsl(raw))))

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_described_object_comes_back_identified(tmp_path, monkeypatch):
    page = {"title": "Pholcus phalangioides",
            "extract": "commonly known as the cosmopolitan cellar spider",
            "index": 1, "thumbnail": {"source": THUMB}}
    server = _stub({
        "/v1/chat/completions": lambda body: {
            "choices": [{"message": {"role": "assistant", "content": VISION_CONTENT}}]},
        "/wiki/lookup": lambda query: {"result": None},
        "/w/api.php": lambda query: {"query": {"pages": {"1": page}}},
    })
    port = server.server_address[1]

    image = tmp_path / "spider.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg")
    monkeypatch.setenv("VISUAL_LOOKUP_VISION_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("JARVIS_WIKI_URL", f"http://127.0.0.1:{port}/wiki")
    monkeypatch.setenv("WIKIPEDIA_API_URL", f"http://127.0.0.1:{port}/w/api.php")

    try:
        result = CliRunner().invoke(main, [str(image), "what is this?"])
    finally:
        server.shutdown()

    assert result.exit_code == 0, result.output
    assert "Pholcus phalangioides" in result.output, result.output
    assert THUMB in result.output, result.output
    assert "cellar spider" in result.output, result.output
