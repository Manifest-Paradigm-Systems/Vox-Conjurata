#!/bin/bash
# Ship the Google index databases to Google Drive. Installed but NOT enabled.
#
# Cadence: monthly, on the 1st (timer in ../systemd/). The upload is a full file
# every time — Drive has no delta protocol, so daily would re-send ~500 MB for a
# database that changes slowly. Monthly is the owner's call and it fits.
#
# ONE FILE, ALWAYS REPLACED. Each account has exactly one object in its own
# Drive (the agreed per-account design), and each run replaces it. Done as
# upload-then-rename rather than overwrite-in-place, so the live object is never
# a half-written file: a failure mid-upload leaves the previous month intact.
#
# The residual risk of a single slot is that a BAD snapshot replaces a GOOD one —
# corruption at snapshot time is copied over your only offsite copy, silently.
# $JARVIS_DRIVE_KEEP_PREV=1 keeps one previous generation by moving the old
# object aside first. Cheap (one extra ~150 MB) and it turns "we lost everything"
# into "we lost a month". Off by default because the owner asked for one file.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DB="${JARVIS_GOOGLE_DB:-$HOME/jarvis/google/google.db}"
REMOTE="${JARVIS_DRIVE_REMOTE:-}"          # e.g. gdrive:mnmeyer-JarvisBackups

# NAMING CONVENTION (owner, 2026-09-18): <account-local-part>-Jarvis<What>DB.
# The account prefix is the point — with four accounts each backing up to its own
# Drive, a bare "google.db" would be four identical names in four places, and
# nothing on sight would say which account a file came from.
#   mnmeyer@gmail.com       -> mnmeyer-JarvisDB
#   meyerfamily813@gmail.com-> meyerfamily813-JarvisDB
# The account comes from the database's own accounts table, so the name cannot
# drift from the contents.
ACCT="${JARVIS_DRIVE_ACCOUNT:-$(python3 - "$DB" <<'PY' 2>/dev/null || echo ""
import sqlite3, sys
try:
    c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    r = c.execute("SELECT email FROM accounts ORDER BY rowid LIMIT 1").fetchone()
    print((r[0] if r else "").split("@")[0])
except Exception:
    print("")
PY
)}"
[ -z "$ACCT" ] && ACCT="${JARVIS_GOOGLE_ACCOUNT%%@*}"
WHAT="${JARVIS_DRIVE_WHAT:-}"              # e.g. Conversations, or empty for the index
NAME="${JARVIS_DRIVE_NAME:-${ACCT}-Jarvis${WHAT}DB}"
KEEP_PREV="${JARVIS_DRIVE_KEEP_PREV:-0}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

if [ -z "$REMOTE" ]; then
    echo "JARVIS_DRIVE_REMOTE is not set — nothing to upload to." >&2
    echo "Run 'rclone config' (interactive, owner-only) and set it per account." >&2
    exit 2
fi
command -v rclone >/dev/null || { echo "rclone not installed" >&2; exit 2; }
[ -f "$DB" ] || { echo "no database at $DB" >&2; exit 2; }

SNAP="$STAGE/$NAME"
echo "[$(date -Is)] snapshotting $DB"
# VACUUM INTO, not cp: the indexers write to this file continuously and a plain
# copy of an open SQLite database is a torn read that may not even open.
python3 - "$DB" "$SNAP" <<'PY'
import sqlite3, sys
src, out = sys.argv[1], sys.argv[2]
c = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
c.execute(f"VACUUM INTO '{out}'")
c.close()
PY

echo "[$(date -Is)] verifying the snapshot before it replaces anything"
python3 - "$SNAP" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
r = c.execute("PRAGMA integrity_check").fetchone()[0]
# NOT `SELECT count(*) FROM messages`. That is what this did, and it made the script
# unusable for anything that is not the mail index: verifying life_records.db — a
# perfectly good database with no `messages` table — raised, so the run exited non-zero
# AFTER taking a valid snapshot and BEFORE uploading it. The object simply never
# appeared, and the log said the problem was the database rather than the check.
# Count the tables instead: it works on any SQLite file and still proves the snapshot
# opens and can be read.
tables = [r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
counts = {}
for t in tables:
    if t.startswith("sqlite_"):
        continue
    try:
        counts[t] = c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    except sqlite3.Error:
        counts[t] = None
c.close()
if r != "ok":
    raise SystemExit(f"integrity_check said {r!r} — refusing to upload a bad snapshot")
if not tables:
    raise SystemExit("the snapshot has no tables at all — refusing to upload it")
biggest = sorted(((v or 0, k) for k, v in counts.items()), reverse=True)[:3]
summary = ", ".join(f"{k}={v}" for v, k in biggest)
print(f"  integrity ok, {len(tables)} tables ({summary})")
PY

gzip -9 "$SNAP"
LOCAL="$SNAP.gz"
BYTES=$(stat -c%s "$LOCAL")
echo "[$(date -Is)] uploading $(numfmt --to=iec "$BYTES")"

# Upload under a partial name, confirm it arrived at full size, THEN rename over
# the live object. rclone moveto on Drive is a rename, so the window where the
# real object could be wrong is effectively nil.
rclone copyto "$LOCAL" "$REMOTE/$NAME.gz.partial" --retries 3 --low-level-retries 10
GOT=$(rclone size "$REMOTE/$NAME.gz.partial" --json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('bytes',0))" 2>/dev/null || echo 0)
if [ "$GOT" != "$BYTES" ]; then
    echo "  uploaded size $GOT != local $BYTES — leaving the previous object in place" >&2
    rclone deletefile "$REMOTE/$NAME.gz.partial" 2>/dev/null || true
    exit 4
fi

if [ "$KEEP_PREV" = "1" ]; then
    rclone moveto "$REMOTE/$NAME.gz" "$REMOTE/$NAME.prev.gz" 2>/dev/null || true
fi
rclone moveto "$REMOTE/$NAME.gz.partial" "$REMOTE/$NAME.gz"
echo "[$(date -Is)] replaced $REMOTE/$NAME.gz ($(numfmt --to=iec "$BYTES"))"
