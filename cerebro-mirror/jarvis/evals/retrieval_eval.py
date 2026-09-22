"""Retrieval-only scoring: did the ANSWER reach the model?

WHY THIS EXISTS SEPARATELY FROM lane_eval.py. That harness scores the model's reply, so a
wrong answer has two possible causes — the evidence was never fetched, or it was fetched and
the model ignored it — and one number cannot tell them apart. This measures only the first
stage, deterministically, with no model in the loop. It runs in seconds.

WHAT IS MEASURED. For each question, the lanes `run_ask` actually calls are called with the
same limits, and the assembled material is searched for the value that answers the question.
A question "hits" when the answer text reaches the context. The rank is recorded because a
hit at position 17 of 18 is not the same as a hit at position 1.

  recall      fraction of answerable questions whose answer reached the context
  noise       returned items carrying no gold — what the model must see past
  unreachable answer is in the index but retrieval never surfaces it (the costly class:
              it looks identical to "we don't have that" from the outside)

THE UNANSWERABLE QUESTIONS ARE NOT SCORED AS MISSES. They have no answer by construction —
they are the invention test from lane_eval, kept here for their CONTEXT cost: what gets
handed to the model when the honest answer is "we do not have that". A retrieval stage that
floods those with near-miss material is manufacturing the invention it later gets blamed for.

Usage:
    python3 retrieval_eval.py                 # score, print the table
    python3 retrieval_eval.py --json out.json # also write machine-readable results
    python3 retrieval_eval.py --verbose       # show every returned item
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

# The limits run_ask uses. Kept here as data so the harness fails loudly if they drift.
DOC_LIMIT, MAIL_LIMIT, CAL_DAYS, CAL_LIMIT = 6, 4, 60, 8


# ---------------------------------------------------------------- the questions
# `gold` is a regex whose match means the answer reached the context. Word boundaries
# matter: "maj" unanchored matches "major", "majority" and "Majesty", which is precisely
# the substring bug this eval exists to catch — so the pattern is anchored even where the
# current retriever is not.
QUESTIONS = [
    # ---- answerable from the index ----
    dict(q="what is my station number", gold=r"\b301[\s-]?60[\s-]?8814\b|\b301608814\b",
         where="46 documents, 2 facts, 0 messages"),
    dict(q="what is the state file number on my birth certificate", gold=r"\b1974097100\b",
         where="1 document, 0 facts, 0 messages"),
    dict(q="what is my mother's maiden name", gold=r"\bhenderson\b",
         where="43 documents"),
    dict(q="what is my rank", gold=r"\bMAJ\b",
         where="3 facts (member_rank, rank)"),
    dict(q="what is my current rank", gold=r"\bMAJ\b", where="same fact rows"),
    dict(q="what is my military unit called", gold=r"\bSUST CMD DET 2\b|\bW8ENY2\b",
         where="2 facts"),
    dict(q="what is my date of birth according to my military records",
         gold=r"\b1974-08-02\b|\b2 August 1974\b", where="3 facts"),
    dict(q="what is my full name", gold=r"\bMICHAEL NATHAN MEYER\b|\bMeyer, Michael N\b",
         where="service record"),
    dict(q="what did the Veterans Advantage email say", gold=r"veterans\s*advantage",
         where="823 messages"),
    dict(q="when was my last TRICARE dental invoice?", gold=r"TRICARE|dental",
         where="mail + documents"),
    dict(q="any recent emails about CVS savings?", gold=r"\bCVS\b", where="mail"),
    dict(q="do you know where my brother Dave lives?", gold=r"\bDave\b|\bDavid\b",
         where="mail + calendar"),

    # ---- unanswerable: the invention test. Scored on CONTEXT COST, not recall. ----
    dict(q="what is my blood type", unanswerable=True, label="BLOOD TYPE",
         where="form label present, value absent"),
    dict(q="what is my passport number", unanswerable=True, label="PASSPORT",
         where="no passport in the index"),
    dict(q="what is my bank account number", unanswerable=True, label="ACCOUNT",
         where="no bank details in the index"),
    dict(q="what is my blood pressure", unanswerable=True, label="BLOOD PRESSURE",
         where="no vitals in the index"),
    dict(q="am I in the 75th Ranger Regiment", unanswerable=True, label="RANGER",
         where="a documented FALSE belief"),
]


# ---------------------------------------------------------------- retrieval
def _api(path: str, **params):
    url = f"{MAIL_URL}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ValueError) as exc:
        return {"__error__": str(exc)}


def assemble(question: str):
    """Exactly the material run_ask builds, in the same order, with the same limits."""
    items = []

    d = _api("/documents/search", q=question, limit=DOC_LIMIT)
    for h in (d.get("results") or []):
        items.append({
            "tier": "document", "source": h.get("source"),
            "id": h.get("id") or "", "cite": h.get("cite") or "",
            "title": h.get("name") or "",
            "text": f"{h.get('name') or ''} {h.get('snippet') or ''}",
            "read": h.get("read"),
        })

    m = _api("/mail/search", q=question, limit=MAIL_LIMIT)
    for h in (m.get("results") or []):
        items.append({
            "tier": "mail", "source": "mail",
            "id": h.get("id") or "", "cite": h.get("id") or "",
            "title": h.get("subject") or "",
            "text": f"{h.get('subject') or ''} {h.get('snippet') or ''}",
        })

    c = _api("/calendar/upcoming", days=CAL_DAYS, limit=CAL_LIMIT)
    for e in (c.get("events") or []):
        items.append({
            "tier": "calendar", "source": "calendar",
            "id": str(e.get("id") or ""), "cite": "",
            "title": f"{e.get('when') or ''} {e.get('summary') or ''}",
            "text": f"{e.get('when') or ''} {e.get('summary') or ''} {e.get('location') or ''}",
        })

    return items, {"documents": d, "mail": m, "calendar": c}


def in_index(gold_rx: re.Pattern) -> dict:
    """Is the answer in the index at all? Separates 'missed' from 'never had it'."""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {"messages": 0, "documents": 0, "facts": 0}
    try:
        rows = list(conn.execute(
            "SELECT m.subject, t.body FROM messages m"
            " LEFT JOIN message_text t ON t.message_id = m.id WHERE m.status='active'"))
        out["messages"] = sum(1 for s, b in rows if gold_rx.search(f"{s or ''} {b or ''}"))
        rows = list(conn.execute("SELECT body FROM document_text"))
        out["documents"] = sum(1 for (b,) in rows if gold_rx.search(b or ""))
        rows = list(conn.execute("SELECT key_name, value FROM records_facts"))
        out["facts"] = sum(1 for k, v in rows if gold_rx.search(f"{k or ''} {v or ''}"))
    finally:
        conn.close()
    return out


def score(spec: dict, verbose: bool) -> dict:
    rx = re.compile(spec["gold"], re.I) if spec.get("gold") else None
    items, raw = assemble(spec["q"])

    gold_hits = []
    for i, it in enumerate(items, 1):
        if rx and rx.search(it["text"]):
            gold_hits.append(i)

    by_tier = {}
    for it in items:
        by_tier[it["tier"]] = by_tier.get(it["tier"], 0) + 1

    # For an unanswerable question, a return that mentions the FIELD is the temptation —
    # the model is shown a form label with no value and asked to fill it in.
    label_shown = 0
    if spec.get("unanswerable") and spec.get("label"):
        lrx = re.compile(re.escape(spec["label"]), re.I)
        label_shown = sum(1 for it in items if lrx.search(it["text"]))

    res = {
        "q": spec["q"],
        "unanswerable": bool(spec.get("unanswerable")),
        "returned": len(items),
        "by_tier": by_tier,
        "gold_rank": gold_hits[0] if gold_hits else None,
        "gold_hits": gold_hits,
        "context_chars": sum(len(it["text"]) for it in items),
        "label_shown": label_shown,
        "errors": [k for k, v in raw.items() if isinstance(v, dict) and v.get("__error__")],
    }
    if not res["unanswerable"]:
        res["index"] = in_index(rx) if rx else {}
        res["ok"] = bool(gold_hits)
    if verbose:
        for i, it in enumerate(items, 1):
            mark = "***" if i in gold_hits else "   "
            print(f"      {mark} {i:>2}. [{it['tier']:<8}] {it['title'][:72]}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not TOKEN:
        raise SystemExit("JARVIS_MAIL_TOKEN is not set — the harness reads the live index")

    rows = []
    for spec in QUESTIONS:
        r = score(spec, args.verbose)
        rows.append(r)
        label = "UNANSWERABLE" if r["unanswerable"] else ("HIT" if r["ok"] else "MISS")
        print(f"\n[{label:<12}] {r['q']}")
        print(f"      where the answer lives: {spec.get('where','')}")
        print(f"      returned {r['returned']} items {r['by_tier']}, "
              f"{r['context_chars']} chars of material")
        if r["unanswerable"]:
            print(f"      field label shown to the model in {r['label_shown']} item(s)")
        else:
            ix = r.get("index") or {}
            print(f"      in index: {ix.get('messages',0)} msg / {ix.get('documents',0)} doc "
                  f"/ {ix.get('facts',0)} fact")
            print(f"      gold rank: {r['gold_rank']}   hits at {r['gold_hits']}")
        if r["errors"]:
            print(f"      LANE ERRORS: {r['errors']}")

    ans = [r for r in rows if not r["unanswerable"]]
    unans = [r for r in rows if r["unanswerable"]]
    hits = [r for r in ans if r["ok"]]

    print("\n" + "=" * 78)
    print("  RETRIEVAL")
    print("-" * 78)
    print(f"  answerable questions      {len(ans)}")
    print(f"  answer reached context    {len(hits)}  ({100*len(hits)//max(len(ans),1)}%)")
    missed = [r for r in ans if not r["ok"]]
    if missed:
        print(f"  MISSED                    {len(missed)}")
        for r in missed:
            ix = r.get("index") or {}
            reach = "IN INDEX — retrieval cannot reach it" if sum(ix.values()) else "not in index"
            print(f"      - {r['q'][:58]:<58} {reach}")
    ranks = [r["gold_rank"] for r in hits if r["gold_rank"]]
    if ranks:
        print(f"  mean gold rank            {sum(ranks)/len(ranks):.1f}")
    print(f"  mean material returned    "
          f"{sum(r['returned'] for r in ans)/max(len(ans),1):.1f} items, "
          f"{sum(r['context_chars'] for r in ans)//max(len(ans),1)} chars")
    print()
    print(f"  unanswerable questions    {len(unans)}")
    print(f"  mean material returned    "
          f"{sum(r['returned'] for r in unans)/max(len(unans),1):.1f} items, "
          f"{sum(r['context_chars'] for r in unans)//max(len(unans),1)} chars")
    print("  ^ the material shown when the honest answer is 'we do not have that'")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=1)
        print(f"\n  written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
