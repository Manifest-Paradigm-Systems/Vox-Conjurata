#!/usr/bin/env python3
"""Build one database per account, as a PROJECTION of google.db.

WHY A PROJECTION AND NOT A REPLACEMENT.

This was going to be a full split, with each indexer writing its own account and
mail-api reading four databases. Two measurements killed that:

  * bm25's corpus statistics are per-index, so `ORDER BY bm25(...) LIMIT n` selected a
    different candidate set in a one-account database than in the shared one — 8 of 14
    queries returned different messages.
  * Removing bm25 to make selection corpus-independent made the ranking depend on
    recency alone, which answered "david" with newsletters that merely contained the
    word. Search relevance came from bm25; the split was not worth losing it.

So google.db remains the single search/read store and Jarvis's recall is unchanged.
These per-account databases exist for OWNERSHIP AND BACKUP: each account's slice, in a
file that can be uploaded to that account's own Drive, so one domain's index is not
sitting in another account's cloud.

Read-only against google.db. Writes only into OUT_DIR, and builds each database to a
temp name first so a failure part-way cannot leave a half-written file in place.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

SRC = os.environ.get("JARVIS_GOOGLE_DB", "/home/admin/jarvis/google/google.db")
SCHEMA = os.environ.get("JARVIS_SCHEMA", "/home/admin/jarvis/google/schema.sql")
OUT_DIR = os.environ.get("JARVIS_ACCOUNTS_DIR", "/home/admin/jarvis/google/accounts")

# Every base table carrying an `account` column — derived from the live schema, not
# assumed. Copied with a straight WHERE account = ?.
ACCOUNTED = [
    "messages", "attachments", "drive_files", "document_text", "chunks", "sync_state",
    "actions", "calls", "sms", "calendar_events", "mms", "mms_parts", "contact_domain",
    "mms_text", "replied_addresses", "records_facts", "event_attendees",
]
# FTS tables that carry `account` directly. Loaded BY ROW so SQLite builds the index —
# the shadow tables are never copied; doing that corrupts the index in a way that reads
# as "no results" rather than as an error.
FTS_ACCOUNTED = ["message_fts", "document_fts"]
# No account column. These each need a decision, and these are the decisions.
KEEP_ALL = ["accounts", "exclusions", "controls"]   # registry and global config
# Filtered via a join to their owning row. Column names verified against the live
# schema: mms_fts carries `part_id` and so joins mms_parts (NOT mms).
JOINED = [
    ("message_text", "SELECT t.* FROM src.message_text t JOIN src.messages m"
                     " ON m.id = t.message_id WHERE m.account = ?"),
    ("attachment_text", "SELECT t.* FROM src.attachment_text t JOIN src.attachments a"
                        " ON a.id = t.attachment_id WHERE a.account = ?"),
    ("sms_fts", "SELECT t.* FROM src.sms_fts t JOIN src.sms s"
                " ON s.id = t.sms_id WHERE s.account = ?"),
    ("mms_fts", "SELECT t.* FROM src.mms_fts t JOIN src.mms_parts p"
                " ON p.id = t.part_id WHERE p.account = ?"),
    ("chunk_vectors", "SELECT t.* FROM src.chunk_vectors t JOIN src.chunks c"
                      " ON c.id = t.chunk_id WHERE c.account = ?"),
]
# Scratch, deliberately not carried into a per-account file.
DROPPED = ["_lockprobe"]


def columns(conn, table):
    return [r[1] for r in conn.execute(f"pragma table_info({table})")]


def build(account: str, verbose: bool = True) -> tuple[str, int]:
    slug = account.split("@")[0]
    final = os.path.join(OUT_DIR, f"{slug}.db")
    tmp = final + ".building"
    os.makedirs(OUT_DIR, exist_ok=True)
    for stale in (tmp, tmp + "-wal", tmp + "-shm"):
        if os.path.exists(stale):
            os.remove(stale)

    dst = sqlite3.connect(tmp)
    dst.executescript(open(SCHEMA).read())
    dst.execute("ATTACH DATABASE ? AS src", (SRC,))
    dst.execute("PRAGMA foreign_keys=OFF")

    def copy(table: str, sql: str, args: tuple = ()) -> int:
        cols = columns(dst, table)
        before = dst.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        dst.execute(f"INSERT INTO {table} ({','.join(cols)}) {sql}", args)
        after = dst.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        moved = after - before
        if verbose:
            print(f"    {table:20s} {moved:>9,d}")
        return moved

    t0 = time.time()
    for table in ACCOUNTED:
        cols = columns(dst, table)
        copy(table, f"SELECT {','.join(cols)} FROM src.{table} WHERE account = ?",
             (account,))
    for table in FTS_ACCOUNTED:
        cols = columns(dst, table)
        copy(table, f"SELECT {','.join(cols)} FROM src.{table} WHERE account = ?",
             (account,))
    for table in KEEP_ALL:
        cols = columns(dst, table)
        copy(table, f"SELECT {','.join(cols)} FROM src.{table}")
    for table, sql in JOINED:
        copy(table, sql, (account,))

    dst.commit()
    dst.execute("DETACH DATABASE src")
    dst.commit()
    dst.execute("VACUUM")           # one clean file, no WAL to carry along
    dst.close()

    os.replace(tmp, final)          # atomic: never a half-built database in place
    return final, int((time.time() - t0) * 1000)


def verify(slug: str, account: str) -> list[str]:
    """Compare against the source. A count that matches while an index is empty looks
    exactly like success, so the FTS tables are checked separately."""
    problems = []
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    dst = sqlite3.connect(f"file:{os.path.join(OUT_DIR, slug + '.db')}?mode=ro", uri=True)

    want = src.execute("SELECT count(*) FROM messages WHERE account=?",
                       (account,)).fetchone()[0]
    got = dst.execute("SELECT count(*) FROM messages").fetchone()[0]
    if want != got:
        problems.append(f"messages: source {want} vs built {got}")

    want = src.execute("SELECT count(*) FROM message_fts WHERE account=?",
                       (account,)).fetchone()[0]
    got = dst.execute("SELECT count(*) FROM message_fts").fetchone()[0]
    if want != got:
        problems.append(f"message_fts: source {want} vs built {got}")

    # A known query must return rows — an empty FTS index is the silent failure.
    hits = dst.execute(
        "SELECT count(*) FROM message_fts WHERE message_fts MATCH 'the'").fetchone()[0]
    if hits == 0 and got > 0:
        problems.append("message_fts MATCH returns nothing while rows exist")
    dst.close()
    return problems


def main() -> int:
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    accounts = [r[0] for r in src.execute(
        "SELECT email FROM accounts ORDER BY email")]
    if not accounts:
        accounts = [r[0] for r in src.execute(
            "SELECT DISTINCT account FROM messages WHERE account IS NOT NULL"
            " ORDER BY account")]
    print(f"building {len(accounts)} account database(s) from {SRC}\n")

    failed = 0
    for account in accounts:
        print(f"  {account}")
        path, ms = build(account)
        size = os.path.getsize(path)
        problems = verify(account.split("@")[0], account)
        if problems:
            failed += 1
            print(f"    !! {size/1e6:.1f} MB in {ms} ms — VERIFY FAILED")
            for p in problems:
                print(f"       {p}")
        else:
            print(f"    ok  {size/1e6:.1f} MB in {ms} ms — counts match the source")
        print()

    print("FAILED" if failed else "all databases built and verified")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
