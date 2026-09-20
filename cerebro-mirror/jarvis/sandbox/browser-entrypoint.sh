#!/bin/bash
# Start a desktop somebody can reach, or just hand the container to Playwright.
#
# TWO MODES, because there are two jobs:
#
#   desktop  (default) — Xvfb + fluxbox + x11vnc + noVNC, so a browser window on this
#                        host can be seen and clicked from another machine. This is what
#                        the Google consent flow needs, because that flow requires a
#                        password and a second factor from a human.
#   shell              — no desktop; just a shell in the container, for Playwright scripts.
#
# A bare `chromium` is also accepted and runs headless, which is what a scraper wants.
set -euo pipefail

MODE="${1:-desktop}"
SCREEN="${SCREEN:-1600x1000x24}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
VNC_PASSWORD_FILE="/tmp/vncpass"

start_desktop() {
    Xvfb "$DISPLAY" -screen 0 "$SCREEN" -nolisten tcp &
    sleep 1
    fluxbox >/dev/null 2>&1 &
    # x11vnc needs SOMETHING before it will serve, and it must have a framebuffer to
    # attach to — hence the sleep above rather than racing it.
    if [ -n "${VNC_PASSWORD:-}" ]; then
        x11vnc -storepasswd "$VNC_PASSWORD" "$VNC_PASSWORD_FILE" >/dev/null 2>&1
        x11vnc -display "$DISPLAY" -forever -shared -rfbauth "$VNC_PASSWORD_FILE" \
               -rfbport 5900 >/dev/null 2>&1 &
        echo "  VNC password: set from \$VNC_PASSWORD"
    else
        # NO PASSWORD BY DEFAULT, AND THAT IS DELIBERATE: this is meant to be reached
        # over an ssh tunnel, and a shared secret that gets printed into a log is worse
        # than no secret on a loopback socket. Publish the port beyond the host and you
        # must set VNC_PASSWORD.
        x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 >/dev/null 2>&1 &
        echo "  VNC password: NONE — reachable only as far as you publish it"
    fi
    sleep 1
    websockify --web=/usr/share/novnc "$NOVNC_PORT" localhost:5900 >/dev/null 2>&1 &

    cat <<EOF

  Desktop is up.
    inside the container : $DISPLAY, ${SCREEN}
    noVNC                : http://127.0.0.1:${NOVNC_PORT}/vnc.html

  From your own machine, tunnel first and then open that URL:

    ssh -N -L ${NOVNC_PORT}:127.0.0.1:${NOVNC_PORT} cerebro

  Then, in the noVNC window, run the flow you need. For a Google consent:

    python3 ~/jarvis/google/auth.py add <email> --port 8765

  and open the URL it prints INSIDE that window — the redirect comes back to cerebro's
  loopback, which is where the catcher is listening.
EOF

    # Keep the container alive for as long as the desktop is.
    wait
}

case "$MODE" in
    desktop) start_desktop ;;
    shell)   exec /bin/bash ;;
    *)
        # Anything else is a command to run in the image — `python3 script.py`,
        # `pdftotext ...`, `qpdf ...`. Playwright needs no display in this mode, so a
        # scraper runs without ever starting Xvfb.
        exec "$@"
        ;;
esac
