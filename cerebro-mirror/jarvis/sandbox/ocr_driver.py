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
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_ladder  # noqa: E402

IN_DIR = os.environ.get("JARVIS_OCR_IN", "/in")

# WHY THIS IS PARALLEL. OCR is one core per document and the host has 32 of them sitting
# idle — measured, 28 seconds per file on a single thread, which is about twenty-one hours
# for a 2,713-file Drive. Tesseract and poppler run as separate processes here, so this is
# a process pool rather than threads, which is also what keeps it honest: a hung tesseract
# cannot take the pool down with it.
#
# Deliberately modest by default. The box shares its memory with the brain and the
# database, and a worker holds a page image at 200 dpi; eight of those is a few hundred
# megabytes, sixteen is not. Raise it with JARVIS_OCR_WORKERS if the machine is idle.
WORKERS = int(os.environ.get("JARVIS_OCR_WORKERS", "8"))


def _one(item: dict) -> dict:
    """Extract one file. Module level so a process pool can pickle it."""
    name = item.get("path") or ""
    # Join under /in and refuse anything that escapes it. The mount is read-only, so this
    # is belt and braces rather than the only guard — but a work list is data, and data
    # should not be able to name a path outside the sandbox.
    path = os.path.normpath(os.path.join(IN_DIR, name))
    if not path.startswith(IN_DIR + os.sep):
        return {"key": item.get("key"), "text": "", "source": "",
                "error": "driver:path-escape"}
    if not os.path.exists(path):
        return {"key": item.get("key"), "text": "", "source": "",
                "error": "driver:missing-input"}
    try:
        text, source, error = ocr_ladder.extract_path(
            path, item.get("mime") or "", item.get("max_pages"))
        return {"key": item.get("key"), "text": text, "source": source, "error": error}
    except Exception as exc:                                      # noqa: BLE001
        # Never let one bad file end the batch. The caller would see a short result set,
        # which reads as "the run stopped here" — indistinguishable from a crash — so
        # every failure is reported as a row instead.
        return {"key": item.get("key"), "text": "", "source": "",
                "error": f"driver:{type(exc).__name__}"}


def _emit(out: dict) -> None:
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main() -> int:
    try:
        spec = json.load(sys.stdin)
    except ValueError as exc:
        sys.stderr.write(f"work list is not valid JSON: {exc}\n")
        return 2

    files = spec.get("files") or []
    workers = min(WORKERS, len(files)) or 1
    if workers <= 1:
        for item in files:
            _emit(_one(item))
        return 0

    # as_completed, not map: results leave as they finish rather than in submission order,
    # which means a slow document at position 3 does not hold back the eight behind it,
    # and the caller starts writing rows while the pool is still working.
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, item) for item in files]
        for fut in as_completed(futures):
            try:
                _emit(fut.result())
            except Exception as exc:                              # noqa: BLE001
                _emit({"key": None, "text": "", "source": "",
                       "error": f"driver:pool:{type(exc).__name__}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
