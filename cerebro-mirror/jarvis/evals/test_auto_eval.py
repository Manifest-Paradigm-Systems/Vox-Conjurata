"""The grader's date blindness, pinned so it cannot come back.

WHY THIS EXISTS. `grade()` compared a stored ISO date against the answer as a plain
substring. The model answers these service records the way the documents write them —
"1 March 1988" — so 13 correct answers were scored WRONG, and the headline read 67.1%
when the true figure was 85.7% (measured 2026-09-23).

THE OBVIOUS FIX WAS THE DANGEROUS ONE. Searching for "1 March 1988" as a substring also
matches inside "31 March 1988", which would have turned a false WRONG into a false
CORRECT — a grader that flatters the system is worse than one that penalises it, because
nothing downstream can see the flattery. The first three cases below are exactly that
trap, and they are the reason `states_date` is anchored and compares tuples.

EVERY DATE HERE IS INVENTED. No value from the owner's record appears in this file; the
repo gets code, and these cases test the comparison, not the data.

    python3 evals/test_auto_eval.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auto_eval as AE  # noqa: E402

CHECKS = []


def check(answer, value, want_verdict, why):
    CHECKS.append((answer, value, want_verdict, why))


DATE = "1988-03-07"      # stored ISO; the answers below must be read as 7 March 1988

# --- the trap the naive fix would have walked into -----------------------------------
check("Your date of rank is 31 March 1988.", DATE, "wrong", "31 != 07; '1 March 1988' is a substring of this")
check("Your date of rank is 21 March 1988.", DATE, "wrong", "21 != 07; same embedding")
check("Your date of rank is 1 March 1988.", DATE, "wrong", "day 01 != day 07, no suffix")
check("Your date of rank is 17 March 1988.", DATE, "wrong", "17 != 07, anchored both sides")

# --- the conventions that must be read as the SAME date ------------------------------
check("Your date of rank is 7 March 1988.", DATE, "correct", "D Month YYYY, the military convention")
check("Your date of rank is 7th March 1988.", DATE, "correct", "ordinal suffix")
check("Your date of rank is March 7, 1988.", DATE, "correct", "Month D, YYYY")
check("Your date of rank is Mar 7, 1988.", DATE, "correct", "abbreviated month")
check("Your date of rank is 07 March 1988.", DATE, "correct", "zero-padded day")
check("Your date of rank is 1988-03-07.", DATE, "correct", "the stored form itself, still accepted")
check("Your date of rank is 3/7/1988.", DATE, "correct", "US M/D order")

# --- numeric ambiguity is resolved strictly, and the cost is stated ------------------
check("Your date of rank is 7/3/1988.", DATE, "wrong",
      "US order makes this 3 July; day-first is NOT guessed while both numbers fit a month")
check("Your date of rank is 13/3/1988.", "1988-03-13", "correct",
      "13 cannot be a month, so day-first is unambiguous and accepted")
check("Your date of rank is 13/3/1988.", DATE, "wrong", "13 March is not 7 March")

# --- the non-date paths are untouched ------------------------------------------------
check("Your station number is A123456.", "A123456", "correct", "plain value, still a substring test")
check("Your station number is A123457.", "A123456", "wrong", "plain value, one digit off")
check("Your document number is 5551234567.", "5551234567", "correct", "the >=5-digit fallback survives")
check("Your document number is 5551234568.", "5551234567", "wrong", "and it is not loosened")
check("You served in March 1988.", "March 1988", "correct", "a non-ISO date value keeps the old path")
check("I could not find that in your records, sir.", DATE, "refused", "refusal is still detected")
check("__ERROR__ TimeoutError: x", DATE, "error", "errors are still errors")

fails = 0
for answer, value, want, why in CHECKS:
    got = AE.grade(answer, value)
    ok = got == want
    fails += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {got:<8} want {want:<8} {why}")
    if not ok:
        print(f"          value={value!r}")

# The count is asserted so a case silently dropped from the list is visible.
if len(CHECKS) != 21:
    print(f"  FAIL  {len(CHECKS)} checks ran, 21 expected")
    fails += 1

print(f"\n  {len(CHECKS) - fails}/{len(CHECKS)} checks pass")
sys.exit(1 if fails else 0)
