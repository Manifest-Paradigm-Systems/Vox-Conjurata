#!/usr/bin/env python3
"""Scrub tombstoned conversations out of the conversation-database snapshots.

WHY THIS EXISTS. Deleting a chat from the panel removes it from the live
database — turns, the standalone copy inside turns_fts, the facts distilled from
it, the archive chunks, the list row. But every snapshot taken BEFORE that delete
still contains the whole conversation, and snapshots rotate out only slowly
(DB_KEEP=14 at a 15-minute cadence, so roughly three and a half hours).

Without this, "delete" would quietly mean "delete in three and a half hours",
which is not what someone asking for a conversation to be forgotten has in mind.
Run on every sync, this closes that window to one cycle.

The live database is the source of truth; this only ever shrinks snapshots to
match it. It writes nothing to the live database and never creates a snapshot.

Usage:  purge-deleted-snapshots.py <snapshot-dir> <tombstone-file>

Exit 0 always unless something is genuinely broken — a failed purge must not
abort the backup that is running around it.
"""

from __future__ import annotations

import gzip
import os
import sqlite3
import sys
import tempfile

# Every table that can hold a conversation, and the column naming it. Miss one
# and the conversation survives the purge in a place nothing looks at — which is
# exactly the failure this file exists to prevent.
TABLES = (
    ("turns", "session"),
    ("turns_fts", "session"),          # standalone FTS: its own full copy of the text
    ("facts", "source_session"),       # what memory extraction learned from it
    ("archive_chunks", "session"),
    ("sessions", "key"),
)


def purge_one(path: str, keys: list[str]) -> int:
    """Remove `keys` from one snapshot. Returns the number of rows destroyed.

    Rewrites via a temp file and os.replace, so a crash mid-write cannot leave a
    truncated snapshot. A corrupt backup is worse than a stale one: it is
    discovered at restore time, which is the worst possible moment.
    """
    work = tempfile.mktemp(suffix=".db", dir=os.path.dirname(path))
    try:
        with gzip.open(path, "rb") as src, open(work, "wb") as dst:
            dst.write(src.read())

        conn = sqlite3.connect(work)
        try:
            marks = ",".join("?" * len(keys))
            removed = 0
            for table, column in TABLES:
                try:
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE {column} IN ({marks})", keys)
                    removed += cur.rowcount
                except sqlite3.Error:
                    # A snapshot predating a table simply does not have it. That
                    # is not an error; it means there is nothing to purge there.
                    continue
            if not removed:
                return 0
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()

        tmp_gz = path + ".tmp"
        with open(work, "rb") as src, gzip.open(tmp_gz, "wb") as dst:
            dst.write(src.read())
        os.replace(tmp_gz, path)
        os.chmod(path, 0o600)
        return removed
    finally:
        try:
            os.unlink(work)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    snap_dir, tomb_file = argv[1], argv[2]

    try:
        with open(tomb_file) as fh:
            keys = [line.strip() for line in fh if line.strip()]
    except OSError as exc:
        print(f"  no tombstone file ({exc}) — nothing to purge")
        return 0
    if not keys:
        return 0

    snaps = sorted(f for f in os.listdir(snap_dir)
                   if f.startswith("conversations-") and f.endswith(".db.gz"))
    total, touched = 0, 0
    for name in snaps:
        path = os.path.join(snap_dir, name)
        try:
            n = purge_one(path, keys)
        except Exception as exc:                       # noqa: BLE001
            # Reported, never fatal: the backup around this must still complete,
            # and the live database is already correct either way.
            print(f"  !! could not purge {name}: {type(exc).__name__}: {exc}")
            continue
        if n:
            total += n
            touched += 1
    if total:
        print(f"  purged {total} row(s) across {touched} snapshot(s) "
              f"for {len(keys)} deleted conversation(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
