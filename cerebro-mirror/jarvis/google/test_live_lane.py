"""Prove Jarvis answers FROM THE DATABASE, not from the model's memory.

The load-bearing signal is the SSE `sources` frame. It carries the exact rows the brain
handed to the model, and `_local_answer` only ever receives those rows — so if a `doc:`
source is in that frame, the answer was grounded in a document from the index. Nothing
in the reply can have come from anywhere else.

WHAT IS ASSERTED STRICTLY AND WHAT IS NOT. This distinction is the difference between a
test that keeps working and one that gets "fixed" into uselessness:

  * The SOURCE assertion is strict — a specific document id must appear in the sources
    frame. That is the claim being tested, and it is deterministic.
  * The TEXT assertion is loose — the answer should mention the fact, but wording varies
    run to run because a model writes it. It is a smoke signal, not the gate. Do not
    tighten it.

THE NEGATIVE CONTROL IS THE POINT. The same question goes to the plain conversationalist
(`model: jarvis`), which has no fetching lane and returns no sources frame. If that
answer also happens to be right, that is exactly why the sources frame is the gate — a
model can be right from memory about a person it has read a thousand documents about.
The control proves the test can tell the two apart.

Usage:
    python3 test_live_lane.py --model jarvis-drive \
        --question "what is my rank" \
        --expect-source doc: --expect-in-source "ORD PROMRED" \
        --expect-text "MAJ" --control jarvis
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BRAIN = "http://127.0.0.1:8092"

fails: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"   <- {detail}"))
    if not cond:
        fails.append(name)


def ask(model: str, question: str, timeout: int = 300) -> tuple[dict, str, list]:
    """One streamed turn. Returns (frames_seen, answer_text, sources)."""
    body = json.dumps({
        "model": model,
        "stream": True,
        "messages": [{"role": "user", "content": question}],
    }).encode()
    req = urllib.request.Request(f"{BRAIN}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    text, sources, kinds = "", [], set()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            # The stream interleaves `: keepalive` COMMENT lines with `data:` frames.
            # Reading a comment as JSON is a silent way to see no frames at all.
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload.strip() == "[DONE]":
                break
            try:
                d = json.loads(payload)
            except ValueError:
                continue
            if "sources" in d:
                kinds.add("sources")
                sources = d["sources"] or []
            if "timings" in d:
                kinds.add("timings")
            for ch in (d.get("choices") or []):
                piece = (ch.get("delta") or {}).get("content")
                if piece:
                    kinds.add("content")
                    text += piece
    return {"kinds": kinds, "sources": sources}, text, sources


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="jarvis-drive")
    p.add_argument("--question", required=True)
    p.add_argument("--expect-source", default="doc:",
                   help="prefix a sources[].url must have, e.g. doc: or mail:")
    p.add_argument("--expect-in-source", default="",
                   help="substring that must appear in some source title")
    p.add_argument("--expect-text", default="",
                   help="loose: substring the answer should mention")
    p.add_argument("--control", default="", help="a model with no fetching lane")
    args = p.parse_args()

    print(f"\n=== {args.model}: {args.question!r} ===")
    meta, answer, sources = ask(args.model, args.question)
    print(f"  frames: {sorted(meta['kinds'])}")
    print(f"  answer: {answer[:300]}")
    print("  sources:")
    for s in sources:
        print(f"    {s.get('url','')[:64]:<64} {(s.get('title') or '')[:56]}")

    # (a) the lane returned a sources frame at all
    check("a sources frame arrived", "sources" in meta["kinds"])
    # (b) STRICT: a source names a document from the index
    urls = [s.get("url", "") for s in sources]
    grounded = [u for u in urls if u.startswith(args.expect_source)]
    check(f"a source is {args.expect_source!r} (read from the index)",
          bool(grounded), f"urls were {urls[:4]}")
    if args.expect_in_source:
        titles = " ".join((s.get("title") or "") for s in sources)
        check(f"a source names {args.expect_in_source!r}",
              args.expect_in_source.lower() in titles.lower(), f"titles: {titles[:200]}")
    # (c) LOOSE: the model used it. Wording varies; this is a smoke signal, not the gate.
    if args.expect_text:
        check(f"the answer mentions {args.expect_text!r} (loose)",
              args.expect_text.lower() in answer.lower())

    if args.control:
        print(f"\n=== control: {args.control} (no fetching lane) ===")
        cmeta, canswer, csources = ask(args.control, args.question)
        print(f"  frames: {sorted(cmeta['kinds'])}")
        print(f"  answer: {canswer[:200]}")
        check("the control returns NO sources frame",
              "sources" not in cmeta["kinds"],
              "if this fails the control is not a control")
        check("the control is not grounded in a document",
              not [u for u in csources if u.startswith("doc:")])

    print()
    print("PASS" if not fails else f"FAIL: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
