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
    # -localhost IS THE POINT, NOT A DETAIL. Without it x11vnc binds 0.0.0.0:5900 — a
    # raw, unauthenticated VNC server on the LAN, sitting behind the loopback-bound noVNC
    # that was supposed to be the only way in. The websockify binding looked right and the
    # port beside it was wide open; `ss -ltnp` was what showed it. With -localhost,
    # x11vnc accepts connections only from this host, so the ssh tunnel is genuinely the
    # only route in.
    if [ -n "${VNC_PASSWORD:-}" ]; then
        x11vnc -storepasswd "$VNC_PASSWORD" "$VNC_PASSWORD_FILE" >/dev/null 2>&1
        x11vnc -display "$DISPLAY" -localhost -forever -shared \
               -rfbauth "$VNC_PASSWORD_FILE" -rfbport 5900 >/dev/null 2>&1 &
        echo "  VNC password: set from \$VNC_PASSWORD"
    else
        # NO PASSWORD, which is only defensible because of -localhost. A shared secret
        # printed into a log is worse than no secret on a socket that nothing off-host can
        # reach. Publish beyond the host (NOVNC_BIND / JARVIS_BROWSER_BIND) and you must
        # set VNC_PASSWORD.
        x11vnc -display "$DISPLAY" -localhost -forever -shared -nopw \
               -rfbport 5900 >/dev/null 2>&1 &
        echo "  VNC password: NONE — loopback only (-localhost)"
    fi
    sleep 1
    # BOUND TO LOOPBACK UNLESS TOLD OTHERWISE. The container runs with host networking
    # (see run-browser.sh), so 127.0.0.1 here is cerebro's loopback — reachable through an
    # ssh tunnel and nowhere else. Publishing it needs NOVNC_BIND=0.0.0.0 and, with it,
    # VNC_PASSWORD, because this is a browser somebody signs into Google with.
    websockify --web=/usr/share/novnc "${NOVNC_BIND:-127.0.0.1}:${NOVNC_PORT}" \
        localhost:5900 >/dev/null 2>&1 &

    cat <<EOF

  Desktop is up.
    inside the container : $DISPLAY, ${SCREEN}
    noVNC                : http://127.0.0.1:${NOVNC_PORT}/vnc.html

  From your own machine, tunnel first and then open that URL:

    ssh -N -L ${NOVNC_PORT}:127.0.0.1:${NOVNC_PORT} cerebro

  For a Google consent, run this in ANOTHER ssh session and open the URL it prints
  inside the noVNC window (see google/CONSENT.md for the whole procedure):

    cd ~/jarvis/google && python3 auth.py add <email>

  No port argument is needed. auth.py picks a free port and prints it in the URL, and
  because this browser shares cerebro's network namespace, the redirect to
  127.0.0.1:<port> lands on the listener that is waiting for it.
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
