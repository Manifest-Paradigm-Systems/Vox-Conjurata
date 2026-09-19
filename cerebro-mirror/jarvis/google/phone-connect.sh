#!/bin/bash
# Make sure adb can see the phone, reconnecting if needed. Run this BEFORE
# phone_index.py; it is the only piece of the integration that needs a live
# connection rather than just a file.
#
# Usage: phone-connect.sh [--check]
#   (no args)  connect if needed, exit 0 when a device is attached
#   --check    report status, change nothing
#
# EXIT CODES — distinct on purpose. Every failure here has historically looked
# like success: phone_index exits 0 with an empty result when adb cannot reach
# the phone, and "no messages" is indistinguishable from a broken connection.
#   0  exactly one device attached
#   10 the phone is unreachable (not on the LAN, mDNS down, tailnet down)
#   11 more than one device attached and we could not reduce it to one
#   12 a device is attached but not in the `device` state (offline/unauthorized)
set -euo pipefail

ADB="${JARVIS_ADB:-adb}"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

STATE_DIR="${JARVIS_ADB_STATE_DIR:-$HOME/.local/state/jarvis-google}"
CACHE="$STATE_DIR/phone-address"          # last address that actually worked
mkdir -p "$STATE_DIR"

# The tailnet address is stable where the LAN one is DHCP. mDNS does NOT cross
# Tailscale, so this is the fallback when the phone is away from home — but the
# wireless-debugging PORT is still dynamic, which is why we cache the last one.
TAILNET="${JARVIS_PHONE_TAILNET:-100.99.183.108}"
SERIAL_HINT="${JARVIS_PHONE_SERIAL:-5C111JEA318353}"

attached() { "$ADB" devices 2>/dev/null | awk 'NR>1 && $2=="device" {print $1}'; }
count()    { attached | wc -l; }

# Reduce to exactly one device. Two entries for ONE phone makes every adb command
# fail with "more than one device/emulator" — and phone_index.py passes no -s, so
# it has no way to disambiguate. That error is loud on stderr, but anything that
# swallows stderr turns it into an empty result. Prefer the mDNS-registered
# entry (it reconnects by itself) and drop explicit ip:port ones.
dedupe() {
    local keep=""
    while read -r dev; do
        [ -z "$dev" ] && continue
        case "$dev" in
            *"_adb-tls-connect._tcp") keep="$dev" ;;
            *)                        "$ADB" disconnect "$dev" >/dev/null 2>&1 || true ;;
        esac
    done < <(attached)
    [ -n "$keep" ] && return 0
    # No mDNS entry: keep the first and drop the rest.
    local first=""
    while read -r dev; do
        [ -z "$dev" ] && continue
        if [ -z "$first" ]; then first="$dev"
        else "$ADB" disconnect "$dev" >/dev/null 2>&1 || true; fi
    done < <(attached)
    return 0
}

# Try one address and confirm it lands as exactly one usable device.
try() {
    local addr="$1" label="$2"
    [ -z "$addr" ] && return 1
    echo "  trying $label ($addr)"
    "$ADB" connect "$addr" >/dev/null 2>&1 || return 1
    sleep 2
    local n; n=$(count)
    if [ "$n" -eq 1 ]; then
        printf '%s' "$addr" > "$CACHE"
        echo "  connected via $label: $(attached)"
        return 0
    fi
    # It half-worked (or duplicated) — undo it so the next attempt starts clean.
    "$ADB" disconnect "$addr" >/dev/null 2>&1 || true
    return 1
}

# ---------------------------------------------------------------- status
n=$(count)
if [ "$n" -eq 1 ]; then
    dev=$(attached)
    echo "ok: $dev"
    exit 0
fi
if [ "$n" -gt 1 ]; then
    echo "warn: $n devices attached for one phone — reducing" >&2
    if [ "$CHECK" -eq 1 ]; then "$ADB" devices | sed 's/^/  /' >&2; exit 11; fi
    dedupe
    n=$(count)
    [ "$n" -gt 1 ] && { echo "error: still $n devices after dedupe" >&2; exit 11; }
    [ "$n" -eq 1 ] && { echo "ok: $(attached)"; exit 0; }
fi

if [ "$CHECK" -eq 1 ]; then
    echo "not connected (0 devices)" >&2
    exit 10
fi

# ---------------------------------------------------------------- reconnect
echo "no device attached — reconnecting"

# 1. mDNS. Free when the phone is on the LAN, and the usual path at home.
while read -r line; do
    addr=$(echo "$line" | grep -oE '[0-9]+(\.[0-9]+){3}:[0-9]+' | head -1)
    try "$addr" "mDNS" && exit 0
done < <(timeout 15 "$ADB" mdns services 2>/dev/null | grep "_adb-tls-connect._tcp")

# 2. The cached address. Survives the phone leaving the LAN, until the
#    wireless-debugging port changes.
try "$(cat "$CACHE" 2>/dev/null || true)" "cached" && exit 0

# 3. Tailnet with the last known port. Same port caveat, different transport —
#    worth one attempt because the LAN and the tailnet fail independently.
port=$(cut -d: -f2 < "$CACHE" 2>/dev/null || true)
try "${TAILNET}:${port:-5555}" "tailnet" && exit 0

# Nothing worked. Be explicit about what this does NOT mean.
echo "error: cannot reach the phone (serial ${SERIAL_HINT})." >&2
echo "  Not on the LAN (mDNS found nothing) and not reachable on the tailnet." >&2
echo "  If the phone rebooted or wireless debugging was toggled, its port changed:" >&2
echo "  read it from Settings > Developer options > Wireless debugging, then" >&2
echo "  re-run. Nothing was indexed — this is NOT 'no messages'." >&2
exit 10
