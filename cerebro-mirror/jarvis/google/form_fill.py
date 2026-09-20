"""Fill a form from the record — and leave blank what the record does not say.

THE WHOLE POINT IS THAT NO MODEL EVER PRODUCES A VALUE HERE. Every field is a lookup in
`records_facts`, and a field the ledger cannot answer comes back EMPTY with the reason
stated. That property is what makes this safe to point at a DA form: the failure mode of a
generative filler is a form that looks complete and is wrong in three places, and nobody
catches it until the paperwork is rejected or, worse, accepted.

A REGISTER IS A DOCUMENT TOO. The existing `form_mappings` table maps
"Checklist_T-11-A-5.pdf / UIC" to the profile key `unit`, which is not the same thing — a
UIC is a unit identification code and `unit` holds a name. A wrong mapping invents a value
just as surely as a wrong sentence does, so mappings live here, in code, with their
reasoning, rather than in a table that can be edited without one.

FOUR OUTCOMES, AND THE THIRD ONE IS THE PRODUCT:

  FILLED        a value the documents support, with how many back it
  UNCONFIRMED   the ledger has a value and NO DOCUMENT SUPPORTS IT — shown, never filled
  BLANK         the ledger has nothing — left for a person, and said so
  REFUSED       must never be auto-filled (free text, a signature, an attestation), or its
                meaning is not what the form's label suggests

WHY UNCONFIRMED EXISTS, AND IT WAS FOUND BY RUNNING THIS. The first version filled
"City, State, Zip Code" with "Peton, CO 80831" — because the ledger's `home_city` is a
row with no source document and zero supporting documents, carrying the misspelling that
was read off one bad scan while ten documents spell it PEYTON. The chat got that wrong for
weeks. Putting it on a DA form would have been worse: a form is a thing a clerk acts on,
and nobody reads it as a claim. So document support is now REQUIRED to fill a field, and a
value that exists without it is displayed rather than used — which is how a wrong fact
gets SEEN instead of propagated.

Usage:
    python3 form_fill.py plan  <form-id-or-field>...   # what could be filled, from a real form
    python3 form_fill.py ledger                        # every fact available to fill from
    python3 form_fill.py fields                        # the vocabulary this knows
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))


def _city_state_zip(parts: dict) -> str:
    """Compose an address line from three separate facts.

    A form asks for one field where the record holds three, and joining them is
    ARITHMETIC ON KNOWN VALUES, not invention — every component still comes from a fact and
    is reported with it. If any piece is missing the whole field is left blank: a partial
    address is worse than none on an official form, because it looks complete.
    """
    city, state, zipc = parts.get("home_city"), parts.get("home_state"), parts.get("home_zip")
    if not (city and state and zipc):
        return ""
    return f"{city}, {state} {zipc}"


# form field (lowercased, punctuation stripped) -> (ledger keys in priority order, composer)
#
# The composer is None when one fact is the whole answer. It is only ever a pure function
# of facts that were found — never a default, never a guess.
FIELDS = {
    "member name":        (["member_name"], None),
    "soldier name":       (["member_name"], None),
    "name":               (["member_name"], None),
    "full name":          (["member_name"], None),
    "name of member":     (["member_name"], None),
    "rank":               (["rank", "member_rank"], None),
    "grade":              (["rank", "member_rank"], None),
    "pay grade":          (["rank", "member_rank"], None),
    "unit name":          (["unit", "member_unit"], None),
    "unit":               (["unit", "member_unit"], None),
    "organization":       (["unit", "member_unit"], None),
    "station number":     (["station_number", "member_station"], None),
    "ssn":                (["ssn"], None),
    "social security number": (["ssn"], None),
    "date of birth":      (["date_of_birth", "dob"], None),
    "dob":                (["date_of_birth", "dob"], None),
    "branch":             (["branch"], None),
    "blood type":         (["blood_type"], None),
    "street":             (["home_street"], None),
    "street address":     (["home_street"], None),
    "address":            (["home_street"], None),
    "city state zip code": (["home_city", "home_state", "home_zip"], _city_state_zip),
    "city, state, zip code": (["home_city", "home_state", "home_zip"], _city_state_zip),
    "city state zip":     (["home_city", "home_state", "home_zip"], _city_state_zip),
    "effective date":     (["effective_date"], None),
    # A BARE "Date" IS NOT MAPPED, and it used to be. It was filled with `report_date` —
    # a date off an ORDERS document — onto whatever the form meant by "Date", which on a
    # checklist is nearly always "date initiated". Those are different facts about
    # different events, and the field label alone does not say which is wanted.
    "unit location":      (["unit_location", "duty_location"], None),
    "security clearance": (["security_clearance"], None),
    "specialty code":     (["specialty_code", "military_occupation_code"], None),
    "mos":                (["specialty_code", "military_occupation_code"], None),
}

# Fields that look fillable and must never be filled automatically. Each is here for a
# reason that would otherwise be re-learned by someone putting a wrong value on a form.
REFUSE = {
    "remarks": "free text — the form wants a person's words, not a lookup",
    "signature": "a signature is an act, not a value",
    "date signed": "must match the day it is actually signed",
    "certifying": "an attestation — someone has to mean it",
    "uic": ("a unit identification CODE, not the unit name — the two are different fields "
            "and the old mapping treated them as one"),
    "email": "no fact holds an address; do not guess one for an official form",
    "unit poc": "a person's name and contact — not in the record",
    "date initiated": "a date the owner chooses, not one the record contains",
    "privacy act": "boilerplate on the form itself",
    "date": ("a bare \"Date\" does not say WHICH date — date initiated, date signed and "
             "effective date are different facts, and the form has to say which"),
}


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def facts(conn, keys: list[str]) -> dict:
    """The current value of each key, with how well it is backed."""
    if not keys:
        return {}
    marks = ",".join("?" * len(keys))
    rows = conn.execute(
        f"SELECT key_name, value, source_name, sourced, evidence_count FROM records_facts"
        f" WHERE is_current = 1 AND key_name IN ({marks})", keys).fetchall()
    out = {}
    for r in rows:
        # First writer wins, and the order of `keys` is the preference — so a form's
        # `rank` can prefer `rank` over `member_rank` without a second mechanism.
        out.setdefault(r["key_name"], dict(r))
    return out


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9, ]", " ", (name or "").lower())).strip()


def _strength(row: dict) -> tuple:
    """How much is behind a fact. Higher is stronger; every tiebreak is stated."""
    return (row["evidence_count"] or 0, 1 if row["sourced"] == 1 else 0,
            1 if row["source_name"] else 0)


def plan(conn, form_fields: list[str]) -> list[dict]:
    """What each form field would be filled with — or why it would not be.

    `form_fields` are the LABELS as they appear on the form. Unknown labels come back with
    "not a field this knows how to map", which is the honest answer and the one that stops
    a filler reaching for something plausible.
    """
    out = []
    for label in form_fields:
        key = _norm(label)
        if key in REFUSE:
            out.append({"field": label, "status": "REFUSED", "value": "",
                        "why": REFUSE[key]})
            continue
        spec = FIELDS.get(key)
        if not spec:
            out.append({"field": label, "status": "UNMAPPED", "value": "",
                        "why": "not a field this knows how to map — left blank rather "
                               "than guessed at"})
            continue
        keys, compose = spec
        found = facts(conn, keys)

        if compose:
            # Every component must be present AND document-backed, or the field stays
            # empty. A half-filled address on an official form reads as a complete one.
            want = keys
            have = [k for k in want if k in found]
            weak = [k for k in have if (found[k]["evidence_count"] or 0) < 1]
            if len(have) < len(want) or weak:
                missing = [k for k in want if k not in found]
                why = ("the record does not contain " + ", ".join(missing)) if missing else (
                    "no document supports " + ", ".join(weak))
                shown = compose({k: v["value"] for k, v in found.items()})
                out.append({"field": label, "status": "UNCONFIRMED" if shown else "BLANK",
                            "value": shown, "why": why,
                            "detail": "; ".join(
                                f"{k}={found[k]['value']} ({found[k]['evidence_count'] or 0} doc)"
                                for k in have)})
                continue
            value = compose({k: v["value"] for k, v in found.items()})
            parts = [found[k] for k in want]
            out.append({"field": label, "status": "FILLED", "value": value, "keys": keys,
                        "evidence": min(p["evidence_count"] or 0 for p in parts),
                        "sources": sorted({p["source_name"] for p in parts
                                           if p["source_name"]})})
            continue

        # Single-fact field: the best-supported candidate wins, and the ordering of `keys`
        # is the preference — so a form's `rank` prefers `rank` over `member_rank` without
        # needing a second mechanism.
        cands = [found[k] for k in keys if k in found]
        if not cands:
            out.append({"field": label, "status": "BLANK", "value": "",
                        "why": "no fact for " + " or ".join(keys)})
            continue
        hit = max(cands, key=_strength)
        if (hit["evidence_count"] or 0) < 1:
            out.append({"field": label, "status": "UNCONFIRMED", "value": hit["value"],
                        "why": "the record carries this value but no document supports it",
                        "detail": f"{hit['key_name']}={hit['value']}"
                                  f" ({hit['evidence_count'] or 0} doc(s),"
                                  f" sourced={hit['sourced']})"})
            continue
        out.append({"field": label, "status": "FILLED", "value": hit["value"],
                    "keys": [hit["key_name"]], "evidence": hit["evidence_count"] or 0,
                    "sources": [hit["source_name"]] if hit["source_name"] else []})
    return out


def report(rows: list[dict]) -> None:
    filled = [r for r in rows if r["status"] == "FILLED"]
    weak = [r for r in rows if r["status"] == "UNCONFIRMED"]
    print(f"\n  {len(filled)} of {len(rows)} field(s) can be filled from the record"
          + (f"; {len(weak)} has a value the documents do not support" if weak else "") + "\n")
    for r in rows:
        head = r["status"]
        if head == "FILLED":
            print(f"  FILLED        {r['field'][:30]:<30} {r['value'][:46]}")
            src = r["sources"][0][:52] if r["sources"] else "no source named"
            print(f"                from {', '.join(r['keys'])} · {r['evidence']} doc(s)"
                  f" · {src}")
        elif head == "UNCONFIRMED":
            # Shown, and deliberately NOT filled. This is where a wrong fact becomes
            # visible instead of being copied onto a form.
            print(f"  UNCONFIRMED   {r['field'][:30]:<30} {r['value'][:46]}")
            print(f"                {r['why']}")
            print(f"                {r.get('detail', '')}")
            print(f"                -> left blank. Correct the record first, then re-run.")
        elif head == "REFUSED":
            print(f"  REFUSED       {r['field'][:30]:<30} {r['why']}")
        elif head == "BLANK":
            print(f"  BLANK         {r['field'][:30]:<30} {r['why']}")
        else:
            print(f"  UNMAPPED      {r['field'][:30]:<30} {r['why']}")
    print(f"\n  {len(rows) - len(filled)} left for a person. Nothing above was invented —"
          f" a field is filled only when a document supports the value.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "ledger", "fields"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    conn = db()

    if a.cmd == "fields":
        for k in sorted(FIELDS):
            print(f"  {k:<26} <- {', '.join(FIELDS[k][0])}")
        print(f"\n  refused on purpose:")
        for k, why in sorted(REFUSE.items()):
            print(f"  {k:<26} {why}")
        return 0

    if a.cmd == "ledger":
        rows = conn.execute("SELECT key_name, value, evidence_count FROM records_facts"
                            " WHERE is_current = 1 ORDER BY evidence_count DESC").fetchall()
        print(f"  {len(rows)} current facts available to fill from:")
        for r in rows:
            print(f"  {r['key_name']:<34} {str(r['value'])[:40]:<40} ({r['evidence_count']})")
        return 0

    if not a.args:
        print("  give at least one field label"); return 2
    report(plan(conn, a.args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
