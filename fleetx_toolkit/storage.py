"""Secret + command persistence. Secrets live in the OS credential store
(Windows Credential Manager via keyring, DPAPI-encrypted). No plaintext fallback."""
import base64
import json
import os

from .config import (CRED_FILE, CMD_FILE, GHTOKEN_FILE,
                     load_settings, save_settings)

try:
    import keyring
    _KEYRING_OK = True
except Exception:
    keyring = None
    _KEYRING_OK = False
KEYRING_SERVICE    = "FleetX-Toolkit"
KEYRING_GH_SERVICE = "FleetX-Toolkit-GitHub"

# Seed command library (from Bruno collection)
# NOTE: All /trigger/sendcommands calls are form-urlencoded (DYNAMIC_COMMAND_SETTING_TRIGGER)
DEFAULT_COMMANDS = [
    {"name": "SPEED_ON_FX10_GPRS",                    "id": "69b3c416e7270303577102b8", "deviceType": "FX10", "style": "form"},
    {"name": "SPEED_OFF_FX10_GPRS",                   "id": "69b3c452c094822097e77cf9", "deviceType": "FX10", "style": "form"},
    {"name": "FOTA_BULK_HW_1.1_to_6.5",               "id": "69faa7f1b59269a4efe3cd82", "deviceType": "FX10", "style": "form"},
    {"name": "FOTA_BULK_HW_1.2_to_6.5",               "id": "69faa80b4c3e4cb7b7f63546", "deviceType": "FX10", "style": "form"},
    {"name": "FOTA_Bulk_2.18_from_2.15",              "id": "69602b8c1f371ea845eb01b7", "deviceType": "FX10", "style": "form"},
    {"name": "FX11A_TIMER_10_SEC_ON_10_SEC_OFF_GPRS", "id": "6a4754d02e7869f27092cc8f", "deviceType": "FX10", "style": "form"},
]
_KNOWN_FORM_IDS = {c["id"] for c in DEFAULT_COMMANDS}


def load_gh_token():
    """Read GH token from Windows Credential Manager; migrate the legacy
       plaintext JSON file into it on first sight, then delete the file."""
    if _KEYRING_OK:
        try:
            t = keyring.get_password(KEYRING_GH_SERVICE, "gist-admin")
            if t:
                return t
        except Exception:
            pass
    try:                                   # legacy plaintext file
        with open(GHTOKEN_FILE) as f:
            t = json.load(f).get("token", "")
        if t:
            save_gh_token(t)               # migrates + deletes if keyring works
        return t
    except Exception:
        return ""

def save_gh_token(token):
    """Store in Credential Manager only. NO plaintext fallback — if keyring is
       unavailable the token simply isn't persisted. Returns True if stored."""
    if not _KEYRING_OK:
        return False
    try:
        keyring.set_password(KEYRING_GH_SERVICE, "gist-admin", token)
        try:
            os.remove(GHTOKEN_FILE)        # kill the legacy plaintext copy
        except OSError:
            pass
        return True
    except Exception:
        return False

def save_credentials(email, password):
    """Password -> Windows Credential Manager (DPAPI). Email (not secret) ->
       settings file. NO plaintext/base64 fallback: if keyring is unavailable,
       Remember-Me simply doesn't persist."""
    if not _KEYRING_OK:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, email, password)
        s = load_settings()
        s["saved_email"] = email
        save_settings(s)
        try:
            os.remove(CRED_FILE)           # kill the legacy base64 copy
        except OSError:
            pass
        return True
    except Exception:
        return False

def load_credentials():
    # Legacy base64 file present? Migrate it into the Credential Manager once.
    legacy_email = legacy_pass = ""
    try:
        with open(CRED_FILE) as f:
            d = json.load(f)
        legacy_email = d.get("email", "")
        legacy_pass = base64.b64decode(d.get("password", "")).decode()
    except Exception:
        pass
    if legacy_email and legacy_pass:
        save_credentials(legacy_email, legacy_pass)   # migrate + delete file
        return legacy_email, legacy_pass
    if _KEYRING_OK:
        try:
            email = load_settings().get("saved_email", "")
            if email:
                pw = keyring.get_password(KEYRING_SERVICE, email)
                if pw:
                    return email, pw
        except Exception:
            pass
    return "", ""

def clear_credentials():
    s = load_settings()
    email = s.pop("saved_email", "")
    save_settings(s)
    if email and _KEYRING_OK:
        try:
            keyring.delete_password(KEYRING_SERVICE, email)
        except Exception:
            pass
    try:
        os.remove(CRED_FILE)
    except OSError:
        pass

def load_commands():
    try:
        with open(CMD_FILE) as f:
            cmds = json.load(f)
        if isinstance(cmds, list) and cmds:
            # Migration: known sendcommands IDs must be form style
            changed = False
            for c in cmds:
                if c.get("id") in _KNOWN_FORM_IDS and c.get("style") != "form":
                    c["style"] = "form"
                    changed = True
            if changed:
                save_commands(cmds)
            return cmds
    except Exception:
        pass
    return DEFAULT_COMMANDS[:]

def save_commands(cmds):
    try:
        with open(CMD_FILE, "w") as f:
            json.dump(cmds, f, indent=2)
    except Exception:
        pass
