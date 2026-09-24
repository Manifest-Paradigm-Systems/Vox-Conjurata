"""The aggregation that turns a matrix of verdicts into a statement about noise.

WHY THIS EXISTS. `repeat_eval.summarize()` decides what the repeated-run number MEANS —
the spread, the mean, and which keys flapped. That arithmetic is the part a reader will
trust when they decide whether a lane change helped, so it is pinned here rather than
checked by eye on a run whose input changes every time.

EVERY VERDICT HERE IS INVENTED. No question, key, value or answer from the owner's record
appears in this file: the input is a dict of strings, and `summarize` never sees a value.
That is also why these tests need no lane, no database and no token.

    python3 evals/test_repeat_eval.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import repeat_eval as RE  # noqa: E402

CHECKS = []


def check(what, got, want):
    CHECKS.append((what, got, want))


def close(a, b, tol=1e-6):
    return abs(a - b) < tol


# --- nothing to summarize --------------------------------------------------------------
empty = RE.summarize({})
check("empty: passes", empty["passes"], 0)
check("empty: keys", empty["keys"], 0)
check("empty: rate_mean", empty["rate_mean"], 0.0)
check("empty: no flapping", empty["flapping"], {})
check("empty: noise floor", empty["noise_floor_points"], 0.0)

# --- a lane nobody could argue about ---------------------------------------------------
steady = RE.summarize({"a": ["correct"] * 3, "b": ["correct"] * 3, "c": ["correct"] * 3})
check("steady: rate", round(steady["rate_mean"], 1), 100.0)
check("steady: spread is zero", steady["rate_spread"], 0.0)
check("steady: nothing flaps", steady["flapping"], {})
check("steady: all stable correct", len(steady["stable"]["correct"]), 3)

# --- one key that changes its mind -----------------------------------------------------
# pass rates 100, 50, 100 -> mean 83.33, spread 50, pstdev sqrt(1666.67/3) = 23.5702
flappy = RE.summarize({"a": ["correct", "wrong", "correct"], "b": ["correct"] * 3})
check("flappy: mean", round(flappy["rate_mean"], 2), 83.33)
check("flappy: min", flappy["rate_min"], 50.0)
check("flappy: max", flappy["rate_max"], 100.0)
check("flappy: spread", flappy["rate_spread"], 50.0)
check("flappy: stdev", round(flappy["rate_stdev"], 4), 23.5702)
check("flappy: the key is named", sorted(flappy["flapping"]), ["a"])
check("flappy: with its sequence", flappy["flapping"]["a"], ["correct", "wrong", "correct"])
check("flappy: the steady key is not", "b" in flappy["flapping"], False)
check("flappy: steady key counted stable", flappy["stable"]["correct"], ["b"])

# --- the noise floor IS the spread, by definition --------------------------------------
check("noise floor == spread", flappy["noise_floor_points"], flappy["rate_spread"])

# --- every verdict is counted, not just correct ----------------------------------------
mixed = RE.summarize({"a": ["refused"], "b": ["wrong"], "c": ["error"], "d": ["correct"]})
check("mixed: n", mixed["per_pass"][0]["n"], 4)
check("mixed: refused", mixed["per_pass"][0]["refused"], 1)
check("mixed: wrong", mixed["per_pass"][0]["wrong"], 1)
check("mixed: error", mixed["per_pass"][0]["error"], 1)
check("mixed: rate", mixed["per_pass"][0]["rate"], 25.0)

# --- a refusal that becomes an answer is a flapping key --------------------------------
flip_kind = RE.summarize({"a": ["refused", "correct", "correct"]})
check("refused->correct flaps", sorted(flip_kind["flapping"]), ["a"])
check("refused->correct is not stable",
      flip_kind["stable"]["refused"], [])

# --- a key missing from a pass must not silently become a wrong answer -----------------
# (a dropped request is not evidence the lane got it wrong; it is evidence of a bug, so
#  the pass narrows its own denominator instead of counting a phantom failure.)
ragged = RE.summarize({"a": ["correct", "correct"], "b": ["correct"]})
check("ragged: second pass has its own n", ragged["per_pass"][1]["n"], 1)
check("ragged: second pass still 100", ragged["per_pass"][1]["rate"], 100.0)
check("ragged: keys counted once", ragged["keys"], 2)

# --- ONE PASS CANNOT MEASURE NOISE, and the summary must not pretend otherwise ----------
single = RE.summarize({"a": ["correct"], "b": ["wrong"], "c": ["correct"], "d": ["correct"]})
check("single: rate", single["per_pass"][0]["rate"], 75.0)
check("single: spread is 0", single["rate_spread"], 0.0)
check("single: stdev is 0", single["rate_stdev"], 0.0)
check("single: passes recorded", single["passes"], 1)

# --- summarize() must not have mutated what it was handed ------------------------------
given = {"a": ["correct", "wrong"]}
RE.summarize(given)
check("input left alone", given, {"a": ["correct", "wrong"]})

fails = 0
for what, got, want in CHECKS:
    ok = close(got, want) if isinstance(want, float) and isinstance(got, float) else got == want
    fails += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {what}")
    if not ok:
        print(f"          got  {got!r}\n          want {want!r}")

if len(CHECKS) != 34:
    print(f"  FAIL  {len(CHECKS)} checks ran, 34 expected")
    fails += 1

print(f"\n  {len(CHECKS) - fails}/{len(CHECKS)} checks pass")
sys.exit(1 if fails else 0)
