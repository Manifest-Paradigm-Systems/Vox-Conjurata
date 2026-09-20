"""Rebuild the service-record fact ledger from the documents, not from a model.

`LifePacket-AI`'s `profile_data_history` holds 123 key/value facts — rank, unit, home
address, SSN — produced by asking a small local model to summarise `ocr_text[:3000]` of
each document. As a source of record it fails in three ways, and all three have
consequences:

  * **The values are generated text, not extracted spans.** 39 of the 123 do not appear
    in their own source document — a city name is stored misspelled, and the misspelling
    traces to a single recent scan our own OCR misread.
  * **`effective_date` is NULL for every row**, so "which is current" cannot be decided
    from the data at all.
  * **`is_current` is therefore set by WRITE ORDER** — last run wins. Re-running the same
    extraction flips the owner's rank between MAJ and CPT, and the two rows that decide
    the answer have `source_doc_id = NULL`: they came from nobody.

This rebuilds it in the index, where the documents now are, so that every fact can name
the document it came from and every fact that cannot is visibly unsourced rather than
quietly authoritative.

THREE RULES, and each replaces one of the failures above:

  1. `effective_date` comes from the source document's own `document_date`.
  2. `is_current` is the row with the LATEST `effective_date` per (domain, key) — never
     the most recently written one. A fact with no date can never outrank a dated one.
  3. Every value is marked `sourced` when it can be found in its document's text, and
     `unsourced` when it cannot. An unsourced value is not deleted — it is labelled, so
     the answer to "where did this come from" is "nowhere I can show you".

`life_records.db` itself is left untouched. It is the source artifact and gets backed up
as it stands; rewriting someone's ledger in place, without being asked, would be its own
kind of silent edit.

Usage:
    python3 lifepacket_facts.py rebuild     # (re)build records_facts in the index
    python3 lifepacket_facts.py report      # what is current, and what it rests on
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import time

LIFE_DB = os.path.expanduser(os.getenv("JARVIS_LIFEPACKET_DB",
                                       "~/.lifepacket/life_records.db"))
DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))
ACCOUNT = os.getenv("JARVIS_LIFEPACKET_ACCOUNT", "unconfigured@invalid")

SCHEMA = """
CREATE TABLE IF NOT EXISTS records_facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account        TEXT NOT NULL,
    domain         TEXT,
    key_name       TEXT NOT NULL,
    value          TEXT NOT NULL,
    effective_date TEXT,        -- the source document's own date, never the write time
    source_file_id TEXT,        -- -> drive_files.id (a lifepacket: document)
    source_name    TEXT,        -- its filename, so an answer can name it
    sourced        INTEGER,     -- 1 = the value appears in that document's text
    evidence_date  TEXT,        -- the LATEST document in the corpus that supports it
    evidence_count INTEGER,     -- how many documents support it (the stable-fact judge)
    is_current     INTEGER DEFAULT 0,
    rebuilt_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_facts_key ON records_facts(domain, key_name, is_current);
"""


def life_db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{LIFE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def index_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=180)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 180000")
    return conn


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def appears_in(value: str, body: str) -> bool:
    """Is this value actually in the document it claims to come from?

    Two normalisations, because both appear in this corpus and neither is a lie:
    a date is stored `1974-08-02` and printed `DOB: 19740802`, and a name is printed in
    whatever case the form uses. What is being tested is presence, not formatting — the
    point is to separate a fact that can be shown to the owner from one that cannot.
    """
    v, b = _norm(value), _norm(body)
    if len(v) >= 3 and v in b:
        return True
    dv = re.sub(r"\D", "", value or "")
    return len(dv) >= 6 and dv in re.sub(r"\D", "", body or "")


def rebuild() -> int:
    life = life_db()
    conn = index_db()
    # Dropped, not migrated. records_facts is entirely derived from the documents and the
    # ledger, so it is always safe to rebuild from nothing — and doing that means the
    # table shape can never lag the code that writes it. (The first run of this script
    # against a table it had created moments earlier failed on exactly that.)
    conn.executescript("DROP TABLE IF EXISTS records_facts;")
    conn.executescript(SCHEMA)

    # document_inventory.id -> file_id, via the hash the import used.
    inv = {r["id"]: r for r in life.execute("SELECT * FROM document_inventory")}
    file_for = {}
    for inv_id, row in inv.items():
        if row["file_hash"]:
            file_for[inv_id] = f"lifepacket:{row['file_hash']}"

    bodies = {}
    for file_id, body in conn.execute(
            "SELECT file_id, body FROM document_text WHERE file_id LIKE 'lifepacket:%'"):
        bodies[file_id] = body

    # The whole corpus, so a value can be checked against every document and not only
    # against the one row it arrived in. This is what makes rule 2 below possible.
    corpus = []
    for file_id, when, body in conn.execute(
            "SELECT id, modified_time, (SELECT body FROM document_text d WHERE d.file_id ="
            " drive_files.id) FROM drive_files WHERE source = 'lifepacket'"):
        if body:
            corpus.append(((when or "")[:10], _norm(body), re.sub(r"\D", "", body)))
    corpus.sort(key=lambda t: t[0])

    def evidence(value: str) -> tuple[str | None, int]:
        """(latest document date supporting this value, how many documents support it)."""
        v = _norm(value)
        dv = re.sub(r"\D", "", value or "")
        if len(v) < 3:
            return None, 0
        latest, count = None, 0
        for when, body_n, body_d in corpus:
            if v in body_n or (len(dv) >= 6 and dv in body_d):
                latest = when            # corpus is sorted, so the last hit is the latest
                count += 1
        return latest, count

    rows = []
    for r in life.execute("SELECT * FROM profile_data_history ORDER BY domain_id, key_name"):
        src = inv.get(r["source_doc_id"]) if r["source_doc_id"] else None
        file_id = file_for.get(r["source_doc_id"]) if r["source_doc_id"] else None
        # Rule 1: the date comes from the DOCUMENT, not from when the row was written.
        eff = (src["document_date"] if src else None) or None
        body = bodies.get(file_id) if file_id else None
        sourced = None
        if body is not None:
            sourced = 1 if appears_in(r["value"], body) else 0
        ev_date, ev_count = evidence(r["value"])
        rows.append({
            "domain": r["domain_id"], "key": r["key_name"], "value": r["value"],
            "eff": eff, "file_id": file_id,
            "name": (src["original_name"] if src else None),
            "sourced": sourced,
            "evidence": ev_date, "evidence_count": ev_count,
        })

    # Rule 2: current is decided by WHAT THE RECORD SHOWS, and how that is judged depends
    # on whether the fact is one that CHANGES.
    #
    # Both known errors in the original ledger came from getting this wrong in opposite
    # directions, and neither fixed the other:
    #
    #   rank        is temporal. CPT until a promotion order makes MAJ effective
    #               13 JUN 2016. Recency is the right judge — the latest evidence wins.
    #               The original ledger answered CPT because `is_current` was write order.
    #   address     is constant. Ten documents agree on the spelling and exactly one
    #               disagrees — a recent scan our own OCR misread, which the extractor
    #               then copied faithfully. Recency is the WRONG judge here: it picks the
    #               one bad read because it is the most recent document. Agreement is the
    #               right judge.
    #
    # So a date-like fact is ranked by its latest evidence, and a stable fact by how many
    # documents support it. Getting either rule wrong produces a confident wrong answer,
    # which is why the distinction is written down rather than tuned.
    best: dict[tuple, dict] = {}
    for row in rows:
        k = (row["domain"], row["key"])
        cur = best.get(k)
        if cur is None or _rank(row) > _rank(cur):
            best[k] = row
    for row in rows:
        row["current"] = 1 if best.get((row["domain"], row["key"])) is row else 0

    with conn:
        conn.execute("DELETE FROM records_facts WHERE account = ?", (ACCOUNT,))
        conn.executemany("""
            INSERT INTO records_facts (account, domain, key_name, value, effective_date,
                                       source_file_id, source_name, sourced, evidence_date,
                                       evidence_count, is_current, rebuilt_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [(ACCOUNT, r["domain"], r["key"], r["value"], r["eff"], r["file_id"],
              r["name"], r["sourced"], r["evidence"], r["evidence_count"], r["current"],
              time.time()) for r in rows])

    cur = sum(1 for r in rows if r["current"])
    unsourced = sum(1 for r in rows if r["sourced"] == 0)
    unlinked = sum(1 for r in rows if r["sourced"] is None)
    print(f"  {len(rows)} facts from {LIFE_DB}")
    print(f"    {cur} current, {len(rows) - cur} superseded")
    print(f"    {len(rows) - unsourced - unlinked} sourced (found in their document)")
    print(f"    {unsourced} UNSOURCED (the value is not in the document it names)")
    print(f"    {unlinked} with no source document at all")
    return len(rows)


# Facts that CHANGE over a career, where the most recent evidence is the truth.
# Everything else is treated as constant, where agreement across documents is the truth.
#
# The test is not "is this a date" but "does a later document supersede an earlier one".
# A rank, a unit and a clearance are all superseded by the next order; a name, a date of
# birth and a home address are not — a later document that disagrees with ten earlier
# ones is far more likely to be a bad scan than a move nobody recorded.
TEMPORAL_KEYS = {
    "rank", "member_rank", "grade", "unit", "unit_location", "station_number",
    "duty_station", "security_clearance", "specialty_code", "tour_length",
    "demob_station", "order_number", "revision_date", "revoked_date", "revoked_order",
}


def _rank(row: dict) -> tuple:
    """Higher wins. The ordering flips on whether the key is temporal — see the comment
    at the call site, which is where both directions of this have already gone wrong once.

    A value no document supports sorts below every value that has one: an unevidenced
    claim must never outrank an evidenced one, whatever its date.
    """
    if row["key"] in TEMPORAL_KEYS:
        return (1 if row["evidence"] else 0, row["evidence"] or "",
                row["evidence_count"], row["sourced"] or 0,
                1 if row["file_id"] else 0)
    return (1 if row["evidence"] else 0, row["evidence_count"],
            row["evidence"] or "", row["sourced"] or 0,
            1 if row["file_id"] else 0)


def report() -> None:
    conn = index_db()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='records_facts'").fetchone():
        raise SystemExit("records_facts does not exist — run: lifepacket_facts.py rebuild")
    print("=== current facts ===")
    for r in conn.execute("""
            SELECT domain, key_name, value, effective_date, sourced, source_name
            FROM records_facts WHERE is_current = 1
            ORDER BY domain, key_name"""):
        flag = {1: "", 0: "  [UNSOURCED]", None: "  [no source document]"}[r["sourced"]]
        when = r["effective_date"] or "no date"
        print(f"  {r['domain']:<9s} {r['key_name']:<20s} {r['value'][:34]:<34s} "
              f"{when:<12s}{flag}")
        if r["source_name"]:
            print(f"            from: {r['source_name'][:72]}")
    print()
    print("=== where the two answers that mattered landed ===")
    for key in ("rank", "home_city"):
        print(f"  {key}  (judged by {'latest evidence' if key in TEMPORAL_KEYS else 'agreement'})")
        for r in conn.execute("""
                SELECT value, evidence_date, evidence_count, sourced, source_name, is_current
                FROM records_facts WHERE key_name=?
                ORDER BY is_current DESC, evidence_count DESC LIMIT 5""", (key,)):
            tag = "CURRENT " if r["is_current"] else "         "
            print(f"    {tag}{r['value']:<12s} in {r['evidence_count'] or 0:>3} document(s),"
                  f" last {(r['evidence_date'] or 'nowhere'):<10s}"
                  f" sourced={r['sourced']}")
            if r["source_name"]:
                print(f"                row from: {r['source_name'][:58]}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    if argv[1] == "rebuild":
        rebuild()
        return 0
    if argv[1] == "report":
        report()
        return 0
    raise SystemExit(f"unknown command {argv[1]!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
