#!/usr/bin/env python3
"""Mock OpenRouter server for testing vox-llm-core proxy."""

import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("mock-openrouter")


class MockHandler(BaseHTTPRequestHandler):
    def _respond(self, code: int, body: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _log_request(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        logger.info("─" * 50)
        logger.info("REQUEST RECEIVED")
        logger.info("  Headers:")
        for k, v in self.headers.items():
            logger.info(f"    {k}: {v}")
        logger.info("  Body model: %s", body.get("model", "(none)"))
        logger.info("  Messages: %s", body.get("messages", [])[:1])
        logger.info("  Has prompt field: %s", "prompt" in body)
        logger.info("  Has response_format: %s", "response_format" in body)
        return body

    def do_POST(self):
        body = self._log_request()

        # Validate authorization header
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            logger.error("  ❌ MISSING or bad Authorization header!")
            self._respond(401, {"error": "Missing or invalid Authorization header"})
            return

        logger.info("  ✅ Authorization header present")

        # Validate referer and title headers
        referer = self.headers.get("HTTP-Referer") or self.headers.get("Referer", "")
        title = self.headers.get("X-Title") or self.headers.get("X-Title", "")
        logger.info("  🔗 Referer: %s", referer or "(none)")
        logger.info("  📋 Title: %s", title or "(none)")

        model = body.get("model", "unknown")
        logger.info("  🎯 Forwarded model: %s", model)

        # Return a realistic mock response
        if self.path == "/api/v1/chat/completions":
            resp = {
                "id": "mock-chat-123",
                "object": "chat.completion",
                "created": 9999999999,
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"emotion_tag": "Thoughtful", "emotional_resonance": "Curious inquiry", "vocal_delivery_prompt": "Deliver with a thoughtful, curious tone."}'
                    },
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 42, "completion_tokens": 24, "total_tokens": 66}
            }
            self._respond(200, resp)
            logger.info("  ✅ Mock chat response sent")
        elif self.path == "/api/v1/completions":
            resp = {
                "id": "mock-completion-456",
                "object": "text_completion",
                "created": 9999999999,
                "model": model,
                "choices": [{"index": 0, "text": "Mock summary of the conversation.", "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}
            }
            self._respond(200, resp)
            logger.info("  ✅ Mock completion response sent")
        else:
            self._respond(404, {"error": f"Not found: {self.path}"})

    def do_GET(self):
        if self.path == "/api/v1/models":
            resp = {
                "object": "list",
                "data": [{"id": "sao10k/l3.1-euryale-70b", "object": "model"}]
            }
            self._respond(200, resp)
        else:
            self._respond(200, {"status": "mock-openrouter-ok"})

    def log_message(self, format, *args):
        pass  # suppress default HTTP server logging


if __name__ == "__main__":
    port = 9999
    server = HTTPServer(("127.0.0.1", port), MockHandler)
    logger.info("🎭 Mock OpenRouter listening on http://127.0.0.1:%d", port)
    server.serve_forever()