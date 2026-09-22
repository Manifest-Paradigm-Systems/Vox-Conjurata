"""Reading the document index — the half that never existed.

`drive_index.py` has written `document_fts` and `document_text` since the day it was
built, and nothing has ever read them. No `MATCH`, no `SELECT body`, nothing. So Jarvis
could not answer a question about a single Drive file — not the plumbing estimate, not
the MRI report, not the service record — while 914 documents of text sat in the index.
This is the read path. It is the same shape as `mail_search.py`, for the same reason mail
needed one: an index nothing reads is a very expensive way of storing nothing.

TWO TIERS AGAIN, AND THIS TIME THE SECOND ONE MEANS SOMETHING DIFFERENT.

In mail, the metadata tier is promotions and social — real messages whose bodies were
never indexed, deliberately. Here it is **documents whose contents have not been read**:
2,713 of them, all PDFs, because the host had no poppler. Those are not the same thing
and must not be labelled the same way, or a coverage gap reads as an answer. Every result
therefore carries `read` (do we have the text?) and `text_state` (why not, when we do
not), and the caller is expected to say so out loud. A file whose name matches is still a
real answer — "Statement_65769.PDF" tells the owner something — but it is a weaker one.

THERE IS NO `search_live`. Mail has one because Gmail can be asked for a message the
crawl has not reached. No API reads inside a PDF, and Drive's own search does not index
these MIME types. The documents lane can only ever be as good as the crawl, so the
response says `"live": "unsupported"` rather than leaving a fallback that was never
written looking like one that was merely not needed today.

REUSE, NOT RE-MIRRORING. `mail_search.py` mirrors `_words`/`content_terms`/`_rank` from
`brain/db.py` because the brain is a separate deploy on a different host. This module is
the same tree, on the same host, in the same service as `mail_search.py`, so it imports
them. If you are about to copy those functions in here, that is a mistake, not a
consistency fix.

Usage:
    python3 document_search.py search <query> [--account X] [--limit N] [--source S]
    python3 document_search.py read <file-id>
    python3 document_search.py stats
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Imported, not mirrored — see the docstring.
from mail_search import (                                        # noqa: E402
    META_SCAN_LIMIT, _excluded, _field, _rank, connect, content_terms, load_rules,
)

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))

_COLS = ("f.id, f.account, f.name, f.mime_type, f.modified_time, f.source, "
         "f.text_state, f.size, f.collection")

# WHAT IS READ FIRST, AND WHY IT IS A LADDER RATHER THAN A SORT.
#
# The Drive holds ~2,350 commercial rulebooks — 89% of all the text in this index — and
# they match almost any word. Ask about a campaign, a dungeon, a dragon or an order and the
# honest best matches are game books, which is exactly what the owner does not want when
# the question is about his own affairs. So the tiers come before relevance:
#
#   0  curated facts         records_facts — the answer, with the document behind it
#   1  the service record    the owner's own military file
#   2  his own documents     everything untagged — mail, invoices, medical, school
#   3  tagged collections    reference works that merely live in the same Drive
#   4  anything else
#
# Ranking only reorders WITHIN a tier, so a rulebook can never outrank a personal document
# by matching better. The books remain searchable — ask in a way that clearly means them
# and they are right there — they just stop answering questions they were never part of.
def _tier(row) -> int:
    if row["source"] == "fact":
        return 0
    if row["source"] == "lifepacket":
        return 1
    if row["source"] == "drive":
        return 3 if (row["collection"] or "") else 2
    return 4


# ---------------------------------------------------------------- fact scoring
# THE FACT TABLE IS A SCHEMA, AND A QUESTION IS NOT.
#
# `records_facts` holds (slot_name, value) pairs: (`blood_type`, `O+`). A question is
# "what is my <thing> <slot>". Counting how many query terms a row matched gives the slot
# and the thing equal weight, and that is measurably wrong. Measured 2026-09-21, against
# the owner's own questions mined from `turns`: "what is my passport number" reduces to
# passport/number; `number` matches four slot names (station_number, form_number,
# order_number, document_number), `passport` matches NONE and therefore had no vote, and
# the tie broke on `evidence_count DESC` — that is, ON HOW COMMON THE FACT IS.
# `station_number` (115 documents) won, and came back at rank 1 carrying a citation,
# indistinguishable from an answer. The same arithmetic answered "bank account number",
# "state file number on my birth certificate" and "blood pressure" with the station number
# and the blood type, and answered "my mother's maiden name" with the owner's own name.
#
# Ranking by commonality where it must rank by distinctiveness. Two changes:
#
#   1  WEIGHT BY IDF, so that among the rows that legitimately match, the distinctive word
#      decides rather than the generic one.
#   2  GATE ON COVERAGE. If the question names a thing no fact can express — a passport, a
#      bank, a maiden name, a blood pressure, the Ranger Regiment — then every row that
#      matched did so on a generic word, and returning one answers a DIFFERENT question.
#      The fact tier says nothing instead, and says why. This is the branch the module never
#      had: with no way to express "not in the archive", a passport question was forced to
#      be answerable with a station number.
#
# Measured over the fact vocabulary: every question the retrieval got WRONG has an entity
# word with df=0, and every question it got RIGHT does not. The split is clean, which is
# why a gate rather than a threshold works here.
#
# The two lists are the minimum needed to tell a thing from a slot. Schema-level English,
# no personal data. `SCHEMA_WORDS` name a FIELD; `QUALIFIERS` scope the thing without
# identifying it. Dropping a qualifier must not change which fact answers — and if it does,
# it was the thing, not a qualifier ("full name" is why `name` is NOT a schema word).
#
# `military` is in QUALIFIERS and that is not obvious. This whole schema IS military: it
# scopes occupation, records, leave, rank. A word that scopes everything in the table
# cannot discriminate between rows in it — but IDF cannot see that, because `military`
# matches exactly ONE key (`military_occupation_code`) and so scores as the rarest, most
# decisive word in the query. Measured: "what is my military unit called" returned
# `military_occupation_code: 3GO3` at rank 1 with the actual `unit` pushed to rank 3.
# Rank by domain-scoping words and you answer a different question.
SCHEMA_WORDS = frozenset({
    "number", "date", "type", "code", "form", "file", "status", "value",
    "account", "record", "records", "document", "order",
})
QUALIFIERS = frozenset({
    "full", "current", "complete", "entire", "exact", "official", "actual", "real",
    "according", "called", "military",
    # QUESTION FILLER — the verbs and prepositions a question puts around the thing it is
    # asking about. Measured, and this list is why the gate does not fire on them:
    # "what city do I live in" reduced to city/live, `live` matched no fact, and the tier
    # withheld `home_city` — a fact sitting right there. "where is my unit located" withheld
    # `unit_location` the same way. A word the schema can never contain is not evidence that
    # the schema lacks the answer; it is usually just grammar.
    "live", "living", "lives", "located", "based", "reside", "resides", "residing",
})


def _fact_terms(terms: list[str]) -> list[str]:
    """The query's content words, reduced to the THINGS it names.

    "what is my station number" -> ["station"]. "what is my blood pressure" -> ["blood",
    "pressure"], and `pressure` matching no fact is the signal that decides whether the
    fact tier may answer at all.

    THIS SAME LIST IS WHAT SCORES. An earlier draft gated on it and then scored on every
    query word, which let a stripped qualifier back in through the score: `military` was
    removed from the gate and still matched `military_occupation_code`, so the row it had
    just been excluded from winning came back first. Stripped means stripped.
    """
    entities = [t for t in terms if t not in SCHEMA_WORDS and t not in QUALIFIERS]
    if entities:
        return entities
    # Nothing but slots and qualifiers survived. "what is my date of birth" leaves only
    # `date`; "what is my order number" leaves nothing but slots. The question named a slot
    # and nothing else, so the slot IS the thing it named — fall back rather than refuse a
    # question whose answer is sitting in the table.
    return [t for t in terms if t not in QUALIFIERS]


def _fact_hits(conn, terms, limit, account=None):
    """The curated facts about the owner, from `records_facts`. Returns (hits, refusal).

    THE MOST AUTHORITATIVE THING IN THIS INDEX, AND NOTHING WAS SEARCHING IT. The table
    holds rank, unit, station number, dates — read out of the documents and adjudicated by
    `lifepacket_facts.py` — and neither this module nor `mail_search.py` has ever queried
    it. Measured 2026-09-20, with the lane eval: given the entire service record as
    material, three different models all failed "what is my military unit", because the
    answer is a row in this table and the search could not reach it. Meanwhile retrieval
    was cheerfully matching form boilerplate — "NOT APPLICABLE to military personnel".

    A fact CITES THE DOCUMENT IT CAME FROM rather than itself: `source_file_id` points at
    a `drive_files` row, so following a citation opens the paperwork that proves it. A fact
    with no source document is still returned, but carries no id and says so — an unsourced
    fact is labelled, never dressed up as evidence.

    `refusal` is None when the tier may answer, and a plain reason when it may not. It is
    returned rather than raised or logged because the caller has to be able to say it out
    loud — the failure being fixed here is precisely a tier that answered when it should
    have stayed silent.
    """
    if not terms:
        return [], None

    where = "is_current = 1"
    args: list = []
    if account:
        where += " AND account = ?"
        args.append(account)
    # The whole table is read: ~81 rows against a query of a few words. Scoring in Python
    # buys per-term matches (so IDF can weight them) that the old single `matched` count
    # could not express, and costs nothing at this size.
    all_rows = conn.execute(
        "SELECT account, domain, key_name, value, effective_date, source_file_id,"
        " source_name, sourced, evidence_count"
        f" FROM records_facts WHERE {where}", args).fetchall()
    n = len(all_rows)
    if not n:
        return [], None

    def _matches(row, t: str) -> bool:
        return t in (row["key_name"] or "").lower() or t in (row["value"] or "").lower()

    df = {t: sum(1 for r in all_rows if _matches(r, t)) for t in terms}

    # THE COVERAGE GATE. An entity word that matches no fact at all cannot be what selected
    # any row, so a row returned here was selected by a slot word alone. That is a keyword
    # collision, not an answer.
    entities = _fact_terms(terms)
    missing = sorted({t for t in entities if df.get(t, 0) == 0})
    if missing:
        return [], "no fact mentions " + ", ".join(missing)

    scored = []
    for r in all_rows:
        hit = [t for t in entities if _matches(r, t)]
        if not hit:
            continue
        # log(1 + n/df), so a term in every fact is worth ~0 and a term in one is worth the
        # most. A term matching nothing is worth 0 and is already handled by the gate above.
        # Summing over ENTITIES only, so a row cannot buy its way back in on a word that was
        # deliberately stripped from the query.
        weight = sum(math.log(1 + n / df[t]) for t in hit if df[t])
        scored.append((weight, r["evidence_count"] or 0, r))
    scored.sort(key=lambda x: (-x[0], -x[1]))

    out = []
    for _weight, _ev, r in scored[:limit]:
        src = "unsourced" if r["sourced"] == 0 else ("no source document" if not r["source_file_id"] else "")
        out.append({
            # A FACT NEEDS ITS OWN ID, WITH THE DOCUMENT NAMED SEPARATELY. Several facts
            # routinely come from one document — `date_of_birth` and `date_of_rank` are
            # both read off the same personnel file — so keying a fact by its source
            # document made two of them collide in the merge and the last one inserted
            # silently replaced the other. Measured: "what is my date of birth" returned
            # the date of a RANK, because the birth fact had been overwritten by the fact
            # behind it. `id` is now unique per fact; `cite` is the document that proves
            # it, and that is what a citation opens.
            "id": f"fact:{r['key_name']}",
            "cite": r["source_file_id"] or "",
            "account": r["account"],
            "name": f"{r['key_name']}: {r['value']}",
            "mime_type": None,
            "modified_time": r["effective_date"] or "",
            "source": "fact",
            "text_state": "ok",
            "size": 0,
            "collection": None,
            "body": r["value"],
            # THE MARKER LEADS, IT DOES NOT TRAIL. It used to be appended after the document
            # count, where it was measurably ignored: handed "blood_type = …, (0
            # document(s))  [no source document]", the model still answered "your blood
            # type is …, recorded in your medical files" — in 4 of 4 runs. The marker was
            # not missing from the prompt; it was sitting after the claim instead of before
            # it. Leading the line puts the warning in the same breath as the assertion,
            # and means it cannot be lost to the 400-character cut the lane applies here.
            "snip": (f"[{src.upper()}] " if src else "")
                    + f"{r['key_name']} = {r['value']}"
                    + (f", from {r['source_name'][:60]}" if r["source_name"] else "")
                    + f" ({r['evidence_count']} document(s))",
            "score": 0.0,
            "read": True,
        })
    return out, None


def _shape(row, read: bool) -> dict:
    """One result, with the two fields that stop a coverage gap reading as an answer."""
    d = {k: row[k] for k in row.keys() if k not in ("body",)}
    d["read"] = read
    d["snippet"] = (d.get("snip") or d.get("snippet") or "").strip()
    d.pop("snip", None)
    return d


def _fts_query(conn, match, limit, account, source, scope="all"):
    sql = (f"SELECT {_COLS}, t.body AS body,"
           " snippet(document_fts, 3, '[', ']', ' … ', 16) AS snip,"
           " bm25(document_fts) AS score"
           " FROM document_fts"
           " JOIN drive_files f ON f.id = document_fts.file_id"
           " LEFT JOIN document_text t ON t.file_id = f.id"
           " WHERE document_fts MATCH ? AND f.trashed = 0 AND f.status = 'active'")
    args: list = [match]
    if account:
        sql += " AND f.account = ?"
        args.append(account)
    if source:
        sql += " AND f.source = ?"
        args.append(source)
    if scope == "personal":
        sql += " AND f.collection IS NULL"
    elif scope == "collection":
        sql += " AND f.collection IS NOT NULL"
    sql += " ORDER BY score LIMIT ?"
    args.append(limit * 4)
    return conn.execute(sql, args).fetchall()


def _fts_hits(conn, terms, limit, account=None, source=None, scope="all"):
    """Documents whose CONTENTS match. bm25-ranked within the source.

    OR, like mail, and an AND-first variant was tried here and REMOVED. It was added
    after a birth certificate could not be found by the words "state file number", and
    the reasoning looked sound — a corpus of forms shares so much vocabulary that an OR
    match buries the one document that matters. Measured against four real queries, AND
    and OR returned identical results on three and neither could find that certificate on
    the fourth.

    The certificate was unfindable because our own OCR read "STATE FILE NUMBER" as
    "BTATEFILENUMBER", so the word is not in the index at all. The retrieval was never
    the problem, and the extra query bought nothing. Recorded because the AND version is
    a plausible-looking change someone will otherwise re-propose.
    """
    match = " OR ".join(f'"{t}"' for t in terms)
    return _fts_query(conn, match, limit, account, source, scope)


def _meta_hits(conn, terms, limit, account=None, source=None):
    """Documents whose NAME matches and whose contents we never read.

    Bounded like mail's metadata tier, and for the same reason: a LIKE scan over ~2,700
    rows is fine, but a query matching something very common should not walk all of them.
    """
    like = " OR ".join("lower(COALESCE(f.name,'')) LIKE ?" for _ in terms)
    sql = (f"SELECT {_COLS}, '' AS body, '' AS snip, 0.0 AS score"
           " FROM drive_files f"
           " WHERE f.trashed = 0 AND f.status = 'active'"
           "   AND NOT EXISTS (SELECT 1 FROM document_text d WHERE d.file_id = f.id)"
           f"   AND ({like})")
    args: list = [f"%{t}%" for t in terms]
    if account:
        sql += " AND f.account = ?"
        args.append(account)
    if source:
        sql += " AND f.source = ?"
        args.append(source)
    sql += " ORDER BY f.modified_time DESC LIMIT ?"
    args.append(min(limit * 4, META_SCAN_LIMIT))
    return conn.execute(sql, args).fetchall()


def search_archive(conn, query: str, account=None, limit: int = 8, source=None
                   ) -> tuple[list[dict], str | None, list[str]]:
    """Search the document index. Returns (hits, error, notes).

    `error` names the tier that failed, and is never folded into an empty result — the
    rule this whole subsystem keeps relearning. A caller that cannot tell "nothing
    matched" from "the search broke" will report the second as the first.

    `notes` carries what the search chose NOT to say, which is a different thing again. The
    fact tier declines to answer when the question names something no fact can express, and
    that refusal has to travel: a caller that only sees an empty fact list will read it as
    "no facts matched", which is how a passport question gets answered with a station
    number by the layer above. A note is not an error — nothing broke — but it is not
    silence either.
    """
    terms = content_terms(query)
    if not terms:
        return [], None

    rows, errors = [], []
    failed_tiers = []
    # PERSONAL MATERIAL AND COLLECTIONS ARE FETCHED SEPARATELY, and this is not a
    # refinement — it is the difference between the ladder working and not.
    #
    # The fetch is capped at `limit * 4` and happens BEFORE the tiers are applied. With
    # roughly 4,500 rulebooks holding most of the text in this index, a capped fetch for
    # a word like "rank" returns almost nothing but game books, so the owner's service
    # records never entered the candidate set and no amount of correct ordering afterwards
    # could bring them back. Measured: "what is my current rank" answered from a Dungeon
    # Masters Guide while the service record sat two tiers above it, un-fetched.
    #
    # Ordering cannot rescue a query that already excluded the right answer. So each side
    # gets its own budget, and the tiering below decides what the reader sees first.
    facts: list = []
    fact_refusal: str | None = None
    try:
        facts, fact_refusal = _fact_hits(conn, terms, limit, account)
    except sqlite3.Error as exc:
        failed_tiers.append(f"fact tier: {exc}")
    # A fact names the document it came from, so that document is already represented in
    # the result. Mentioning both would double-count one piece of evidence and give the
    # reader two citations to the same page.
    fact_docs = {f["cite"] for f in facts if f.get("cite")}

    for scope, label in (("personal", "full-text tier"), ("collection", "collections")):
        try:
            rows += [r for r in _fts_hits(conn, terms, limit, account, source, scope)
                     if r["id"] not in fact_docs]
        except sqlite3.Error as exc:
            # NOT swallowed. The reference implementation in brain/db.py returns [] on a
            # bad query, which is indistinguishable from "no mail" — and that is exactly
            # how a broken index comes to look like an empty one.
            failed_tiers.append(f"{label}: {exc}")

    try:
        rows += list(_meta_hits(conn, terms, limit, account, source))
    except sqlite3.Error as exc:
        failed_tiers.append(f"name tier: {exc}")

    # Four fetches, so an empty result is only trustworthy when all four failed to run.
    if len(failed_tiers) >= 4:
        return [], "; ".join(failed_tiers)
    errors.extend(failed_tiers)

    rules = load_rules(conn, "drive") + load_rules(conn, "records")
    by_id: dict[str, sqlite3.Row] = {}
    # Facts go in first and win their document's slot: a fact IS that document's answer,
    # stated once and adjudicated, and listing both would hand the reader two citations to
    # the same page — one of them a worse rendition of the other.
    for r in facts:
        by_id[r["id"]] = r
    for r in rows:
        prev = by_id.get(r["id"])
        if prev is not None and isinstance(prev, dict) and prev.get("source") == "fact":
            continue
        if prev is None or (len(r["body"] or "") > len(prev["body"] or "")):
            by_id[r["id"]] = r

    # `_excluded` looks for `subject` and `from_addr`, because it was written for mail.
    # A document's equivalent of a subject is its filename, and an owner's `contains`
    # rule must not miss a document just because the field is named differently here —
    # so the name is offered under both keys before the rules are applied.
    kept = []
    for r in by_id.values():
        probe = dict(r)
        probe.setdefault("subject", probe.get("name") or "")
        if not _excluded(rules, probe):
            kept.append(r)

    # Ranked WITHIN each tier, then concatenated. `_rank` sorts by how well a row matches
    # the query, so running it across the merged set would interleave a scanned rulebook
    # between two pages of the service record and destroy the ordering that makes personal
    # material primary. The tier is the first key; relevance only orders within it.
    # The tier NUMBER travels with its rows. The allocation below needs to know which
    # group is tier 0, and position alone will not say: a query with no fact hits starts
    # its list at tier 1, so `tiers[0]` would be the lifepacket tier wearing the fact
    # tier's exemption.
    tiers: list[tuple[int, list]] = []
    for tier in (0, 1, 2, 3, 4):
        group = [r for r in kept if _tier(r) == tier]
        if not group:
            continue
        # TIER 0 ARRIVES ALREADY ORDERED, BY IDF, FROM `_fact_hits`. `_rank` scores a row by
        # how many query terms appear in it as substrings — the very count that ranked the
        # station number above the passport answer — so re-ranking the fact tier here would
        # undo the fix. Every other tier is still ordered by it.
        tiers.append((tier, group if tier == 0 else _rank(group, terms, limit, ("name", "body"))))

    # EVERY TIER THAT MATCHED GETS ONE ROW BEFORE ANY TIER GETS ITS SECOND.
    #
    # Concatenating and then truncating to `limit` does not merely REORDER the tiers — it
    # ERASES the ones below the cut. A tier holding `limit` or more rows consumes every
    # slot, and the tiers beneath it become unreachable at any rank, however good they are.
    # The fetches above already give each side its own budget, so those rows exist; this
    # loop was the only thing discarding them.
    #
    # Measured on the live index at limit 6: "mos" returned 6 lifepacket rows and NONE of
    # the 15 drive rows that match it; "when did i enlist" returned 6 and none of 24. The
    # same queries at limit 40 return 25/15 and 16/24 — the material was always there, and
    # the cut was the whole defect.
    #
    # The floor is ONE row, not `limit // len(tiers)`. Tier order still carries meaning —
    # the owner's own records outrank a scanned rulebook — and one slot is enough for a
    # lower tier to be SEEN. Equal shares would let a weak tier compete, which is a
    # different change with a different justification.
    #
    # TIER 0 IS NOT COMPETING FOR ITS SLOTS, IT ALREADY OWNS THEM. The fact tier is the
    # owner's own records — the one tier that is not a document at all — and a floor
    # granted to the tiers below it is paid for out of its window. The A/B caught the
    # price: `duty_location` sat at rank 4 for "where is my duty station" and the floors
    # pushed it out of a six-slot window entirely, 77 fact hits -> 76 across the 81 fact
    # questions. That is a worse trade than it looks, because a displaced fact is not a
    # reordered result, it is an answer the model never sees.
    #
    # Exempting it costs the starvation fix nothing, and that is measured, not assumed:
    # over the same 81 questions the fact tier never occupies more than 4 of the 6 slots,
    # and the two cases this patch exists for — "mos", "when did i enlist" — have no fact
    # tier at all. Where the fact tier does fill the window, the rows it displaces are its
    # own candidates, which is the priority the ladder already asserts.
    alloc = [1] * len(tiers)
    if tiers and tiers[0][0] == 0:
        alloc[0] = min(len(tiers[0][1]), limit)
    spare = limit - sum(alloc)
    for i in range(len(tiers)):
        if tiers[i][0] == 0:
            continue
        if spare <= 0:
            break
        take = min(len(tiers[i][1]) - 1, spare)
        if take > 0:
            alloc[i] += take
            spare -= take

    out: list[dict] = []
    for (_tier_no, ranked), take in zip(tiers, alloc):
        for r in ranked[:take]:
            read = bool((r["body"] or "").strip())
            out.append(_shape(r, read))
    notes = [f"facts withheld: {fact_refusal}"] if fact_refusal else []
    return out[:limit], ("; ".join(errors) if errors else None), notes


def read_document(conn, file_id: str) -> dict | None:
    """One document in full, with a page count if the text carries page markers."""
    row = conn.execute("""
        SELECT f.id, f.account, f.name, f.mime_type, f.modified_time, f.source,
               f.text_state, f.size, COALESCE(t.body,'') AS body, t.source AS text_source
        FROM drive_files f LEFT JOIN document_text t ON t.file_id = f.id
        WHERE f.id = ?""", (file_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["pages"] = d["body"].count("[page ") or (1 if d["body"].strip() else 0)
    d["read"] = bool(d["body"].strip())
    d["excluded"] = _excluded(load_rules(conn, "drive") + load_rules(conn, "records"), d)
    return d


def stats(conn) -> dict:
    """Coverage, split by source — the numbers that belong on /health."""
    out = {}
    for source, in conn.execute("SELECT DISTINCT source FROM drive_files ORDER BY source"):
        row = conn.execute("""
            SELECT COUNT(*) n,
                   SUM(CASE WHEN d.file_id IS NOT NULL THEN 1 ELSE 0 END) with_text,
                   SUM(CASE WHEN f.text_state LIKE 'unavailable%' THEN 1 ELSE 0 END) bad,
                   SUM(CASE WHEN f.text_state IS NULL THEN 1 ELSE 0 END) untouched
            FROM drive_files f LEFT JOIN document_text d ON d.file_id = f.id
            WHERE f.source = ?""", (source,)).fetchone()
        key = source or "drive"
        out[key] = {k: (row[k] or 0) for k in row.keys()}
        # `unread` is "fetchable and not yet read" — NOT every row without text. Media,
        # archives and folders have no text to get, and counting them as unread made
        # /health report 6,471 on a Drive where the real figure was 6. A number that
        # cannot be acted on stops being read, which is how the original gap hid.
        out[key]["unread"] = conn.execute(
            "SELECT COUNT(*) FROM drive_files f"
            " LEFT JOIN document_text d ON d.file_id = f.id"
            " WHERE d.file_id IS NULL AND f.indexable = 1 AND f.source = ?",
            (key,)).fetchone()[0]
        out[key]["not_fetchable"] = row["n"] - row["with_text"] - out[key]["unread"]
    return out


# ---------------------------------------------------------------- cli
def _print(hits, error, notes=()):
    if error:
        print(f"  !! {error}")
    for n in notes:
        print(f"  -- {n}")
    print(f"  {len(hits)} hit(s)")
    for h in hits:
        mark = "" if h["read"] else "  [NOT READ]"
        print(f"    {(h['modified_time'] or '')[:10]}  {h['source']:<10} "
              f"{h['name'][:52]}{mark}")
        if h["snippet"]:
            print(f"        ...{h['snippet'][:120]}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    conn = connect()
    cmd = argv[1]

    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    if cmd == "search":
        if len(argv) < 3:
            raise SystemExit("usage: document_search.py search <query>")
        hits, error, notes = search_archive(conn, argv[2], account=opt("--account"),
                                            limit=int(opt("--limit", 8)),
                                            source=opt("--source"))
        _print(hits, error, notes)
        return 0

    if cmd == "read":
        if len(argv) < 3:
            raise SystemExit("usage: document_search.py read <file-id>")
        d = read_document(conn, argv[2])
        if not d:
            print("  not in the index")
            return 1
        print(f"  {d['name']}  ({d['source']}, {d['pages']} page(s), "
              f"{len(d['body']):,} chars, via {d['text_source']})")
        print(d["body"][:3000])
        return 0

    if cmd == "stats":
        for source, s in stats(conn).items():
            print(f"  {source:<11} {s['n']:>6} rows, {s['with_text']:>6} with text,"
                  f" {s['unread']:>6} unread ({s['bad']} unavailable)")
        return 0

    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
