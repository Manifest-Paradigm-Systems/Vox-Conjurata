"""The same question set, asked N times, reported as a distribution.

WHY THIS EXISTS. `auto_eval.py` reports one number from one pass, and that number moves.
Measured 2026-09-23: two consecutive full passes of the SAME instrument with the SAME
grader gave 86.2% and 75.9% — five keys apart. A single-pass rate cannot tell an
improvement from the instrument's own jitter, so every A/B this project has run on the
lane (the alias map, the grader fix, the tier changes) was read against a ruler with no
graduations.

WHAT IT ADDS. Nothing about accuracy — it is the same questions and the same grader. It
adds the two things a single pass cannot give:

    the spread   mean, min, max and stdev of the per-pass correct rate
    the flappers which keys changed verdict between passes, and in what order

The spread IS the noise floor. A difference smaller than it is not evidence of anything,
which is the sentence the report prints so nobody has to remember it.

THE ROOT CAUSE THIS MEASURED, for the record. The lane was not merely "noisy at
temperature 0.0" — `temperature: 0.0` never reached the sampler. `chat_completions` never
read the field, and `_local_answer` called `_chat` without a temperature, so the `actor`
default of 0.7 applied to every request either eval ever made. That is fixed in brain.py
(`_req_temperature` + `temperature=temperature` threaded through `run_ask`,
`_answer_grounded` and both of its attempts). This script is how the fix is checked, and
how a future change is judged once the lane is quiet.

PRIVACY. Same discipline as `auto_eval.py`: values are read from the DB, never printed,
never written to the report, never sent anywhere but the local brain. The report holds key
names and verdicts only.

Usage:
    python3 repeat_eval.py --passes 3              # the full set, three times
    python3 repeat_eval.py --passes 5 --keys RCSBP,form_version,purpose
    python3 repeat_eval.py --passes 3 --limit 12
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import auto_eval as AE  # noqa: E402

STATUSES = ("correct", "refused", "wrong", "error")


def summarize(matrix: dict[str, list[str]]) -> dict:
    """The whole aggregate, from `key -> [verdict per pass]`. Pure, so the tests can pin it.

    Takes the matrix rather than the raw answers on purpose: the arithmetic that decides
    what the number MEANS is the part worth testing, and it should not require a lane call
    to exercise.
    """
    keys = sorted(matrix)
    if not keys:
        return {"passes": 0, "keys": 0, "per_pass": [], "rate_mean": 0.0, "rate_min": 0.0,
                "rate_max": 0.0, "rate_spread": 0.0, "rate_stdev": 0.0, "flapping": {},
                "stable": {s: [] for s in STATUSES}, "noise_floor_points": 0.0}

    passes = max(len(v) for v in matrix.values())
    per_pass = []
    for i in range(passes):
        col = [matrix[k][i] for k in keys if i < len(matrix[k])]
        n = len(col)
        counts = {s: sum(1 for v in col if v == s) for s in STATUSES}
        per_pass.append({"pass": i + 1, "n": n, **counts,
                         "rate": (100.0 * counts["correct"] / n) if n else 0.0})

    rates = [p["rate"] for p in per_pass]
    spread = (max(rates) - min(rates)) if rates else 0.0
    return {
        "passes": passes,
        "keys": len(keys),
        "per_pass": per_pass,
        "rate_mean": statistics.fmean(rates) if rates else 0.0,
        "rate_min": min(rates) if rates else 0.0,
        "rate_max": max(rates) if rates else 0.0,
        "rate_spread": spread,
        "rate_stdev": statistics.pstdev(rates) if len(rates) > 1 else 0.0,
        "flapping": {k: matrix[k] for k in keys if len(set(matrix[k])) > 1},
        "stable": {s: [k for k in keys if set(matrix[k]) == {s}] for s in STATUSES},
        # Named rather than left implicit: this is the smallest difference worth believing.
        "noise_floor_points": spread,
    }


def report(summary: dict, model: str, elapsed: float) -> None:
    n = summary["keys"]
    print("\n" + "=" * 72)
    print("REPEATED RETRIEVAL ACCURACY — the same questions, asked again and again")
    print("=" * 72)
    if not n:
        print("\n  no questions ran")
        return
    print(f"\n  lane {model}   {summary['passes']} pass(es) over {n} question(s)"
          f"   {elapsed:.0f}s")

    print(f"\n  {'pass':<6}{'correct':>9}{'refused':>9}{'wrong':>7}{'error':>7}{'rate':>9}")
    for p in summary["per_pass"]:
        print(f"  {p['pass']:<6}{p['correct']:>9}{p['refused']:>9}{p['wrong']:>7}"
              f"{p['error']:>7}{p['rate']:>8.1f}%")

    print(f"\n  correct rate   {summary['rate_mean']:.1f}% mean   "
          f"[{summary['rate_min']:.1f}, {summary['rate_max']:.1f}]   "
          f"spread {summary['rate_spread']:.1f} pts   "
          f"stdev {summary['rate_stdev']:.2f}")

    flap = summary["flapping"]
    print(f"\n  stable: {len(summary['stable']['correct'])} always correct, "
          f"{len(summary['stable']['wrong'])} always wrong, "
          f"{len(summary['stable']['refused'])} always refused")
    print(f"  FLAPPING: {len(flap)} of {n}")
    if flap:
        for k in sorted(flap):
            print(f"     {k:<38} {' '.join(flap[k])}")

    if summary["passes"] > 1:
        print(f"\n  THE NOISE FLOOR. This instrument moves {summary['rate_spread']:.1f} points "
              f"between passes on identical input.")
        print("  Any difference smaller than that is the instrument, not the system. A change")
        print("  to the lane must be judged on the mean of several passes, never on one.")
    else:
        print("\n  ONE PASS ONLY — this gives a number, not a spread. Run --passes 3+ before")
        print("  comparing anything against it.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="first N strong keys only")
    ap.add_argument("--keys", default="", help="comma-separated key names, overrides --limit")
    ap.add_argument("--model", default=AE.MODEL)
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()

    truth = AE.ground_truth()
    strong = [(k, v) for k, v, s in truth if s]
    # Short values are dropped here, once, so every pass scores the SAME questions.
    strong = [(k, v) for k, v in strong if len(AE.norm(v)) > AE.SHORT_VALUE]

    if a.keys:
        want = {k.strip() for k in a.keys.split(",") if k.strip()}
        strong = [(k, v) for k, v in strong if k in want]
        missing = want - {k for k, _ in strong}
        if missing:
            print(f"  ! not in the strong set, skipped: {sorted(missing)}")
    elif a.limit:
        strong = strong[:a.limit]

    n = len(strong)
    total = n * a.passes
    print(f"ground truth: {len(truth)} current keys — {n} usable, {len(truth) - n} excluded")
    print(f"lane: {a.model} at {AE.BRAIN}")
    print(f"asking {n} question(s) x {a.passes} pass(es) = {total} request(s)\n")

    matrix: dict[str, list[str]] = {k: [] for k, _ in strong}
    started = time.time()
    done = 0
    for i in range(a.passes):
        for key, val in strong:
            ans = AE.LE.ask(AE.BRAIN, a.model, AE.LE.SYSTEM_BLIND, AE.schema_question(key),
                            timeout=a.timeout)
            matrix[key].append(AE.grade(ans, val))
            done += 1
            print(f"  [{done:>4}/{total}] pass {i + 1}  {key:<38} {matrix[key][-1]}")

    summary = summarize(matrix)
    report(summary, a.model, time.time() - started)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.abspath(os.path.expanduser(f"~/jarvis-db-backups/repeat_eval-{stamp}.json"))
    try:
        with open(path, "w") as fh:
            os.chmod(path, 0o600)
            json.dump({"stamp": stamp, "model": a.model, "passes": a.passes,
                       "summary": summary, "matrix": matrix}, fh, indent=1)
        print(f"\n  report (key names and verdicts only): {path}")
    except OSError as e:
        print(f"\n  report not written: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
