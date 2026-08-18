"""Standalone auth for the SMS Command and Messaging tabs.

These two tabs do NOT use the FleetX Bearer token. They gate on their own
email+password, managed by an admin, stored in a dedicated public Gist:

  {
    "salt": "<random hex>",
    "users": {
      "email@fleetx.io": {"pw": "<sha256(salt+password)>", "admin": true/false}
    }
  }

The Gist holds ONLY authentication (users + salt) — no secrets. The SemySMS
token is NOT in the Gist: each user enters it once on their own machine after
login and it is stored locally in Windows Credential Manager (DPAPI-encrypted,
per Windows user). So finding the Gist URL or extracting the exe reveals no
token. Passwords are salted SHA-256 hashes — never plaintext.

Writes (admin user management) require a GitHub token with gist scope, entered
by an admin and kept in Credential Manager.
"""
import hashlib
import json

import requests

# Dedicated user/token Gist for the SMS+Messaging feature (public, obscure URL).
# Raw URL is read with no credential; the API URL is used for admin writes.
SMS_GIST_ID       = "b91e57250dbc3f53faa813465b95f292"
SMS_GIST_FILENAME = "sms_users.json"
SMS_GIST_RAW      = f"https://gist.githubusercontent.com/saketverma-dotcom/{SMS_GIST_ID}/raw/{SMS_GIST_FILENAME}"
SMS_GIST_API      = f"https://api.github.com/gists/{SMS_GIST_ID}"


# ─────────────── pure helpers (no network) ───────────────

def hash_password(password, salt):
    """Salted SHA-256 of a password. Deterministic; used for both set + check."""
    return hashlib.sha256((str(salt) + str(password)).encode("utf-8")).hexdigest()


def verify_password(password, salt, stored_hash):
    return hash_password(password, salt) == str(stored_hash)


def normalize_email(email):
    return str(email or "").strip().lower()


def check_login(store, email, password):
    """(ok, is_admin, reason) against a loaded store dict."""
    if not isinstance(store, dict):
        return False, False, "User store unavailable."
    email = normalize_email(email)
    users = store.get("users") or {}
    rec = users.get(email)
    if not rec:
        return False, False, "Unknown user. Ask the admin to add you."
    salt = store.get("salt", "")
    if verify_password(password, salt, rec.get("pw", "")):
        return True, bool(rec.get("admin")), ""
    return False, False, "Incorrect password."


def apply_user_change(store, action, email, password=None, admin=None):
    """Return a NEW store dict with a user added/updated/removed. Pure — the
    caller pushes the result to the Gist. `store` is the current loaded dict."""
    store = json.loads(json.dumps(store or {}))       # deep copy
    store.setdefault("users", {})
    salt = store.setdefault("salt", "")
    email = normalize_email(email)
    if action == "delete":
        store["users"].pop(email, None)
        return store
    rec = store["users"].get(email, {"pw": "", "admin": False})
    if password:
        rec["pw"] = hash_password(password, salt)
    if admin is not None:
        rec["admin"] = bool(admin)
    store["users"][email] = rec
    return store


# ─────────────── Gist I/O ───────────────

def load_store():
    """Read the public user/token Gist. Returns dict, or None on failure."""
    try:
        r = requests.get(SMS_GIST_RAW, timeout=20,
                         headers={"User-Agent": "FleetXSMS"})
        if r.status_code == 200:
            return json.loads(r.text)
    except Exception:
        pass
    return None


def push_store(store, gh_token):
    """Write the store back to the Gist (admin only). Returns (ok, message)."""
    try:
        payload = {"files": {SMS_GIST_FILENAME: {
            "content": json.dumps(store, indent=2)}}}
        r = requests.patch(
            SMS_GIST_API, json=payload, timeout=20,
            headers={"Authorization": f"Bearer {gh_token}",
                     "Accept": "application/vnd.github+json",
                     "User-Agent": "FleetXSMS",
                     "X-GitHub-Api-Version": "2022-11-28"})
        if r.status_code in (200, 201):
            return True, "Saved."
        return False, f"GitHub HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, f"Error: {e}"
