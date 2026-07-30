"""Google One-Tap sign-in for the desktop app.

Two layers, kept separate so the logic is testable without a browser:
  • Pure helpers: decode a Google ID token (JWT), validate its domain/audience,
    build the FleetX exchange request, and extract the Bearer token from the
    response. These have no network or Tk.
  • capture_google_token(): spins up a localhost page that hosts Google One Tap,
    lets the user pick their @fleetx.io account, and hands the ID token back.

The token exchange itself hits FleetX's own endpoint, reusing FleetX's OAuth
client id — no separate Google Cloud project is required. FleetX sits behind
Cloudflare, so the exchange may be challenged; callers should fall back to the
manual-token paste path on failure.
"""
import base64
import json
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from .config import (API_BASE, CLIENT_ID, GOOGLE_CLIENT_ID, GOOGLE_HOSTED_DOMAIN,
                     GOOGLE_LOGIN_URL)


# ─────────────── pure helpers (no network, no browser) ───────────────

def decode_id_token(jwt):
    """Decode a Google ID token's payload (claims) without verifying the
    signature — verification is delegated to FleetX's endpoint, which does the
    real check. Returns a dict, or {} on malformed input."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)          # pad base64url
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def validate_claims(claims):
    """Client-side sanity checks before we bother the server. Returns
    (ok, reason). The server is still the source of truth; this just gives a
    clear early error for the obvious cases."""
    if not isinstance(claims, dict) or not claims:
        return False, "Could not read the Google sign-in token."
    if claims.get("aud") != GOOGLE_CLIENT_ID:
        return False, "This Google token was not issued for FleetX."
    if not claims.get("email_verified"):
        return False, "Your Google email is not verified."
    if claims.get("hd") != GOOGLE_HOSTED_DOMAIN:
        return False, (f"Only @{GOOGLE_HOSTED_DOMAIN} accounts can sign in "
                       f"(got '{claims.get('email', '?')}').")
    if claims.get("exp", 0) < time.time():
        return False, "The Google sign-in token has expired; try again."
    return True, ""


def google_email(claims):
    return str(claims.get("email", "")).strip().lower()


def build_exchange_request(id_token):
    """(url, files, headers) for POSTing the Google ID token to FleetX.
    Mirrors the browser's multipart form field name `token`."""
    headers = {
        "accept": "application/json, text/plain, */*",
        "clientid": CLIENT_ID,
        "origin": API_BASE,
        "referer": f"{API_BASE}/users/login",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    }
    files = {"token": (None, id_token)}
    return GOOGLE_LOGIN_URL, files, headers


def extract_bearer(resp_json):
    """Pull the FleetX Bearer token out of the exchange response, tolerating
    several shapes (top-level or nested under data/result/payload)."""
    def find(d):
        if not isinstance(d, dict):
            return None
        for k in ("access_token", "token", "value", "accessToken", "authToken", "jwt"):
            if d.get(k):
                return d[k]
        for sub in ("data", "result", "payload"):
            if isinstance(d.get(sub), dict):
                t = find(d[sub])
                if t:
                    return t
        return None
    return find(resp_json)


def exchange_google_token(id_token, session=None):
    """POST the Google ID token to FleetX and return (bearer, error).
    On Cloudflare challenge or any failure, bearer is None and error explains."""
    url, files, headers = build_exchange_request(id_token)
    try:
        r = (session or requests).post(url, files=files, headers=headers, timeout=30)
    except Exception as e:
        return None, f"Network error contacting FleetX: {e}"
    try:
        body = r.json()
    except Exception:
        body = {}
    if r.status_code == 200:
        tok = extract_bearer(body)
        if tok:
            return tok, ""
        return None, "FleetX accepted the sign-in but returned no token."
    # Cloudflare challenges usually show as 403 with an HTML body
    if r.status_code == 403 and "cloudflare" in (r.text or "").lower():
        return None, ("FleetX's security layer (Cloudflare) blocked the "
                      "sign-in. Use the manual token option instead.")
    detail = ""
    if isinstance(body, dict):
        detail = body.get("message") or body.get("error") or ""
    return None, f"FleetX login failed (HTTP {r.status_code}). {detail}".strip()


# ─────────────── local One-Tap capture (browser bridge) ───────────────

_ONE_TAP_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>FleetX Toolkit — Google Sign-in</title>
<script src="https://accounts.google.com/gsi/client" async defer></script>
<style>body{{font-family:Segoe UI,Arial,sans-serif;text-align:center;margin-top:70px}}
#msg{{color:#555;margin-top:24px}}</style></head>
<body>
<h2>Sign in to FleetX Toolkit</h2>
<div id="g_id_onload"
     data-client_id="{client_id}"
     data-hd="{hd}"
     data-callback="onToken"
     data-auto_prompt="true"></div>
<div class="g_id_signin" data-type="standard" data-size="large"
     data-theme="outline" data-text="signin_with" data-shape="rectangular"></div>
<div id="msg">Waiting for Google sign-in…</div>
<script>
function onToken(resp) {{
  document.getElementById('msg').textContent = 'Signing you in… you can close this tab.';
  fetch('/token', {{method:'POST', headers:{{'Content-Type':'text/plain'}},
                    body: resp.credential}})
    .then(()=>{{ document.title='Done'; }})
    .catch(()=>{{ document.getElementById('msg').textContent='Could not reach the app.'; }});
}}
</script>
</body></html>"""


def capture_google_token(port_hint=0, timeout=180):
    """Serve a local One-Tap page, open it in the browser, and return the
    Google ID token the user's sign-in produces — or (None, error).

    NOTE: this requires FleetX's OAuth client to allow http://localhost as an
    authorized JavaScript origin. If it doesn't, the One-Tap widget won't render
    and this times out; callers fall back to manual token paste.
    """
    result = {"token": None, "error": None}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # silence

        def do_GET(self):
            page = _ONE_TAP_PAGE.format(client_id=GOOGLE_CLIENT_ID,
                                        hd=GOOGLE_HOSTED_DOMAIN)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode("utf-8"))

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", "ignore").strip()
            result["token"] = body or None
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            done.set()

    try:
        server = HTTPServer(("127.0.0.1", port_hint), Handler)
    except Exception as e:
        return None, f"Could not start local sign-in server: {e}"

    port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        webbrowser.open(f"http://localhost:{port}/")
    except Exception:
        pass

    ok = done.wait(timeout)
    try:
        server.shutdown()
    except Exception:
        pass
    if not ok:
        return None, ("Timed out waiting for Google sign-in. If the Google "
                      "button never appeared, localhost may not be an "
                      "authorized origin for FleetX — use manual token instead.")
    return result["token"], None
