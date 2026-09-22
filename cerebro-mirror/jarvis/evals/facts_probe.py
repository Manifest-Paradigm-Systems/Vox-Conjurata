"""Query the index for the owner's own information and check what comes back.

THE TESTS ARE GENERATED FROM THE DATABASE, NOT WRITTEN BY HAND. `records_facts` holds 81
current facts, each a (key, value) pair read out of a document. Every one is a question the
owner could ask and a value the index already claims to know, so the question set is derived
rather than invented — and it grows with the corpus instead of with someone's imagination.

Two directions, because they fail differently:

  1. FACT -> QUERY.   Ask for the fact in words. Did the stored value come back?
                      This is a retrieval failure: the data is right and unreachable.

  2. FACT -> SOURCE.  Does the document the fact cites actually contain that value?
                      This is a DATA failure: retrieval cannot fix it, and a better ranker
                      just surfaces the wrong value sooner.

DIRECTION 2 IS THE ONE THAT MATTERS MOST. `records_facts` is tier 0 in document_search.py's
ladder — above the owner's own documents — so an unsupported fact outranks the paperwork.
Dates are compared through an alternate-format matcher ("2012-10-02" vs "02 OCT 2012"),
because a naive string compare reports every date in the ledger as a fabrication.

Usage:
    python3 facts_probe.py                 # both directions
    python3 facts_probe.py --direction 1   # retrieval only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request

DB = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB", "~/jarvis/google/google.db"))
MAIL_URL = os.getenv("JARVIS_MAIL_URL", "http://127.0.0.1:7870")
TOKEN = os.getenv("JARVIS_MAIL_TOKEN", "").strip()
DOC_LIMIT, MAIL_LIMIT = 6, 4

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


# ---------------------------------------------------------------- matching
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def date_alts(value: str) -> list[str]:
    """Every way a date is actually written in these documents.

    Written because the first version of this check compared normalised strings and
    reported 17 facts as unsupported — all of them dates, all of them present. The
    order amending "02 OCT 2012" simply does not contain "2012-10-02".
    """
    m = re.fullmatch(r"(\d{4})-(\d{2})(?:-(\d{2}))?", value.strip())
    if not m:
        return []
    y, mo, d = m.group(1), int(m.group(2)), m.group(3)
    name = [k for k, v in MONTHS.items() if v == mo]
    if not name:
        return []
    mon = name[0]
    out = [f"{y}-{m.group(2)}"]
    if d:
        dd, di = d, str(int(d))
        out += [f"{dd} {mon} {y}", f"{di} {mon} {y}", f"{mon} {dd} {y}",
                f"{mon} {di}, {y}", f"{mon} {di} {y}",
                f"{m.group(2)}/{dd}/{y}", f"{dd}/{m.group(2)}/{y}"]
    return out


def value_present(value: str, text: str) -> bool:
    """Is this value in this text, allowing for how it is actually written?"""
    if not value:
        return False
    low = (text or "").lower()
    for alt in [value] + date_alts(value):
        a = alt.lower()
        if len(norm(a)) <= 3:
            # Short values ("MAJ", "O+", "SC") must match on a token boundary or they
            # match inside unrelated words — the same substring bug the retriever has.
            if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", low):
                return True
        elif a in low or norm(a) in norm(text):
            return True
    return False


# ---------------------------------------------------------------- question phrasing
def phrase(key: str) -> str:
    """A natural question for a ledger key. Falls back to the key, de-pluralised."""
    special = {
        "ssn": "what is my social security number",
        "member_station": "what is my station number",
        "station_number": "what is my station number",
        "rank": "what is my rank",
        "member_rank": "what is my rank",
        "date_of_birth": "what is my date of birth",
        "dob": "what is my date of birth",
        "unit": "what is my military unit called",
        "member_unit": "what is my military unit called",
        "member_name": "what is my full name",
        "security_clearance": "what is my security clearance",
        "specialty_code": "what is my MOS",
        "military_occupation_code": "what is my MOS",
        "blood_type": "what is my blood type",
        "unit_location": "where is my unit located",
        "duty_location": "where is my duty station",
        "home_city": "what city do I live in",
        "home_state": "what state do I live in",
        "home_zip": "what is my zip code",
        "home_street": "what is my home address",
        "pebd": "what is my pay entry base date",
    }
    if key in special:
        return special[key]
    return "what is my " + key.replace("_", " ")


# ---------------------------------------------------------------- retrieval
def _api(path: str, **params):
    url = f"{MAIL_URL}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ValueError) as exc:
        return {"__error__": str(exc)}


def probe_one(conn, key, value, verbose=False):
    q = phrase(key)
    d = _api("/documents/search", q=q, limit=DOC_LIMIT)
    m = _api("/mail/search", q=q, limit=MAIL_LIMIT)
    items = []
    for h in (d.get("results") or []):
        items.append(("doc", h.get("name") or "", h.get("id") or "",
                      f"{h.get('name') or ''} {h.get('snippet') or ''}"))
    for h in (m.get("results") or []):
        items.append(("mail", h.get("subject") or "", h.get("id") or "",
                      f"{h.get('subject') or ''} {h.get('snippet') or ''}"))

    rank = None
    for i, (_, _, _, text) in enumerate(items, 1):
        if value_present(value, text):
            rank = i
            break
    if verbose:
        for i, (tier, title, _id, _t) in enumerate(items, 1):
            print(f"        {i}. [{tier}] {title[:70]}")
    return dict(q=q, value=value, returned=len(items), rank=rank,
                errors=[k for k, v in (("documents", d), ("mail", m))
                        if isinstance(v, dict) and v.get("__error__")])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--direction", default="both", choices=["1", "2", "both"])
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    if not TOKEN:
        raise SystemExit("JARVIS_MAIL_TOKEN is not set")

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    facts = list(conn.execute(
        "SELECT key_name, value, source_file_id, source_name, evidence_count, sourced"
        " FROM records_facts WHERE is_current = 1 ORDER BY key_name"))
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM drive_files")}
    by_name = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM drive_files")}

    out = {}

    # ---- direction 2: does the cited document actually contain the value? ----
    if args.direction in ("2", "both"):
        print("=" * 78)
        print("  DIRECTION 2 — does the document the fact cites contain the value?")
        print("=" * 78)
        bodies = {r["file_id"]: (r["body"] or "") for r in
                  conn.execute("SELECT file_id, body FROM document_text")}
        unsourced, unsupported, verified = [], [], []
        for f in facts:
            if not f["value"]:
                continue
            src = f["source_file_id"] or by_name.get(f["source_name"] or "")
            if not src:
                unsourced.append(f)
                continue
            body = bodies.get(src)
            if body is None:
                unsupported.append((f, names.get(src, src), "cited document has no text"))
            elif value_present(f["value"], body):
                verified.append(f)
            else:
                unsupported.append((f, names.get(src, src), "value not in cited text"))
        print(f"  verified in their cited document : {len(verified)}")
        print(f"  UNSUPPORTED by cited document    : {len(unsupported)}")
        print(f"  NO SOURCE AT ALL                 : {len(unsourced)}")
        print()
        if unsourced:
            print("  -- no source: tier-0 authority with nothing behind it --")
            for f in unsourced:
                print(f"     {f['key_name']:<26} = {str(f['value'])[:40]:<40} "
                      f"evidence_count={f['evidence_count']}")
            print()
        if unsupported:
            print("  -- cited a document that does not contain the value --")
            for f, src, why in unsupported:
                print(f"     {f['key_name']:<26} = {str(f['value'])[:34]:<34} ({why})")
                print(f"        cited: {src}")
            print()
        out["direction2"] = {
            "verified": len(verified), "unsupported": len(unsupported),
            "unsourced": [f["key_name"] for f in unsourced],
            "unsupported_keys": [f["key_name"] for f, _, _ in unsupported],
        }

    # ---- direction 1: can retrieval return the value? ----
    if args.direction in ("1", "both"):
        print("=" * 78)
        print("  DIRECTION 1 — ask for the fact in words; did the value come back?")
        print("=" * 78)
        rows = []
        for f in facts:
            if not f["value"] or len(str(f["value"])) < 2:
                continue
            r = probe_one(conn, f["key_name"], str(f["value"]), args.verbose)
            r["key"] = f["key_name"]
            rows.append(r)
            flag = f"HIT@{r['rank']}" if r["rank"] else "MISS"
            print(f"  [{flag:<7}] {r['q'][:52]:<52} -> {str(f['value'])[:26]}")
            if args.verbose and not r["rank"]:
                for i, (tier, title, _i, _t) in enumerate([], 1):
                    pass
        hits = [r for r in rows if r["rank"]]
        print()
        print(f"  facts probed        : {len(rows)}")
        print(f"  value came back     : {len(hits)}  "
              f"({100*len(hits)//max(len(rows),1)}%)")
        if hits:
            print(f"  mean rank when found: {sum(r['rank'] for r in hits)/len(hits):.1f}")
        print()
        print("  -- MISSES: the index knows this and the question cannot reach it --")
        for r in rows:
            if not r["rank"]:
                print(f"     {r['q'][:56]:<56} (value {r['value'][:24]!r}, "
                      f"{r['returned']} items returned)")
        out["direction1"] = rows

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=1)
        print(f"\n  written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
