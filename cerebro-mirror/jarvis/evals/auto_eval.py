"""The automated question set: a real retrieval number, with no human in the loop.

WHY THIS EXISTS. The 13-question lane eval cannot see the thing that is actually wrong.
Its questions were mined from the owner's turns but written in near-schema wording, so
they measure retrieval on a phrasing nobody uses. The owner's report is "most every
question wrong", and the honest response to that is a number over many questions, phrased
BOTH ways, that nobody has to sit and grade.

WHERE THE LABELS COME FROM. `records_facts` holds 81 keys with a single current value
each — the owner's military record, already extracted. That is free ground truth: no
human labels anything, and a question is correct exactly when the answer contains the
value that is in the record. A date is the exception: it is stored ISO and answered in
the documents' own military convention, so dates are compared as DATES, not as strings.
See `states_date` for why that comparison is anchored.

NOT ALL OF IT IS GROUND TRUTH. 63 of the 81 carry both a `source_name` and a non-zero
`evidence_count`. Two carry NEITHER — `blood_type` and `home_state` — and `blood_type` is
the very key the hand-written eval assumes is UNANSWERABLE (class B) and scores a supplied
value as INVENTED. Meanwhile the grounded lane answers it with the value that is in the
fact tier, so the two instruments contradict each other about the same question. A rate
built on those two rows would be meaningless, so they are excluded and reported apart.

TWO PHRASINGS, AND THE GAP BETWEEN THEM IS THE POINT:

    schema   "what is my date of rank"          — the words in the record
    owner    the owner's own question, verbatim, mined from his turns

The gap between those two rates is the number that explains "most every question wrong",
and it is invisible to any eval written in schema words.

PRIVACY. Values are read from the DB and never printed, never written to the report, and
never sent anywhere but the local brain. Only key names, verdicts and counts appear in
the output. The report file holds key names and verdicts only.

Usage:
    python3 auto_eval.py --limit 8      # pilot
    python3 auto_eval.py                # the whole set
    python3 auto_eval.py --phrasing owner
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BRAIN = os.getenv("EVAL_BRAIN_URL", "http://127.0.0.1:8092")
GOOGLE_DB = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB", "~/jarvis/google/google.db"))
CONV_DB = os.path.expanduser(os.environ.get("JARVIS_CONV_DB", "~/jarvis/conversations.db"))
MODEL = os.getenv("EVAL_MODEL", "jarvis-ask")

# The two keys with no provenance anywhere in the record. Excluded from the rate and
# reported on their own, because one of them is the class-B question above.
NO_PROVENANCE = {"blood_type", "home_state"}
# A value this short matches by accident inside unrelated text ("in", "VA", "62"), so it is
# graded on whole-token boundaries and counted separately rather than in the headline.
SHORT_VALUE = 3


def _lane():
    """Reuse lane_eval's ask() so both instruments send byte-identical requests."""
    spec = importlib.util.spec_from_file_location("lane_eval", os.path.join(HERE, "lane_eval.py"))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


LE = _lane()
REFUSAL = LE.REFUSAL


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


# ------------------------------------------------------------------ dates are values too
# THE GRADER WAS BLIND TO THE CONVENTION THE RECORD ITSELF USES. A date is stored ISO
# (`1974-05-01`), but these are service records and the model answers the way the
# documents write it: "1 May 1974", sometimes "1st May 1974". A substring test cannot see
# that, so it scored correct answers WRONG — measured 2026-09-23: 13 of 23 wrong verdicts
# on the military record were this, and the headline read 67.1% when the true figure was
# 85.7%. The instrument's own founding anecdote (lane_eval.py line 9) is `"2nd August
# 1974"`, so the format was known and the comparison was still raw.
#
# ANCHORED, WHICH MATTERS MORE THAN IT LOOKS. A plain substring search for "1 May 1974"
# also matches inside "31 May 1974", so the naive fix would have converted a false WRONG
# into a false CORRECT. Every extractor below is boundary-anchored and the result is
# compared as a (year, month, day) TUPLE, so a near-miss date cannot pass.
_MON3 = {m[:3]: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}

_D_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_D_DMY = re.compile(r"(?<![\w])(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?,?\s+(\d{4})(?!\d)")
_D_MDY = re.compile(r"(?<![\w])([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)")
_D_NUM = re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)")


def _as_date(value: str):
    """(y, m, d) if the record's value IS an ISO date, else None."""
    m = _D_ISO.fullmatch((value or "").strip())
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    return (y, mo, d) if 1 <= mo <= 12 and 1 <= d <= 31 else None


def states_date(answer_low: str, want: tuple) -> bool:
    """Does the answer state exactly this date, in any convention? Anchored throughout."""
    for m in _D_ISO.finditer(answer_low):
        if tuple(int(g) for g in m.groups()) == want:
            return True
    for m in _D_DMY.finditer(answer_low):
        mo = _MON3.get(m.group(2)[:3])
        if mo and (int(m.group(3)), mo, int(m.group(1))) == want:
            return True
    for m in _D_MDY.finditer(answer_low):
        mo = _MON3.get(m.group(1)[:3])
        if mo and (int(m.group(3)), mo, int(m.group(2))) == want:
            return True
    for m in _D_NUM.finditer(answer_low):
        a, b, y = (int(g) for g in m.groups())
        # Month/day order is genuinely ambiguous in numeric form. US order is taken as
        # read; day-first is accepted ONLY when the first number cannot be a month, so
        # "13/5/1974" is 13 May while "5/1/1974" is never silently flipped to 1 May.
        if (y, a, b) == want or (a > 12 and (y, b, a) == want):
            return True
    return False


def grade(answer: str, value: str) -> str:
    """correct | refused | wrong. Deterministic — no model, no human."""
    if answer.startswith("__ERROR__"):
        return "error"
    low = norm(answer)
    want = norm(value)
    if not want:
        return "error"
    if len(want) > SHORT_VALUE:
        hit = want in low or (len(digits(want)) >= 5 and digits(want) in digits(low))
        if not hit:
            wd = _as_date(want)
            if wd:
                hit = states_date(low, wd)
    else:
        hit = re.search(r"(?<!\w)" + re.escape(want) + r"(?!\w)", low) is not None
    if hit:
        return "correct"
    return "refused" if REFUSAL.search(answer) else "wrong"


def ground_truth():
    """(key, value, strong?) for every current key. Values never leave this function."""
    g = sqlite3.connect(GOOGLE_DB)
    g.row_factory = sqlite3.Row
    out = []
    for r in g.execute("SELECT key_name, value, source_name, evidence_count FROM records_facts"
                       " WHERE is_current=1 ORDER BY key_name"):
        val = (r["value"] or "").strip()
        if not val:
            continue
        strong = bool(r["source_name"]) and (r["evidence_count"] or 0) > 0
        out.append((r["key_name"], val, strong))
    return out


def owner_turns():
    """The owner's real questions, with the eval's own runs excluded."""
    eval_q = {(q.get("q") or "").strip().lower() for q in LE.QUESTIONS}
    c = sqlite3.connect(CONV_DB)
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT session, role, content FROM turns").fetchall()
    bad = {t["session"] for t in rows
           if t["role"] == "user" and (t["content"] or "").strip().lower() in eval_q}
    return [t["content"].strip() for t in rows
            if t["role"] == "user" and t["session"] not in bad
            and t["content"] and 5 < len(t["content"].strip()) < 300]


def mine_owner_question(key: str, turns: list[str]) -> str | None:
    """The owner's own phrasing for this key, or None.

    Prefers a real question (he asked it with a question mark) and the SHORTEST such turn,
    because the long ones are essays that happen to mention the field.
    """
    toks = [w for w in re.findall(r"[a-z0-9]{3,}", key.lower())]
    if not toks:
        return None
    need = max(1, len(toks) - 1)
    hits = []
    for t in turns:
        low = t.lower()
        n = sum(1 for w in toks if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", low))
        if n >= need:
            hits.append(t)
    if not hits:
        return None
    questions = [t for t in hits if t.rstrip().endswith("?")]
    pool = questions or hits
    return min(pool, key=len)


def schema_question(key: str) -> str:
    phrase = key.replace("_", " ").strip()
    phrase = re.sub(r"^(the)\s+", "", phrase, flags=re.I)
    return f"what is my {phrase}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--phrasing", choices=["both", "schema", "owner"], default="both")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()

    truth = ground_truth()
    strong_all = [(k, v) for k, v, s in truth if s]
    weak = [(k, v) for k, v, s in truth if not s]
    strong = strong_all[:a.limit] if a.limit else strong_all

    turns = owner_turns()
    print(f"ground truth: {len(truth)} current keys — {len(strong_all)} strong, {len(weak)} weak")
    if a.limit:
        print(f"  --limit {a.limit}: using the first {len(strong)} of them")
    print(f"  excluded for no provenance: {[k for k, _ in weak] or 'none'}")
    print(f"  owner turns available for mining: {len(turns)}")
    print(f"  lane: {a.model} at {BRAIN}\n")

    jobs = []
    for key, val in strong:
        if a.phrasing in ("both", "schema"):
            jobs.append(("schema", key, val, schema_question(key)))
        if a.phrasing in ("both", "owner"):
            q = mine_owner_question(key, turns)
            if q:
                jobs.append(("owner", key, val, q))
    print(f"asking {len(jobs)} question(s)\n")

    results = []
    for i, (phrasing, key, val, q) in enumerate(jobs, 1):
        ans = LE.ask(BRAIN, a.model, LE.SYSTEM_BLIND, q, timeout=a.timeout)
        verdict = grade(ans, val)
        results.append({"phrasing": phrasing, "key": key, "verdict": verdict,
                        "answer_chars": len(ans)})
        flag = "" if len(norm(val)) > SHORT_VALUE else "  (short value, weak label)"
        print(f"  [{i:>3}/{len(jobs)}] {phrasing:<6} {key:<38} {verdict}{flag}")

    print("\n" + "=" * 72)
    print("RETRIEVAL ACCURACY — value from records_facts found in the answer")
    print("=" * 72)
    # Short values are dropped HERE, once, so the per-phrasing rates and the gap are
    # computed over the SAME questions -- otherwise the two sections disagree by
    # whichever keys happened to carry a short value.
    val_of = dict(strong_all)
    solid = [r for r in results if len(norm(val_of.get(r["key"], ""))) > SHORT_VALUE]
    n_short = len(results) - len(solid)
    for phrasing in ("schema", "owner"):
        sub = [r for r in solid if r["phrasing"] == phrasing]
        if not sub:
            continue
        n = len(sub)
        ok = sum(1 for r in sub if r["verdict"] == "correct")
        rf = sum(1 for r in sub if r["verdict"] == "refused")
        wr = sum(1 for r in sub if r["verdict"] == "wrong")
        er = sum(1 for r in sub if r["verdict"] == "error")
        if not n:
            continue
        print(f"\n  {phrasing.upper():<8} n={n}")
        print(f"    correct  {ok:>3}  {100 * ok / n:>5.1f}%")
        print(f"    refused  {rf:>3}  {100 * rf / n:>5.1f}%")
        print(f"    wrong    {wr:>3}  {100 * wr / n:>5.1f}%")
        if er:
            print(f"    error    {er:>3}  {100 * er / n:>5.1f}%")

    both = {r["key"] for r in solid if r["phrasing"] == "schema"} & \
           {r["key"] for r in solid if r["phrasing"] == "owner"}
    if both:
        sc = {r["key"]: r["verdict"] for r in solid if r["phrasing"] == "schema"}
        oc = {r["key"]: r["verdict"] for r in solid if r["phrasing"] == "owner"}
        s_ok = sum(1 for k in both if sc[k] == "correct")
        o_ok = sum(1 for k in both if oc[k] == "correct")
        print(f"\n  THE PHRASING GAP — the same {len(both)} keys asked both ways")
        print(f"    schema wording  {s_ok:>3}/{len(both)}  {100 * s_ok / len(both):>5.1f}%")
        print(f"    owner wording   {o_ok:>3}/{len(both)}  {100 * o_ok / len(both):>5.1f}%")
        print(f"    gap             {100 * (s_ok - o_ok) / len(both):>+5.1f} points")
    if n_short:
        print(f"\n  {n_short} question(s) excluded from the rates above: values of "
              f"{SHORT_VALUE} chars or fewer match by accident.")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.expanduser(f"~/jarvis-db-backups/auto_eval-{stamp}.json")
    try:
        with open(os.path.abspath(path), "w") as fh:
            os.chmod(os.path.abspath(path), 0o600)
            json.dump({"stamp": stamp, "model": a.model, "results": results}, fh, indent=1)
        print(f"\n  report (key names and verdicts only): {os.path.abspath(path)}")
    except OSError as e:
        print(f"\n  report not written: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
