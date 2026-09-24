"""Pin `_index_note` — the one piece of prompt text the answer path prepends.

WHY THIS EXISTS. `run_ask` builds `context = index_note + "\\n\\n".join(blocks)`
(brain.py:1655) and `_local_answer` builds a PARALLEL numbered citation list from
`framed` with its own independent counter (brain.py:1416). Nothing enforces that the two
agree, and nothing tests this text at all — the note is the only prose inserted ahead of
the numbered material, so it is exactly where a well-meant edit desynchronises every
citation in every answer.

Two of these checks are load-bearing beyond wording:

  * `_index_note([]) == ""` is the BIT-FOR-BIT guarantee. Every question with no fact-tier
    note — mail, drive, calendar, web, wiki, and every fact query that resolves — must
    prepend nothing at all, so the prompt it produces is unchanged from before this text
    existed. Any change that makes the empty case non-empty changes every lane's prompt.

  * the note carries NO `[n]` of its own. A numbered line here would shift the model's
    idea of which item is which, silently, in every answer.

This is the first test in the project to import `brain.py`, which needs the service venv:

    /var/home/admin/ag2/.venv/bin/python3 brain/test_index_note.py

Run it with plain `python3` and it will say so rather than fail with an ImportError.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import brain
except ImportError as e:  # most likely httpx: brain.py is a service module, not a library
    print(f"  cannot import brain.py ({e})")
    print("  run this with the service venv:")
    print(f"    /var/home/admin/ag2/.venv/bin/python3 {os.path.basename(__file__)}")
    raise SystemExit(2)

CHECKS = []


def check(what, got, want):
    CHECKS.append((what, got, want))


# The template as shipped. Pinned in full on purpose: a wording change to this text is a
# change to every grounded answer, and it should have to be made deliberately and here.
TEMPLATE = (
    "INDEX NOTE — the extracted-fact index has no entry for this question. "
    "It reports: {said}. Facts are read out of documents in advance and keyed by "
    "field, so a word that matches no field means no FACT is listed here — the "
    "documents themselves have not been searched for meaning, only for the word, "
    "and one of them may well state the thing asked for. If the material below "
    "does state it, answer from it and cite it. If it does not, the answer is that "
    "it is not on record: do not supply a plausible value, and do not answer with "
    "some other field's value.\n\n"
)

# --- the bit-for-bit guarantee ---------------------------------------------------------
check("no notes -> empty string", brain._index_note([]), "")
check("no notes -> None-safe context", brain._index_note([]) + "BODY", "BODY")
check("a lone empty-string note still renders", brain._index_note([""]) == "", False)

# --- refusal, the case that exists today ----------------------------------------------
reason = "facts withheld: no fact mentions report, date"
got = brain._index_note([reason])
check("refusal: full template", got, TEMPLATE.format(said=reason))
check("refusal: names the reason", reason in got, True)
check("refusal: ends with a blank line", got.endswith("\n\n"), True)

# --- several notes join with '; ' -----------------------------------------------------
check("two notes: semicolon join", brain._index_note(["a", "b"]),
      TEMPLATE.format(said="a; b"))
check("three notes: order preserved", brain._index_note(["a", "b", "c"]),
      TEMPLATE.format(said="a; b; c"))

# --- non-string notes must not crash --------------------------------------------------
check("an int note is stringified", brain._index_note([7]),
      TEMPLATE.format(said="7"))

# --- THE INVARIANT: the note must never number anything -------------------------------
check("no [n] in the note", re.search(r"\[\d+\]", got), None)
check("no [n] when notes are digits", re.search(r"\[\d+\]", brain._index_note(["1"])), None)

# --- it prepends, it does not disturb -------------------------------------------------
body = "[1] FACT\n    a\n\n[2] DRIVE DOCUMENT\n    b"
check("prepending leaves the body intact", brain._index_note([reason]) + body,
      TEMPLATE.format(said=reason) + body)
check("the body still numbers from [1]", body.startswith("[1]"), True)

fails = 0
for what, g, want in CHECKS:
    ok = g == want
    fails += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {what}")
    if not ok:
        print(f"          got  {g!r}\n          want {want!r}")

print(f"\n  {len(CHECKS) - fails}/{len(CHECKS)} checks pass")
sys.exit(1 if fails else 0)
