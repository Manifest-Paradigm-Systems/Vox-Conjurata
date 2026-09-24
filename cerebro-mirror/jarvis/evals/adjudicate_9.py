"""The 9 stably-wrong keys, laid out for a human to judge.

WHY THIS EXISTS. Every automatic test I could build agrees the lane is wrong on these 9:
the right FACT is in the prompt, correctly labelled; the gate does not refuse; and
`grade()`'s verdict survives a punctuation-normalised re-test. But two of my own probes
DISAGREE about how close the answers are -- one finds `account_class`'s value words
contiguous and in order, the other finds them scattered -- and the disagreement is about
stopword handling, which cannot be settled without reading the answer.

Reading the answers is not something a hosted-model session may do: the exposure is the
transcript, not the query. So the judgement is the owner's, and this script exists to make
it a five-minute read instead of a session. It writes ONE file on this box, mode 0600, and
prints nothing but that file's path.

WHAT IT DOES NOT DO. It does not call a model, score anything, or decide. It asks each
question once, prints what the model said next to what the ledger holds and what retrieval
actually put in front of it, and leaves a blank for the verdict.

    python3 adjudicate_9.py                  # the keys repeat_eval last found stably wrong
    python3 adjudicate_9.py --keys end_date,reason
    python3 adjudicate_9.py --from-report ~/jarvis-db-backups/repeat_eval-XXXX.json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "google"))

import auto_eval as AE  # noqa: E402
import lane_eval as LE  # noqa: E402
import document_search as DS  # noqa: E402

BACKUPS = os.path.expanduser("~/jarvis-db-backups")
GOOGLE_DB = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                              "~/jarvis/google/google.db"))

# Fallback only: used when no repeat_eval report is available to read them from.
KNOWN_WRONG = ["RCSBP", "account_class", "demob_station", "deployment_location",
               "discharge_or_transfer", "end_date", "form_number", "reason",
               "reporting_location"]


def stably_wrong_from_report(path: str | None = None) -> list[str]:
    """The keys the last repeated run found wrong on EVERY pass.

    Read from the report rather than hardcoded so this script follows the measurement
    instead of drifting away from it.
    """
    if not path:
        try:
            cands = sorted(f for f in os.listdir(BACKUPS) if f.startswith("repeat_eval-"))
        except OSError:
            return list(KNOWN_WRONG)
        if not cands:
            return list(KNOWN_WRONG)
        path = os.path.join(BACKUPS, cands[-1])
    try:
        with open(path) as fh:
            matrix = json.load(fh)["matrix"]
    except (OSError, KeyError, ValueError) as e:
        print(f"  ! could not read {path}: {e}", file=sys.stderr)
        return list(KNOWN_WRONG)
    keys = [k for k in sorted(matrix) if set(matrix[k]) == {"wrong"}]
    return keys or list(KNOWN_WRONG)


def window(conn, question: str) -> list[dict]:
    """Exactly what run_ask would have been shown, in the order it gets it."""
    hits, _err, _notes = DS.search_archive(conn, question, account=None, limit=6)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", default="")
    ap.add_argument("--from-report", default="")
    ap.add_argument("--model", default=AE.MODEL)
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()

    keys = ([k.strip() for k in a.keys.split(",") if k.strip()] if a.keys
            else stably_wrong_from_report(a.from_report or None))
    truth = {k: v for k, v, s in AE.ground_truth()}

    conn = sqlite3.connect(f"file:{GOOGLE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(BACKUPS, f"adjudicate-{stamp}.md")

    # Created 0600 BEFORE anything is written, so the file is never briefly readable.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    lines: list[str] = []
    w = lines.append

    w("# Adjudication — the keys that were wrong on every pass\n")
    w(f"run {stamp}   lane `{a.model}`   {len(keys)} key(s)\n")
    w("For each entry: does the model's answer actually convey the ledger's value?\n")
    w("Tick **correct** if it does (the grader is then too strict and is the thing to fix),")
    w("**wrong** if it does not (the lane is genuinely failing). A note on any entry helps.\n")
    w("> This file is 0600 and stays on this machine. Nothing here is printed to a terminal.\n")
    w("\n---\n")

    for key in keys:
        q = AE.schema_question(key)
        val = truth.get(key, "")
        try:
            ans = LE.ask(AE.BRAIN, a.model, LE.SYSTEM_BLIND, q, timeout=a.timeout)
        except Exception as e:  # noqa: BLE001 - a dead lane must not lose the packet
            ans = f"__ERROR__ {e}"
        verdict = AE.grade(ans, val)

        w(f"\n## `{key}`\n")
        w(f"**eval verdict:** `{verdict}`\n")
        w(f"**question the eval asks:** {q}\n")
        w(f"**the ledger's value:**\n\n    {val}\n")
        w("**the model's answer:**\n")
        w("\n".join(f"> {ln}" for ln in (ans or "(empty)").splitlines()) + "\n")

        try:
            hits = window(conn, q)
        except Exception as e:  # noqa: BLE001
            hits = []
            w(f"\n_(retrieval re-run failed: {e})_\n")

        facts = [h for h in hits if h.get("source") == "fact"]
        others = [h for h in hits if h.get("source") != "fact"]
        w("**what retrieval put in front of it — FACT lines (tier 0):**\n")
        if facts:
            for h in facts:
                mark = " <-- the one asked for" if h["id"] == f"fact:{key}" else ""
                w(f"    {h.get('name') or ''}{mark}")
                w(f"      {h.get('snip') or ''}")
        else:
            w("    (no fact tier hit for this question)\n")

        if others:
            w("\n**the document tier that came with it:**\n")
            for h in others:
                w(f"    [{h.get('source')}] {(h.get('name') or '')[:90]}")

        w("\n**verdict:**  [ ] correct   [ ] wrong\n")
        w("**note:**\n")
        w("\n---\n")

    os.write(fd, "\n".join(lines).encode())
    os.close(fd)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
