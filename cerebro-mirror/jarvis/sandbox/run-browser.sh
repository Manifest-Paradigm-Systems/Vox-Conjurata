#!/bin/bash
# Start the Jarvis browser desktop and print how to reach it.
#
# BOUND TO LOOPBACK, NOT TO THE LAN. Every other service in this fleet binds 0.0.0.0 and
# trusts the network — but this one is a browser somebody will sign into Google with, and
# a browser session is a credential in a way a mail index is not. Reach it through an ssh
# tunnel. Set JARVIS_BROWSER_BIND=0.0.0.0 to publish it, and set VNC_PASSWORD if you do.
set -euo pipefail

NAME="${JARVIS_BROWSER_NAME:-jarvis-browser}"
PORT="${NOVNC_PORT:-6080}"
BIND="${JARVIS_BROWSER_BIND:-127.0.0.1}"

if ! podman image exists localhost/jarvis-browser; then
    echo "no image. Build it first:" >&2
    echo "  cd ~/jarvis && podman build -t localhost/jarvis-browser \\" >&2
    echo "      -f sandbox/Containerfile.browser ." >&2
    exit 2
fi

# A previous run that was killed leaves the name held; --rm cleans the container but not
# a name that a crashed process still owns.
podman rm -f "$NAME" >/dev/null 2>&1 || true

cat <<EOF

  Starting the browser desktop as $NAME
    reachable at : ${BIND}:${PORT} (noVNC)
    tunnel from your machine:
        ssh -N -L ${PORT}:127.0.0.1:${PORT} cerebro
    then open   : http://127.0.0.1:${PORT}/vnc.html

  Ctrl-C stops it.

EOF

exec podman run --rm --name "$NAME" \
    --security-opt label=disable \
    -p "${BIND}:${PORT}:${PORT}" \
    -e VNC_PASSWORD="${VNC_PASSWORD:-}" \
    -e NOVNC_PORT="$PORT" \
    localhost/jarvis-browser desktop
