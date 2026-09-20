#!/bin/bash
# Launch the browser, wherever Playwright decided to put it.
#
# WHY THIS EXISTS. `playwright install chromium` does not install a browser you can run —
# it unpacks one into a versioned cache directory
# (/root/.cache/ms-playwright/chromium-<build>/chrome-linux64/chrome) and drives it
# through the library. So `command -v chromium` finds nothing, and a procedure that says
# "open Chromium in the desktop window" leaves you with no browser to open. This is the
# missing name.
#
# The path is asked of Playwright rather than hardcoded: the build number changes with
# every Playwright release, and a hardcoded path is a launcher that works until it
# silently does not.
set -euo pipefail

CHROME="$(python3 - <<'PY' 2>/dev/null
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    print(p.chromium.executable_path)
PY
)"

if [ -z "$CHROME" ] || [ ! -x "$CHROME" ]; then
    echo "could not locate the Playwright Chromium build" >&2
    exit 2
fi

# --no-sandbox because this runs as root inside a container, where Chromium's sandbox
# cannot initialise. That is acceptable HERE and would not be on a desktop: the container
# is the boundary, it has no interesting host access, and it is thrown away after the
# consent. It would not be acceptable for a browser rendering untrusted pages as a user.
exec "$CHROME" --no-sandbox --no-first-run --disable-dev-shm-usage "$@"
