"""End to end: the real CLI, the real functions, one stub vision service.

Everything the package owns runs for real — read_image, build_prompt, query_vision's
HTTP call, parse_vision_response, resolve_candidate, format_answer. The only thing
faked is the remote vision model, which is the one piece that cannot be present here:
verifies run with --network=none, so the model is replaced by a loopback server rather
than by patching our own functions.

That distinction is the entire point of this file. The component tests all passed while
the assembled CLI crashed on every single run, because each of them mocked the stage
next door.
"""
import http.server
import json
import threading

from click.testing import CliRunner

from visual_lookup.cli import main

REPLY = "markings: LM2596S"


def _stub_vision(reply: str):
    """Stand-in for the vision service, speaking the same HTTP shape."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json.dumps({
                "choices": [{"message": {"role": "assistant", "content": reply}}]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run_cli(tmp_path, monkeypatch, url):
    image = tmp_path / "part.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg")
    monkeypatch.setenv("VISUAL_LOOKUP_VISION_URL", url)
    return CliRunner().invoke(main, [str(image), "what is this?"])


def test_cli_answers_end_to_end(tmp_path, monkeypatch):
    server = _stub_vision(REPLY)
    try:
        result = _run_cli(tmp_path, monkeypatch,
                          f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()

    assert result.exit_code == 0, result.output
    assert "LM2596S" in result.output, result.output


def test_vision_service_down_is_loud(tmp_path, monkeypatch):
    """A dead vision service must not read as an object with no markings."""
    result = _run_cli(tmp_path, monkeypatch, "http://127.0.0.1:1")
    assert result.exit_code != 0, result.output
