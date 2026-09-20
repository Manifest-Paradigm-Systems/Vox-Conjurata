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
    JARVIS_DRIVE_DB="$db" JARVIS_DRIVE_NAME="$name" JARVIS_DRIVE_ACCOUNT=mnmeyer \
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

echo
if [ "$failed" -eq 0 ]; then
    echo "$(date -Is) ALL BACKUPS COMPLETE"
else
    echo "$(date -Is) $failed of 4 steps FAILED — see above" >&2
fi
exit "$failed"
