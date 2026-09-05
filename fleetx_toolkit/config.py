"""Constants, file paths, and the settings store. No behavior code."""
import json
import os
import sys

API_BASE    = "https://api.fleetx.io"
APP_BASE    = "https://app.fleetx.io"
LOGIN_URL   = f"{API_BASE}/api/v1/login"
# Google One-Tap SSO (v3.4): reuse FleetX's own OAuth client + exchange endpoint
GOOGLE_LOGIN_URL   = f"{API_BASE}/api/v2/login/google-one-tap"
GOOGLE_CLIENT_ID   = "135264764063-hcs8ri1mm7p7bq0p69krvlntlpkcq4re.apps.googleusercontent.com"
GOOGLE_HOSTED_DOMAIN = "fleetx.io"
CLIENT_ID   = "fleetxweb"
DELAY_MS    = 1250
TOKEN_PARAM = "udbhav"
MOBILE_PARAM = "5754236272120"

APP_VERSION = "3.12"

CRED_FILE     = os.path.join(os.path.expanduser("~"), ".fleetx_toolkit_creds.json")
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".fleetx_toolkit_settings.json")
CMD_FILE      = os.path.join(os.path.expanduser("~"), ".fleetx_toolkit_commands.json")
GHTOKEN_FILE  = os.path.join(os.path.expanduser("~"), ".fleetx_toolkit_ghtoken.json")

# ─────────────── Toolkit access control ───────────────
ADMIN_EMAILS   = {"saket.verma@fleetx.io"}          # full access + manage access
ALLOWED_DOMAIN = "@fleetx.io"                        # only fleetx emails allowed

CONTROLLABLE_TABS = [
    "Device Add", "SIM Inventory", "SIM Update", "Vehicle-Device Map",
    "Send Command", "Sequential 2-Phase", "SensorType", "Assets",
    "Tickets", "SMS Command", "Messaging",
]

# Access file / logs sit NEXT TO the exe (or the entry script in dev mode).
# NOTE (fix during split): under PyInstaller onefile, __file__ points into the
# temp extraction dir (_MEIPASS), so the old logic silently wrote the access
# cache and logs into a folder that is deleted on exit. sys.executable is the
# real exe location when frozen.
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    try:
        _BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) or os.getcwd()
    except Exception:
        _BASE_DIR = os.getcwd()
ACCESS_FILE = os.path.join(_BASE_DIR, "fleetx_toolkit_access.json")   # local cache / fallback
LOGS_DIR = os.path.join(_BASE_DIR, "logs")

# Remote source of truth (edit rules in this Gist; every app fetches live on login)
ACCESS_URL = "https://gist.githubusercontent.com/saketverma-dotcom/e4d2c83ab2c25f30638c049aa2ec2a29/raw/fleetx_access.json"
GIST_ID       = "e4d2c83ab2c25f30638c049aa2ec2a29"
GIST_FILENAME = "fleetx_access.json"
GIST_API      = f"https://api.github.com/gists/{GIST_ID}"

SENSOR_PRESETS = [
    "REFER_AC_AIN1_TEMP_1_WIRE",
    "RFID_AVL_78",
    "TATA_EXPRESS_T_EV_WITH_ZIPTRON_BATTERY_TFT100_SOS_AIN2",
]
SIM_PROVIDERS  = ["ONOMONDO", "TATA", "AERIS", "AIRTEL", "VODAFONE", "BSNL", "JIO"]

# ─────────────── SemySMS (SMS Command tab) ───────────────
SEMYSMS_API   = "https://semysms.net/api/3/sms.php"
SEMYSMS_INBOX_API = "https://semysms.net/api/3/inbox_sms.php"   # incoming SMS list
SEMYSMS_OUTBOX_API = "https://semysms.net/api/3/outbox_sms.php" # outgoing + status
MESSAGING_POLL_SECONDS = 7
# device id -> friendly name (dropdown shows the names, API gets the id)
SEMYSMS_SIMS  = {
    "355387": "Airtel 1",
    "355386": "Airtel 2",
    "350374": "Airtel Pulse",
    "352969": "Voda Pulse",
    "338826": "Voda Restrict 1",
    "338825": "Voda Restrict 2",
}
SEMYSMS_SIM_NAMES = list(SEMYSMS_SIMS.values())
_SIM_NAME_TO_ID   = {v: k for k, v in SEMYSMS_SIMS.items()}
# Messaging can now use all 6 SIMs (previously limited to the two Pulse SIMs).
MESSAGING_SIMS = dict(SEMYSMS_SIMS)
MESSAGING_SIM_NAMES = list(MESSAGING_SIMS.values())

def sim_id_for_name(name):
    """Friendly SIM name -> device id, or '' if unknown."""
    return _SIM_NAME_TO_ID.get((name or "").strip(), "")

# Ticket assignee directory  (display name -> FleetX assignee id)
ASSIGNEE_DIRECTORY = {
    "Komal Bisht":        3767073,
    "Kuldeep Kashyap":    2795599,
    "Niranjan Saini":     2836388,
    "Saket Verma":        156286,
    "Sakir Khan":         301895,
    "Vinay Tyagi":        272420,
    "Himanshu Gupta":     952546,
    "Divya Rai":          3934290,
}


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(d):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass
