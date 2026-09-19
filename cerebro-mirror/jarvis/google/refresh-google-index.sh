#!/bin/bash
# Refresh the Google index for one source. Installed but NOT enabled — see the
# timers in ../systemd/. Nothing here runs until the accounts are all indexed.
#
# Usage: refresh-google-index.sh {gmail|gmail-full|calendar|drive|phone}
#
# WHY EACH SOURCE HAS ITS OWN TIMER RATHER THAN ONE SCHEDULE:
# a Gmail refresh is not a poll. gmail_index.py has no historyId incremental
# mode — it walks newest-first with a resume cursor and never stops early on
# messages it already holds, so a full walk is measured in hours ("100k messages
# takes a couple of hours", per its own comment). Calendar and Drive are full
# rewrites of a few hundred rows and cost seconds. One cadence cannot serve both,
# so each source gets the interval that fits it.
set -euo pipefail

MODE="${1:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Accounts, and where each one's database lives. Kept OUTSIDE the repo: it names
# the owner's accounts, and the repo pushes to a remote. Defaults to the current
# single-database state so this works before the per-account split happens.
CONF="${JARVIS_GOOGLE_ACCOUNTS:-$HOME/.config/jarvis/google/accounts.json}"

if [ ! -f "$CONF" ]; then
    # Pre-split fallback: one database, one account. Replace with the JSON file
    # above once the index is per-account.
    ACCOUNTS=( "${JARVIS_GOOGLE_ACCOUNT:-mnmeyer@gmail.com}" )
else
    mapfile -t ACCOUNTS < <(python3 -c "
import json,sys
for a in json.load(open('$CONF')): print(a['email'])")
fi

run_source() {   # run_source <account> <db>
    local acct="$1" db="$2"
    case "$MODE" in
        gmail)
            # Bounded recent run: sets the everyday refresh apart from the full
            # walk. Re-fetches the newest N (there is no early exit on known ids),
            # which is bounded and cheap; everything deeper waits for gmail-full.
            echo "[$(date -Is)] $acct: gmail recent (${JARVIS_GMAIL_RECENT:-1000})"
            ( cd "$HERE" && JARVIS_GOOGLE_DB="$db" python3 gmail_index.py run "$acct" "${JARVIS_GMAIL_RECENT:-1000}" ) ;;
        gmail-full)
            # No max_messages: the complete walk. This is what sees a message move
            # to TRASH, or a label change, anywhere in the mailbox.
            echo "[$(date -Is)] $acct: gmail FULL walk"
            ( cd "$HERE" && JARVIS_GOOGLE_DB="$db" python3 gmail_index.py run "$acct" ) ;;
        calendar)
            # `run` is required: without it the account name lands in argv[1],
            # which is the COMMAND slot, and the indexer exits with
            # "unknown command 'someone@gmail.com'". It failed that way hourly
            # for days — an enabled, scheduled unit that never once succeeded.
            echo "[$(date -Is)] $acct: calendar"
            ( cd "$HERE" && JARVIS_GOOGLE_DB="$db" python3 calendar_index.py run "$acct" ) ;;
        drive)
            echo "[$(date -Is)] $acct: drive"
            ( cd "$HERE" && JARVIS_GOOGLE_DB="$db" python3 drive_index.py run "$acct" ) ;;
        phone)
            # Reads the handset over adb, so it cannot be on a wall clock — the
            # phone has to be attached. Wired to a timer only because a failed run
            # is harmless; the real trigger should be the device connecting.
            echo "[$(date -Is)] $acct: phone (requires the handset)"
            ( cd "$HERE" && JARVIS_GOOGLE_DB="$db" python3 phone_index.py ) ;;
        *)
            echo "usage: $(basename "$0") {gmail|gmail-full|calendar|drive|phone}" >&2
            exit 2 ;;
    esac
}

for acct in "${ACCOUNTS[@]}"; do
    db="${JARVIS_GOOGLE_DB:-$HOME/jarvis/google/google.db}"
    # A paused crawl must stay paused: control.is_paused() is re-checked every
    # page by the crawlers themselves, so there is nothing to do here but honour
    # it. Noted so nobody "fixes" the apparent silence later.
    run_source "$acct" "$db"
done
echo "[$(date -Is)] $MODE done"
