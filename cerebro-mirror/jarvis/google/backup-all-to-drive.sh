#!/bin/bash
# Everything that has no second copy, in one monthly run.
#
# WHY THERE IS MORE THAN ONE DATABASE HERE. `google.db` is the big one and the
# replaceable one — its own schema says so: GMAIL IS THE SOURCE OF TRUTH, THIS IS A VIEW,
# and every row can be re-fetched by re-running the crawlers. The other two cannot be
# rebuilt from anywhere:
#
#   conversations.db   Jarvis's own memory — turns, facts, plans. Nothing upstream has it.
#   life_records.db    the Army service record ledger. The documents behind it live on
#                      Workhorse and are uploaded separately below.
#
# Neither had a backup of any kind before this script.
#
# CONVERSATIONS.DB IS UPLOADED WHOLE, `runs` AND ALL. Stripping that table was the plan
# for a while on the grounds that it is devteam telemetry — and it is, 1,871 rows of it.
# Measured: the whole database gzips to 4.9 MB and without `runs` to 0.2 MB, so the
# saving is 4.7 MB of a monthly upload. Throwing away history to save 4.7 MB is not a
# trade worth making, and a backup that quietly drops a table is one you have to remember
# the shape of before you can trust it.
#
# ONE FAILURE MUST NOT STOP THE OTHERS, hence no `set -e` around the loop. If google.db
# fails to upload, the conversations backup still has to happen — the failure mode of a
# script that gives up is that you lose the thing you needed most because of the thing
# you could re-crawl.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BACKUP="$HERE/backup-to-drive.sh"
# Workhorse's scans of the service record, copied here so one host runs every backup.
RAW_DOCS="${JARVIS_LIFEPACKET_RAW_ON_CEREBRO:-$HOME/jarvis/lifepacket/raw_documents}"

if [ -z "${JARVIS_DRIVE_REMOTE:-}" ]; then
    echo "JARVIS_DRIVE_REMOTE is not set — nothing to upload to." >&2
    exit 2
fi

failed=0
run() {   # run <label> <database> <object name>
    local label="$1" db="$2" name="$3"
    echo
    echo "=============================================================="
    echo "  $label"
    echo "=============================================================="
    if [ ! -f "$db" ]; then
        echo "  no database at $db — skipping" >&2
        failed=$((failed + 1))
        return
    fi
    echo "  backing up : $db"
    echo "  as object  : $name.gz"
    # JARVIS_GOOGLE_DB, which is the name backup-to-drive.sh actually reads. This
    # dispatcher passed JARVIS_DRIVE_DB for its first run — a variable the script has
    # never heard of — so it fell back to its default and uploaded GOOGLE.DB three times
    # under three different names. Every object was 341 MB, the log said COMPLETE, and
    # two of the three databases had no backup at all behind a name that said they did.
    #
    # Unset it explicitly on the fallback path below rather than inheriting whatever the
    # unit happens to have: an inherited value here would silently redirect every backup
    # to one database, which is the same bug wearing a different hat.
    JARVIS_GOOGLE_DB="$db" JARVIS_DRIVE_NAME="$name" JARVIS_DRIVE_ACCOUNT=mnmeyer \
        bash "$BACKUP" || failed=$((failed + 1))
}

# --- the databases -------------------------------------------------------------
# The object names follow the convention <account>-Jarvis<What>DB, which is what makes
# four accounts backing up to four Drives readable at a glance.
run "google index (emails, calendar, Drive metadata, service record)" \
    "$HOME/jarvis/google/google.db" "mnmeyer-JarvisDB"
run "Jarvis's own memory (conversations, facts, plans)" \
    "$HOME/jarvis/conversations.db" "mnmeyer-JarvisConversationsDB"
run "Army service record ledger" \
    "$HOME/jarvis/lifepacket/life_records.db" "mnmeyer-JarvisLifeRecordsDB"

# --- per-account projections ---------------------------------------------------
# Each account's own slice of the index, uploaded to THAT ACCOUNT'S OWN DRIVE.
#
# This is the point of the per-account databases. `google.db` is one file holding four
# life-domains — the owner's medical/military, Michael's care, the rental property,
# Dave's health — and a single consolidated file cannot be handed to any one of those
# accounts without handing it the other three as well.
#
# THE PROJECTIONS ARE REBUILT HERE, NOT BY A TIMER OF THEIR OWN, so a backup can never
# upload a slice older than the run that made it. All four build in about 25 seconds;
# the crawl they are derived from takes hours.
#
# `google.db` STILL UPLOADS ABOVE, WHOLE, to the owner's own Drive, and it remains the
# single search store. Splitting the SEARCH index is what broke recall — bm25's
# statistics are per-index, so a one-account database ranked differently and answered
# "david" with newsletters. These files exist for ownership and for disaster recovery,
# not to be searched.
ACCOUNTS_DIR="$HOME/jarvis/google/accounts"
BUILDER="$HERE/build_account_dbs.py"
if [ -f "$BUILDER" ]; then
    echo
    echo "=============================================================="
    echo "  rebuilding the per-account projections"
    echo "=============================================================="
    if python3 "$BUILDER" | sed 's/^/  /'; then
        echo "  projections rebuilt"
    else
        echo "  PROJECTION BUILD FAILED — the uploads below would be stale" >&2
        failed=$((failed + 1))
    fi
else
    echo "  no builder at $BUILDER — per-account slices are NOT in this backup" >&2
    failed=$((failed + 1))
fi

for db in "$ACCOUNTS_DIR"/*.db; do
    [ -f "$db" ] || continue
    slug="$(basename "$db" .db)"
    echo
    echo "=============================================================="
    echo "  $slug — to its own Drive"
    echo "=============================================================="
    # The remote is per-account HERE: this is the one place that decides which cloud a
    # given domain's index lands in.
    JARVIS_DRIVE_REMOTE="${slug}:JarvisBackups" \
    JARVIS_GOOGLE_DB="$db" \
    JARVIS_DRIVE_NAME="${slug}-JarvisAccountDB" \
    JARVIS_DRIVE_ACCOUNT="$slug" \
        bash "$BACKUP" || failed=$((failed + 1))
done

# --- the scans themselves ------------------------------------------------------
# The service record's TRUE source of record is the filesystem, not either database.
# The index holds the text and the ledger holds the facts, but if the 46 MB of original
# scans are lost, the re-OCR has nothing to read. Copied to Drive as a tree rather than a
# single object: it is documents, and it should look like documents.
if [ -d "$RAW_DOCS" ]; then
    echo
    echo "=============================================================="
    echo "  Army service record — the original scans ($(du -sh "$RAW_DOCS" 2>/dev/null | cut -f1))"
    echo "=============================================================="
    rclone copy "$RAW_DOCS" "$JARVIS_DRIVE_REMOTE/lifepacket/raw_documents" \
        --retries 3 --low-level-retries 10 \
        && echo "  uploaded" || { echo "  FAILED" >&2; failed=$((failed + 1)); }
else
    echo
    echo "  no scan directory at $RAW_DOCS — the original service-record scans are NOT"
    echo "  in this backup. See the note at the top of this script." >&2
    failed=$((failed + 1))
fi

# --- what is actually in Drive now -------------------------------------------------
# The check that would have caught the first run. That run uploaded google.db three times
# under three names, and every line of its log said what it intended to do rather than
# what it did — "backing up conversations.db", "replaced mnmeyer-JarvisConversationsDB.gz"
# — so the only thing that told the truth was the SIZE, and nothing printed it.
#
# Two databases of different sizes do not produce two objects of the same size. Printing
# the sizes every run, and saying so out loud when two of them match, turns "the log says
# COMPLETE" into "here is what I can see", which is a different kind of claim.
# WRITTEN TO A FILE, THEN ECHOED, and the file is the one that counts.
#
# This block printed correctly when the script was run by hand and printed NOTHING on two
# runs under systemd — with the same PATH, the same remote and the same commands, each
# verified working inside a systemd unit in isolation. That was never explained. Rather
# than ship a check whose silence cannot be told apart from its success, the result goes to
# a file first: a listing that is present on disk and absent from the log still tells you
# what is in Drive, whereas a check that simply says nothing tells you nothing.
GUARD_FILE="${JARVIS_BACKUP_GUARD_FILE:-/tmp/jarvis-backup-listing.txt}"
echo
{
    echo "--- the backup folder now contains ---"
    sizes=$(rclone ls "$JARVIS_DRIVE_REMOTE/" --max-depth 1 2>/dev/null | sort -k2 || true)
    if [ -z "$sizes" ]; then
        echo "  (could not list the folder — rclone failed or the remote is unreachable)"
    else
        echo "$sizes" | awk '{printf "  %12d  %s\n", $1, $2}'
        dupe=$(echo "$sizes" | awk '{print $1}' | sort | uniq -d | head -1)
        if [ -n "$dupe" ]; then
            echo
            echo "  WARNING: two or more objects are exactly $dupe bytes."
            echo "  Databases of different sizes do not compress to the same size — this is"
            echo "  what uploading ONE database under several names looks like."
        fi
    fi
} > "$GUARD_FILE" 2>&1
cat "$GUARD_FILE"

# The verdict is read back from the file, so the pass/fail cannot depend on whether the
# log captured the text.
if [ ! -s "$GUARD_FILE" ] || grep -q "could not list" "$GUARD_FILE"; then
    echo "  (the listing could not be produced — see $GUARD_FILE)" >&2
    failed=$((failed + 1))
fi
if grep -q "^  WARNING" "$GUARD_FILE"; then
    failed=$((failed + 1))
fi

echo
if [ "$failed" -eq 0 ]; then
    echo "$(date -Is) ALL BACKUPS COMPLETE"
else
    echo "$(date -Is) $failed step(s) FAILED or suspect — see above" >&2
fi
exit "$failed"
