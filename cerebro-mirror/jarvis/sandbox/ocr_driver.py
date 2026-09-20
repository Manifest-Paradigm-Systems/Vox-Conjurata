#!/usr/bin/env python3
"""Batch front end for ocr_ladder.py, running INSIDE the OCR container.

This file is deliberately thin. The extraction logic lives in `ocr_ladder.py`, which is
copied into the image alongside this driver and imported below — one source of truth,
so the container and the host cannot disagree about what "unreadable" means. If this
file ever grows a rung of its own ladder, that has gone wrong.

WHAT IT DOES. Reads one JSON work list on stdin:

    {"files": [{"key": 0, "path": "00000.pdf", "mime": "application/pdf",
                "max_pages": 0}, ...]}

where `path` is a filename relative to /in (mounted read-only by the caller), and writes
one JSON object per line to stdout:

    {"key": 0, "text": "...", "source": "pdftotext", "error": null}

WHY JSONL RATHER THAN ONE JSON DOCUMENT. A batch of a few hundred scanned documents can
carry hundreds of megabytes of text, and a single top-level array would have to be
buffered whole before the caller could read any of it. Line-delimited output means the
caller can consume results as they arrive, and — the part that matters — a container
that dies at document 900 still leaves 899 usable results rather than nothing. A crash
mid-crawl should cost the last document, not the whole run.

`error` follows ocr_ladder's contract exactly: null means "we looked and there is
nothing there", a string means "we could not look, and here is what was missing".
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_ladder  # noqa: E402

IN_DIR = os.environ.get("JARVIS_OCR_IN", "/in")


def main() -> int:
    try:
        spec = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write(f"work list is not valid JSON: {exc}\n")
        return 2

    files = spec.get("files") or []
    for item in files:
        name = item.get("path") or ""
        # Join under /in and refuse anything that escapes it. The mount is read-only,
        # so this is belt and braces rather than the only guard — but a work list is
        # data, and data should not be able to name a path outside the sandbox.
        path = os.path.normpath(os.path.join(IN_DIR, name))
        if not path.startswith(IN_DIR + os.sep):
            out = {"key": item.get("key"), "text": "", "source": "",
                   "error": "driver:path-escape"}
        elif not os.path.exists(path):
            out = {"key": item.get("key"), "text": "", "source": "",
                   "error": "driver:missing-input"}
        else:
            try:
                text, source, error = ocr_ladder.extract_path(
                    path, item.get("mime") or "", item.get("max_pages"))
                out = {"key": item.get("key"), "text": text,
                       "source": source, "error": error}
            except Exception as exc:                              # noqa: BLE001
                # Never let one bad file end the batch. The caller would see a short
                # result set, which reads as "the run stopped here" — indistinguishable
                # from a crash — so every failure is reported as a row instead.
                out = {"key": item.get("key"), "text": "", "source": "",
                       "error": f"driver:{type(exc).__name__}"}

        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
