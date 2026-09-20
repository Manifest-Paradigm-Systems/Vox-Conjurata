"""Bring the Army service record into the mnmeyer index.

The record lives in two places that have never met. `~/.lifepacket/life_records.db` on
Workhorse knows the 174 documents by name, type and date — and holds a page-1-only,
upper-cased transcription of each. The raw scans sit beside it in
`LifePacket-AI/raw_documents/Meyer-ArmyServiceRecord/`. Nothing in `google/` or `brain/`
has ever referenced either, so Jarvis could not answer a single question about the
owner's military service.

This module re-reads the RAW files and writes them into the mnmeyer index, where the
same search, the same read path and the same backup already exist.

WHY THE RAW FILES AND NOT `processed/`, WHICH IS WHAT THE DATABASE POINTS AT.

`core/ingestion.py` converts images with `Image.open(p).save(out, "PDF")` and no
`ImageSequence`, so every multi-frame TIFF was flattened to its first frame before OCR
ever ran. Measured across this record: 100 TIFFs, 43 of them multi-frame, 215 frames in
total — against 174 documents recorded as complete. About 190 pages were never read at
any stage, and nothing anywhere said so. `core/ocr_engine.py` then reads
`reader.pages[0]` only, which finished the job.

So the source of record here is `raw_documents/`, and every frame of every file is read.
`processed/` (114 MB) and `filed/` (135 MB) are derived duplicates and are not touched.

WHY THE `ocr_raw_text` COLUMN IS DELIBERATELY NOT IMPORTED. It is the same page-1-only
read, upper-cased, and `document_text.file_id` is a primary key — so importing it would
either collide with the real text or, worse, sit there looking complete while 190 pages
were absent. An import that silently drops two thirds of a service record is exactly the
failure this whole piece of work exists to remove.

THE FACTS ARE A SEPARATE STEP. `profile_data_history` holds model output rather than
quoted spans, and its `is_current` is decided by write order — which is how `home_city`
came to be stored as "Peton" when ten documents say PEYTON, CO, and why the rank answer
flips between MAJ and CPT depending on which run wrote last. `lifepacket_facts.py
rebuild` regenerates that ledger from these documents; this module only supplies the
documents.

WHERE IT RUNS. Extraction happens on Workhorse, which has poppler and tesseract
natively. The result is a small fragment database; `apply` merges it into the live index
on cerebro inside one transaction. The 990 MB index is never copied across the network.

Usage:
    python3 lifepacket_import.py plan                    # what would be read
    python3 lifepacket_import.py run  [--limit N]        # extract -> fragment db
    python3 lifepacket_import.py apply <fragment.db>     # merge (on cerebro)
    python3 lifepacket_import.py verify                  # coverage after apply
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ocr_ladder  # noqa: E402

ACCOUNT = os.getenv("JARVIS_LIFEPACKET_ACCOUNT", "mnmeyer@gmail.com")
LIFE_DB = os.path.expanduser(os.getenv("JARVIS_LIFEPACKET_DB",
                                       "~/.lifepacket/life_records.db"))
RAW_DIR = os.path.expanduser(os.getenv(
    "JARVIS_LIFEPACKET_RAW",
    "~/LifePacket-AI/raw_documents/Meyer-ArmyServiceRecord"))

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))

# The watchdog flag. A long OCR run is a heavy job by this fleet's definition, and the
# rule is that heavy jobs yield to it between batches rather than fighting the machine
# for resources while something else needs them.
SUSPEND = "/tmp/cinematome-suspend"

MIME_BY_EXT = {
    ".pdf": "application/pdf", ".tif": "image/tiff", ".tiff": "image/tiff",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".html": "text/html", ".htm": "text/html", ".xhtml": "application/xhtml+xml",
    ".xml": "text/plain", ".txt": "text/plain",
}


def life_db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{LIFE_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def index_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=180)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 180000")
    return conn


# ---------------------------------------------------------------- what to read
def documents() -> list[dict]:
    """The work list: every inventory row, resolved to a file in raw_documents/.

    The inventory is the authority for type and date — it is the curated set, one row
    per real document (the 101 image rows point at a converted PDF, but `original_name`
    still names the raw file, which is what is actually read here).
    """
    raw = {os.path.basename(p): p for p in glob.glob(os.path.join(RAW_DIR, "*"))}
    out = []
    for row in life_db().execute("SELECT * FROM document_inventory ORDER BY id"):
        name = row["original_name"]
        path = raw.get(name)
        if not path:
            out.append({"id": row["id"], "name": name, "path": None,
                        "doc_type": row["doc_type"], "date": row["document_date"],
                        "missing": True})
            continue
        out.append({
            "id": row["id"],
            "name": name,
            "path": path,
            "doc_type": row["doc_type"] or "Unsorted",
            "date": row["document_date"] or "",
            "hash": row["file_hash"] or "",
            "mime": MIME_BY_EXT.get(os.path.splitext(name)[1].lower(), ""),
        })
    return out


def fragment_schema(conn: sqlite3.Connection) -> None:
    """The staging shape. Deliberately mirrors the destination columns so `apply` is a
    straight INSERT ... SELECT and cannot drift into a second interpretation."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS docs (
            file_id TEXT PRIMARY KEY, account TEXT, name TEXT, mime_type TEXT,
            modified_time TEXT, size INTEGER, doc_type TEXT, document_date TEXT,
            text_state TEXT, origin_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS bodies (
            file_id TEXT PRIMARY KEY, account TEXT, body TEXT, text_source TEXT,
            pages INTEGER
        );
        CREATE TABLE IF NOT EXISTS fts_rows (
            file_id TEXT PRIMARY KEY, account TEXT, name TEXT, body TEXT
        );
        CREATE TABLE IF NOT EXISTS run_info (k TEXT PRIMARY KEY, v TEXT);
    """)


def _iso(date: str) -> str:
    """Drive stores RFC3339. The inventory stores YYYY-MM-DD. Match the drive shape so
    ordering by modified_time means the same thing across both sources."""
    return f"{date}T00:00:00.000Z" if date else ""


def run(limit: int | None = None, out_path: str = "/tmp/lifepacket-fragment.db") -> int:
    docs = documents()
    missing = [d for d in docs if d.get("missing")]
    todo = [d for d in docs if not d.get("missing")]
    if limit:
        todo = todo[:limit]

    print(f"  {len(docs)} documents in the inventory"
          f"{f', {len(missing)} with no file in raw_documents/' if missing else ''}")
    for d in missing:
        print(f"    MISSING: {d['name']}")
    print(f"  extracting {len(todo)} from {RAW_DIR}")

    if os.path.exists(out_path):
        os.remove(out_path)
    frag = sqlite3.connect(out_path)
    fragment_schema(frag)

    started = time.time()
    pages_total = 0
    ok = empty = unavailable = 0
    for i, d in enumerate(todo, 1):
        if os.path.exists(SUSPEND):
            print(f"  SUSPENDED at {i}/{len(todo)} — {SUSPEND} exists. "
                  f"Re-run to continue; finished documents are already written.")
            break

        # ALL PAGES, and the page count is not capped: the point of this pass is the
        # pages the old one never reached.
        text, source, error = ocr_ladder.extract_path(d["path"], d["mime"],
                                                      max_pages=0, mark_pages=True)
        pages = text.count("[page ") or (1 if text else 0)
        pages_total += pages
        if error:
            state = error
            unavailable += 1
        elif text:
            state = "ok"
            ok += 1
        else:
            state = "empty"
            empty += 1

        size = os.path.getsize(d["path"]) if os.path.exists(d["path"]) else 0
        file_id = f"lifepacket:{d['hash']}"
        # The doc type rides in the FTS name, not the display name: it becomes a
        # searchable token ("promotion", "awards", "dd214") at no schema cost, and
        # drive_files.name stays the honest filename.
        fts_name = f"{d['name']} [{d['doc_type']}]"

        with frag:
            frag.execute("INSERT OR REPLACE INTO docs (file_id, account, name, mime_type,"
                         " modified_time, size, doc_type, document_date, text_state,"
                         " origin_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (file_id, ACCOUNT, d["name"], d["mime"], _iso(d["date"]),
                          size, d["doc_type"], d["date"], state, d["id"]))
            if text:
                frag.execute("INSERT OR REPLACE INTO bodies (file_id, account, body,"
                             " text_source, pages) VALUES (?,?,?,?,?)",
                             (file_id, ACCOUNT, text, source or "", pages))
                frag.execute("INSERT OR REPLACE INTO fts_rows (file_id, account, name,"
                             " body) VALUES (?,?,?,?)",
                             (file_id, ACCOUNT, fts_name, text[:400000]))

        if i % 25 == 0 or i == len(todo):
            el = time.time() - started
            print(f"    {i}/{len(todo)}  ok={ok} empty={empty} unreadable={unavailable}"
                  f"  pages={pages_total}  {el:.0f}s", flush=True)

    with frag:
        frag.execute("INSERT OR REPLACE INTO run_info (k, v) VALUES (?,?)",
                     ("finished_at", str(time.time())))
        frag.execute("INSERT OR REPLACE INTO run_info (k, v) VALUES (?,?)",
                     ("raw_dir", RAW_DIR))
    frag.close()

    el = time.time() - started
    print(f"\n  wrote {out_path}")
    print(f"  {ok} read, {empty} with no text, {unavailable} unreadable,"
          f" {pages_total} pages, {el:.0f}s")
    if unavailable:
        print("  unreadable documents are recorded with the reason, not as empty —"
              " install the tool and re-run to pick them up")
    return ok


# ---------------------------------------------------------------- merge into the index
def apply_fragment(path: str) -> int:
    """Merge the fragment into the live index, in one transaction.

    Attached rather than parsed: the fragment is a database, so this is INSERT ... SELECT
    and nothing re-interprets the text on the way through.
    """
    if not os.path.exists(path):
        raise SystemExit(f"no fragment at {path}")
    conn = index_db()
    conn.execute("ATTACH DATABASE ? AS frag", (path,))
    have = {r[1] for r in conn.execute("PRAGMA table_info(drive_files)")}
    for col in ("source", "text_state"):
        if col not in have:
            raise SystemExit(f"drive_files.{col} is missing — apply the schema change first")
    if not conn.execute("SELECT 1 FROM accounts WHERE email=?", (ACCOUNT,)).fetchone():
        raise SystemExit(f"{ACCOUNT} is not in the accounts table")

    docs = conn.execute("SELECT COUNT(*) FROM frag.docs").fetchone()[0]
    bodies = conn.execute("SELECT COUNT(*) FROM frag.bodies").fetchone()[0]
    print(f"  fragment: {docs} documents, {bodies} with text")

    try:
        with conn:
            cur = conn.execute("""
                INSERT OR REPLACE INTO drive_files
                    (id, account, name, mime_type, modified_time, size, owners, trashed,
                     indexable, status, source, text_state, indexed_at)
                SELECT file_id, account, name, mime_type, modified_time, size, '[]', 0,
                       1, 'active', 'lifepacket', text_state, ?
                FROM frag.docs""", (time.time(),))
            n_docs = cur.rowcount

            # The FTS row is replaced, not appended: a re-import of the same document
            # must not leave the previous transcription searchable alongside the new one.
            conn.execute("DELETE FROM document_fts WHERE file_id IN"
                         " (SELECT file_id FROM frag.docs)")
            conn.execute("DELETE FROM document_text WHERE file_id IN"
                         " (SELECT file_id FROM frag.docs)")
            cur = conn.execute("""
                INSERT OR REPLACE INTO document_text (file_id, account, body, source)
                SELECT file_id, account, body, 'lifepacket:' || text_source
                FROM frag.bodies""")
            n_bodies = cur.rowcount
            cur = conn.execute("""
                INSERT INTO document_fts (file_id, account, name, body)
                SELECT file_id, account, name, body FROM frag.fts_rows""")
            n_fts = cur.rowcount
    finally:
        conn.execute("DETACH DATABASE frag")

    print(f"  merged: {n_docs} drive_files, {n_bodies} document_text, {n_fts} document_fts")
    verify(conn)
    return n_docs


def verify(conn: sqlite3.Connection | None = None) -> None:
    """Coverage for the records specifically — the number that says whether the import
    actually landed, and whether anything is still unread."""
    conn = conn or index_db()
    row = conn.execute("""
        SELECT COUNT(*) n,
               SUM(CASE WHEN d.file_id IS NOT NULL THEN 1 ELSE 0 END) with_text,
               SUM(CASE WHEN f.text_state LIKE 'unavailable%' THEN 1 ELSE 0 END) unread
        FROM drive_files f LEFT JOIN document_text d ON d.file_id = f.id
        WHERE f.source = 'lifepacket'""").fetchone()
    print(f"  service records: {row['n']} documents, {row['with_text']} with text"
          f"{f', {row['unread']} unreadable' if row['unread'] else ''}")
    by_type = conn.execute("""
        SELECT f.name, LENGTH(d.body) n FROM drive_files f
        JOIN document_text d ON d.file_id = f.id
        WHERE f.source = 'lifepacket' ORDER BY n DESC LIMIT 3""").fetchall()
    for r in by_type:
        print(f"    largest: {r['n']:>7,} chars  {r['name'][:64]}")


# ---------------------------------------------------------------- cli
def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]

    if cmd == "plan":
        docs = documents()
        missing = [d for d in docs if d.get("missing")]
        print(f"  {len(docs)} inventory rows, {len(docs) - len(missing)} resolvable")
        print(f"  raw dir: {RAW_DIR}")
        probe = ocr_ladder.probe()
        print(f"  ladder : {json.dumps(probe)}")
        for d in docs[:10]:
            print(f"    {(d['doc_type'] or '?'):<11s}  {(d['date'] or ''):<10s}"
                  f"  {d['name'][:60]}")
        print("    ...")
        return 0

    if cmd == "run":
        limit = None
        out = "/tmp/lifepacket-fragment.db"
        if "--limit" in argv:
            limit = int(argv[argv.index("--limit") + 1])
        if "--out" in argv:
            out = argv[argv.index("--out") + 1]
        run(limit, out)
        return 0

    if cmd == "apply":
        if len(argv) < 3:
            raise SystemExit("usage: lifepacket_import.py apply <fragment.db>")
        apply_fragment(argv[2])
        return 0

    if cmd == "verify":
        verify()
        return 0

    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
