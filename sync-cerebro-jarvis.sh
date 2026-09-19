#!/bin/bash
# Pull the live Jarvis tree off cerebro into the versioned mirror.
#
# This is a BACKUP, and it only ever reads from cerebro. The previous mechanism
# is gone entirely (git-failsafe.sh is a local commit/push daemon and never did
# the copying; there is no rsync in the repo, no cron and no timer on either
# box), so the mirror sat frozen at 2026-09-13 while the live panel moved on.
#
# WHY tar AND NOT rsync: cerebro is Fedora IoT 44 on rpm-ostree — /usr is
# read-only, rsync is not installed anywhere, and `rpm-ostree install rsync`
# would need a reboot of a box serving Jarvis, Matrix and Open WebUI. tar ships
# with it and needs nothing added. tar preserves mtimes, so unchanged files land
# with identical timestamps and git-failsafe sees no spurious diff.
#
# Known limit: this does not delete files that were removed on cerebro. A mirror
# that accumulates is the safer failure for a backup, but it means the mirror can
# hold stale files — it is not a byte-exact replica.
set -euo pipefail

HOST=${HOST:-cerebro}
REMOTE=${REMOTE:-/var/home/admin/jarvis}
DEST="${DEST:-$HOME/vox-conjurata/cerebro-mirror/jarvis}"
UNIT_SRC=${UNIT_SRC:-/var/home/admin/.config/systemd/user}
UNIT_DEST="$DEST/systemd-user"

mkdir -p "$DEST" "$UNIT_DEST"

# Excluded, each for a reason:
#   .venv, .aider-venv   70M of virtualenvs, rebuildable
#   conversations.db*    live SQLite — a tar of an open DB is a torn copy
#   viz-bus              runtime state, rewritten constantly
#   *.tgz                nested archives would compound the repo
#   __pycache__, *.pyc   bytecode
#   node_modules         rebuildable
#   *.log                noise
#   *.bak, *.bak-*       editor backups; git already keeps every prior version,
#                        so mirroring them duplicates history as clutter
# The conversation DB is deliberately NOT here; it is data, not code, and putting
# a 33M binary in a repo that auto-commits would bloat history. Back it up
# separately (sqlite3 .backup) if it matters.
# tar exits 1 for "file changed as we read it", which on a live tree is NORMAL —
# Jarvis is writing to this directory the whole time we read it. Under `set -e`
# that benign status aborted the run, intermittently and only when a write
# happened to land mid-read, which is the worst way for a backup to fail: it
# looked like it worked. Exit 1 is therefore accepted; only >=2 is fatal.
pull_tree() {   # pull_tree <remote-cmd> <dest>
    local rc=0
    ssh "$HOST" "$1" 2>/dev/null | tar xzf - -C "$2" --warning=no-file-changed || rc=$?
    if [ "$rc" -gt 1 ]; then
        echo "[$(date -Is)] ERROR: tar failed with status $rc" >&2
        return "$rc"
    fi
    [ "$rc" = 1 ] && echo "[$(date -Is)]   (tar noted a file changed mid-read; benign)"
    return 0
}

echo "[$(date -Is)] pulling $HOST:$REMOTE -> $DEST"
pull_tree "tar czf - -C '$REMOTE' \
    --warning=no-file-changed \
    --exclude=.venv --exclude=.aider-venv \
    --exclude='conversations.db*' \
    --exclude=viz-bus \
    --exclude=__pycache__ --exclude='*.pyc' \
    --exclude=node_modules \
    --exclude='*.log' --exclude='*.tgz' \
    --exclude='*.bak' --exclude='*.bak-*' \
    ." "$DEST"

# The systemd USER units and their drop-ins — the half that was never backed up.
# The drop-ins are where Jarvis's voice, TTS routing and devteam environment
# actually live (jarvis-docks.service.d/tts.conf holds JARVIS_VOICE and the
# Kokoro URLs), so a restore without them silently reverts him to the module
# defaults in panel.py. `find -print0 | tar --null -T -` rather than shell
# globs, so a missing drop-in cannot make tar fail the whole run.
echo "[$(date -Is)] pulling systemd user units -> $UNIT_DEST"
pull_tree "cd '$UNIT_SRC' && find . -maxdepth 2 -name 'jarvis-*' -print0 \
    | tar czf - --warning=no-file-changed --null -T -" "$UNIT_DEST"

# --- the conversation database ---------------------------------------------
# Deliberately NOT in the git mirror above: the DB is ~33M (mostly the `runs`
# table's devteam transcripts) and a live SQLite file copied while open is a
# torn read. VACUUM INTO writes a consistent, compacted copy without blocking
# the writers, which is the only safe way to snapshot a database in use.
# Kept outside git, as dated full copies, with retention.
DB_REMOTE=${DB_REMOTE:-/var/home/admin/jarvis/conversations.db}
DB_DEST=${DB_DEST:-$HOME/vox-conjurata/jarvis-db-backups}
DB_KEEP=${DB_KEEP:-14}
mkdir -p "$DB_DEST"

SNAP_BYTES=$(cat <<PY | ssh "$HOST" python3 -
import sqlite3, os
src, out = "$DB_REMOTE", "/tmp/conv-snapshot.db"
if os.path.exists(out):
    os.remove(out)
c = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
c.execute(f"VACUUM INTO '{out}'")        # consistent + compacted, schema and all
c.close()

# Empty `runs` in the COPY. It is the devteam's black box — every model call's
# full prompt and output, ~17.6 KB a row and 96% of the file — and essentially
# nothing reads the text back (the board takes metadata only; devteam reads
# `output` only WHERE ok=0). The conversations we actually care about are 48 KB.
# The table and its schema stay, so a restore still has a valid DB; it just has
# no devteam history. Set DB_KEEP_RUNS=1 to keep it.
if os.environ.get("DB_KEEP_RUNS") != "1":
    d = sqlite3.connect(out)
    d.execute("DELETE FROM runs")
    d.commit()
    d.execute("VACUUM")
    d.close()
print(os.path.getsize(out))
PY
)
STAMP=$(date +%Y%m%d-%H%M%S)
scp -q "$HOST:/tmp/conv-snapshot.db" "$DB_DEST/conversations-$STAMP.db"
ssh "$HOST" 'rm -f /tmp/conv-snapshot.db'
gzip -f "$DB_DEST/conversations-$STAMP.db"
# keep the newest N snapshots
ls -1t "$DB_DEST"/conversations-*.db.gz 2>/dev/null | tail -n +$((DB_KEEP + 1)) | xargs -r rm -f
echo "[$(date -Is)] conversation snapshot ${SNAP_BYTES:-?}B -> conversations-$STAMP.db.gz"
