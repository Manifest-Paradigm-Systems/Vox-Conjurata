#!/usr/bin/env python3
"""Regenerate schema.sql from the LIVE database.

The hand-maintained schema.sql had drifted: `account` was missing from sms, mms,
mms_parts and calls, three columns were missing from attachments, and drive_files had
its columns in a different order. Building the per-account databases from it would have
silently dropped the phone attribution — the failure would have been missing columns,
not an error.

sqlite_master is the only description that cannot drift, because it IS the database.
Rows are emitted in creation order (rowid), which respects the foreign-key dependencies
between them. FTS5 shadow tables are skipped: they are created by the virtual table
itself and are not part of a schema you would ever write by hand.
"""
import sqlite3
import sys

SRC = "/home/admin/jarvis/google/google.db"
OUT = "/home/admin/jarvis/google/schema.sql"

SHADOW_SUFFIXES = ("_data", "_idx", "_content", "_docsize", "_config")

src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)

rows = src.execute(
    "SELECT type, name, tbl_name, sql FROM sqlite_master "
    "WHERE sql IS NOT NULL ORDER BY rowid").fetchall()

shadow = {name for typ, name, _, _ in rows
          if typ == "table" and name.endswith(SHADOW_SUFFIXES)}

body = []
tables = indexes = 0
for typ, name, tbl_name, sql in rows:
    if typ == "table":
        if name in shadow or name.startswith("sqlite_"):
            continue
        body.append(sql.rstrip(";") + ";")
        tables += 1
    elif typ == "index":
        # Auto-indexes ("sqlite_autoindex_*") have no SQL. Shadow indexes belong to FTS.
        if name.startswith("sqlite_") or tbl_name in shadow:
            continue
        body.append(sql.rstrip(";") + ";")
        indexes += 1

header = f"""-- schema.sql — GENERATED FROM THE LIVE DATABASE, do not hand-edit.
--
-- Regenerated on the live google.db rather than maintained by hand, because the
-- hand-written copy had drifted: `account` was missing from sms, mms, mms_parts and
-- calls, and three columns were missing from attachments. A database built from it
-- would have been missing the phone attribution entirely — and a missing COLUMN fails
-- silently in an INSERT that names only the columns it knows about.
--
-- Regenerate with:  python3 regen_schema.py
--
-- Tables: {tables}   Indexes: {indexes}
"""

with open(OUT, "w", encoding="utf-8") as fh:
    fh.write(header + "\n" + "\n".join(body) + "\n")

print(f"wrote {OUT}: {tables} tables, {indexes} indexes")
