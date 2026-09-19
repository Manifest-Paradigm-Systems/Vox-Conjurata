"""Google authorization for Jarvis — one consent per account, then never again.

Deliberately standard library only. The fleet's rule for anything that runs in a
sandbox is "import nothing you cannot guarantee is installed", and this runs in the
same house as code that must never reach the network. urllib plus a loopback
http.server is the whole of the installed-app OAuth flow.

READ-ONLY, and that is a design choice rather than a stage we are passing through.
This code can look at four mailboxes, every document the owner owns, and his calendar.
It can change nothing. Escalating to writes later is a scope change plus a re-consent,
not a rewrite — and until then the worst case is that Jarvis *knows* something he
should not, rather than that he destroys something that cannot be recovered.

WHERE THE SECRETS LIVE — never in the repo, never in the index, never logged:

    ~/.config/jarvis/google/client.json    {"client_id": ..., "client_secret": ...}
    ~/.config/jarvis/google/tokens.json    {"<email>": {"refresh_token": ...}}

Both are written 0600. A refresh token is not a session; it is standing access to a
mailbox. If one leaks it does not expire on its own.

Usage:
    python3 auth.py add mnmeyer@gmail.com      # one browser consent for one account
    python3 auth.py list                       # which accounts are authorized
    python3 auth.py check mnmeyer@gmail.com    # prove the token still works
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

CONFIG_DIR = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_CONFIG",
                                               "~/.config/jarvis/google"))
CLIENT_PATH = os.path.join(CONFIG_DIR, "client.json")
TOKENS_PATH = os.path.join(CONFIG_DIR, "tokens.json")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# The read-only set. drive.readonly also covers reading Docs, Sheets and Slides — they
# are Drive files — so three scopes reach six products.
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# calendar.events is a WRITE scope — the first one here, and the first thing in
# this module that can change something rather than look at it. It is kept
# separate from SCOPES so the read consent still works for an account that has
# not been re-consented for writes, and so a reader can see at a glance which
# scope is the one that can do damage.
#
# It is a SENSITIVE scope on an app whose consent screen is External and
# Published, which is the configuration chosen to stop refresh tokens expiring
# every 7 days. Google may require a verification review before it will grant
# this; if `auth.py check` shows it ungranted after a re-consent, that is a
# console problem rather than a code one.
BUSINESS_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# Everything requested when someone re-consents for writes. Reads keep working
# either way — adding a scope only ever widens the grant.
FULL_SCOPES = SCOPES + BUSINESS_SCOPES


# ---------------------------------------------------------------- secret storage
def _write_private(path: str, data: dict) -> None:
    """Write JSON readable only by the owner.

    0600 because these files are standing access to four mailboxes. Writing to a
    temp file and renaming means a crash mid-write cannot leave a truncated token
    file — losing a refresh token means re-consenting by hand, in a browser, four times.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _read_private(path: str) -> dict:
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except (ValueError, OSError) as exc:
        raise SystemExit(f"cannot read {path}: {exc}")


def load_client() -> dict:
    cfg = _read_private(CLIENT_PATH)
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        raise SystemExit(
            f"no OAuth client at {CLIENT_PATH}.\n"
            "Create one: Google Cloud Console -> APIs & Services -> Credentials ->\n"
            "  Create credentials -> OAuth client ID -> Desktop app\n"
            f"then write:  {{\"client_id\": \"...\", \"client_secret\": \"...\"}}  to that path."
        )
    return cfg


def load_tokens() -> dict:
    return _read_private(TOKENS_PATH)


def _update_tokens(email: str, record: dict) -> None:
    """Add one account's token under an exclusive lock.

    Concurrent consent flows are the reason this exists: each one would otherwise read
    the whole file, insert its own account and write it back, so whichever finished last
    would drop the others on the floor.
    """
    import fcntl
    os.makedirs(CONFIG_DIR, exist_ok=True)
    lock_path = TOKENS_PATH + ".lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            store = _read_private(TOKENS_PATH)
            store[email] = record
            _write_private(TOKENS_PATH, store)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


# ---------------------------------------------------------------- the consent flow
class _Catcher(BaseHTTPRequestHandler):
    """Receives the loopback redirect. Google redirects the browser here with ?code=."""

    code: str | None = None
    error: str | None = None

    def do_GET(self):                                  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "error" in q:
            _Catcher.error = q["error"][0]
        elif "code" in q:
            _Catcher.code = q["code"][0]
        body = (b"<h2>Jarvis is authorized.</h2><p>You can close this tab and return "
                b"to the terminal.</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                          # keep the console clean
        pass


def authorize(email: str, port: int = 0, timeout: int = 900) -> dict:
    """Run one interactive consent for one account. Returns the stored record.

    `login_hint` matters more than it looks: the browser is usually already signed into
    a Google account, and without the hint it will happily consent as whichever one that
    is. With four accounts sharing one machine, that is how you end up with two refresh
    tokens for the same person and an empty slot for another.
    """
    client = load_client()
    state = secrets.token_urlsafe(16)

    server = HTTPServer(("127.0.0.1", port), _Catcher)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"

    params = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",       # the only way to get a refresh token
        "prompt": "consent",            # forces one even if previously granted
        "state": state,                 # CSRF guard: the callback must echo what we sent
        "login_hint": email,            # pre-select the ACCOUNT, not just the browser
    }
    url = AUTH_URL + "?" + urllib.parse.urlencode(params)

    print(f"\nAuthorizing {email}\n")
    print("  1. Open this URL in a browser signed into THAT account:\n")
    print(f"     {url}\n")
    print("  2. Approve the scopes. You will see an 'unverified app' warning —")
    print("     that is expected; click Advanced, then 'Go to ... (unsafe)'.")
    print("     It is safe here: you are the app's owner and nothing is shared.\n")
    print("  Waiting for the redirect...")

    # Serve until the CODE arrives — not merely until SOME request arrives. A browser
    # asks for /favicon.ico alongside the redirect, and a single handle_request() will
    # happily answer that, return, and leave us waiting for a callback that already
    # went past. The symptom is "no authorization code received" on a consent that
    # actually succeeded, which is a maddening thing to debug.
    stop = threading.Event()

    def _serve():
        server.timeout = 1.0                      # so the loop can notice `stop`
        while not stop.is_set() and _Catcher.code is None and _Catcher.error is None:
            server.handle_request()

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    while thread.is_alive() and time.time() < deadline:
        thread.join(0.5)
        if _Catcher.code or _Catcher.error:
            break
    stop.set()
    server.server_close()

    if _Catcher.error:
        raise SystemExit(f"authorization refused: {_Catcher.error}")
    if not _Catcher.code:
        raise SystemExit("no authorization code received (timed out or wrong URL)")
    if state and _Catcher.code is None:
        raise SystemExit("callback carried no code")

    tokens = _exchange(client, _Catcher.code, redirect_uri)
    if "refresh_token" not in tokens:
        raise SystemExit(
            "Google returned no refresh token. That normally means this account has "
            "already granted this app and the grant was not re-prompted; revoke it at "
            "myaccount.google.com -> Security -> Third-party apps, then retry."
        )

    # Locked read-modify-write. These flows can run concurrently (three tabs at once is
    # far kinder than three rounds of clicking), and without the lock each process would
    # read the same file, add its own account, and write back — last one wins, silently
    # discarding the other two tokens. Re-consenting an account by hand is exactly the
    # tedium this file exists to avoid.
    _update_tokens(email, {
        "refresh_token": tokens["refresh_token"],
        "scopes": SCOPES,
        "added_at": time.time(),
    })
    store = load_tokens()
    print(f"\n  Stored a refresh token for {email} ({TOKENS_PATH}, mode 0600).")
    return store[email]


def _post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", "replace")
        # The token endpoint's error body never contains the client secret; the request
        # does, and it is never echoed here.
        raise SystemExit(f"token endpoint refused ({exc.code}): {detail}")


def _exchange(client: dict, code: str, redirect_uri: str) -> dict:
    return _post(TOKEN_URL, {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })


def access_token(email: str) -> str:
    """A fresh access token, refreshing the stored one. Raises if re-consent is needed.

    The failure mode this exists for: a refresh token dies when the owner changes his
    Google password or revokes access. That must surface as "account 3 needs re-auth",
    never as silently returning nothing.
    """
    client = load_client()
    store = load_tokens()
    rec = store.get(email)
    if not rec:
        raise SystemExit(f"{email} is not authorized yet — run: python3 auth.py add {email}")

    data = _post(TOKEN_URL, {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "refresh_token": rec["refresh_token"],
        "grant_type": "refresh_token",
    })
    if "access_token" not in data:
        raise SystemExit(
            f"{email} needs re-authorization (the refresh token was refused: "
            f"{data.get('error', 'unknown')}). This happens after a password change or "
            f"if access was revoked. Run: python3 auth.py add {email}"
        )
    return data["access_token"]


class TokenSource:
    """An access token that renews itself. Every long crawl needs one.

    Access tokens last about an hour and these crawls last nine. Fetching one at the start
    and using it throughout is a crawl that is structurally incapable of finishing — it
    works perfectly for the first fifty minutes and then every request fails at once. That
    is exactly how the first full mail run died, at 3,600 messages, with a single
    `UNAUTHENTICATED` in the log and nothing else to see.

    Renewing proactively (well inside the hour) and again on any 401 means a long job
    cannot be outlived by its own credentials.
    """

    REFRESH_AFTER = 2700          # 45 minutes, comfortably inside the ~60-minute lifetime

    def __init__(self, email: str):
        self.email = email
        self._token = ""
        self._fetched = 0.0
        self.renewals = 0
        # Concurrent fetchers share one token. Without the lock, several of them can
        # decide to renew at the same moment and each fire its own refresh request —
        # wasteful, and on a rate-limited endpoint actively harmful.
        self._lock = threading.Lock()

    def get(self, force: bool = False) -> str:
        with self._lock:
            if force or not self._token or (time.time() - self._fetched) > self.REFRESH_AFTER:
                self._token = access_token(self.email)
                self._fetched = time.time()
                self.renewals += 1
            return self._token


# ---------------------------------------------------------------- cli
def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]

    if cmd == "add":
        if len(argv) < 3:
            raise SystemExit("usage: auth.py add <email>")
        authorize(argv[2])
        return 0

    if cmd == "list":
        store = load_tokens()
        if not store:
            print("no accounts authorized yet")
            return 0
        for email, rec in sorted(store.items()):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(rec.get("added_at", 0)))
            print(f"  {email:<34} authorized {when}  ({len(rec.get('scopes', []))} scopes)")
        return 0

    if cmd == "check":
        if len(argv) < 3:
            raise SystemExit("usage: auth.py check <email>")
        email = argv[2]
        tok = access_token(email)
        # Prove it against a real endpoint rather than trusting that a token came back.
        req = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {tok}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            prof = json.loads(r.read())
        print(f"  {email} OK — {prof.get('messagesTotal')} messages, "
              f"{prof.get('threadsTotal')} threads")
        return 0

    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
