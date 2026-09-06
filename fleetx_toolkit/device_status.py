"""SIM device status (v3.13) — battery % and online state for the dropdowns.

Speed matters here: the SMS Command and Messaging tabs must never block on this.
So the design is:
  • the network fetch always runs on a worker thread,
  • results are cached for DEVICE_STATUS_TTL seconds,
  • the UI reads only the cache and renders instantly, showing plain names until
    the first result lands.

Nothing in this module touches Tk, and the pure parts take no network.
"""
import threading
import time

from .config import DEVICE_STATUS_TTL, SEMYSMS_DEVICES_API
from .http import session


# ─────────────── pure parsing / formatting ───────────────

def build_devices_request(token):
    return SEMYSMS_DEVICES_API, {"token": token}


def parse_devices(resp_json):
    """{device_id(str): {"online": bool, "battery": int|None, "name": str,
                         "for_sending": bool|None, "last_active": str}}

    Field names per the SemySMS docs (api/3/devices.php):
      • ``bat``          battery charge in percent (may arrive as a string)
      • ``power``        the service in the phone is on or off  -> online
      • ``is_work``      "use for sending" (a setting, NOT online state)
      • ``device_name``  device name
    Reading ``power`` as the battery was what made every SIM show "1%".
    """
    out = {}
    if not isinstance(resp_json, dict):
        return out
    for row in resp_json.get("data") or []:
        if not isinstance(row, dict):
            continue
        did = str(row.get("id", "")).strip()
        if not did:
            continue

        def flag(key):
            if key not in row or row.get(key) in (None, ""):
                return None
            try:
                return int(row.get(key)) == 1
            except (TypeError, ValueError):
                return bool(row.get(key))

        battery = None
        raw_bat = row.get("bat", row.get("battery"))
        if raw_bat not in (None, ""):
            try:
                battery = max(0, min(100, int(float(raw_bat))))
            except (TypeError, ValueError):
                battery = None

        out[did] = {
            "online": flag("power"),
            "battery": battery,
            "name": str(row.get("device_name") or row.get("name") or "").strip(),
            "for_sending": flag("is_work"),
            "last_active": str(row.get("date_last_active") or ""),
        }
    return out


def format_sim_label(name, status):
    """Dropdown text for one SIM, e.g.
         "Airtel Pulse — online 87%"
         "Voda Restrict 1 — OFFLINE 42%"
         "Airtel 2 — online 90% (sending off)"
       Unknown status falls back to the bare name so the dropdown is never
       blank while the first fetch is still in flight."""
    if not status:
        return name
    online = status.get("online")
    bat = status.get("battery")
    state = "" if online is None else ("online" if online else "OFFLINE")
    bits = [b for b in (state, f"{bat}%" if bat is not None else "") if b]
    label = f"{name} — {' '.join(bits)}" if bits else name
    # is_work=0 means SemySMS won't use this device to send, even when online.
    if status.get("for_sending") is False:
        label += " (sending off)"
    return label


def label_to_name(label):
    """Recover the plain SIM name from a decorated dropdown label."""
    return str(label).split(" — ")[0].strip()


def is_unusable(status):
    """Offline, or online but disabled for sending — either way, sends fail."""
    if not status:
        return False
    return status.get("online") is False or status.get("for_sending") is False


def is_offline(status):
    """True only when we positively know the device is offline."""
    return bool(status) and status.get("online") is False


# ─────────────── cached, thread-safe status store ───────────────

class DeviceStatusCache:
    """Holds the last known device status with a TTL. Reads never block; a
    refresh runs on a worker thread and updates the cache when it returns."""

    def __init__(self, ttl=DEVICE_STATUS_TTL):
        self.ttl = ttl
        self._data = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()
        self._in_flight = False

    def get(self, device_id):
        with self._lock:
            return self._data.get(str(device_id))

    def all(self):
        with self._lock:
            return dict(self._data)

    def is_stale(self):
        return (time.monotonic() - self._fetched_at) > self.ttl

    def set(self, data):
        with self._lock:
            self._data = data or {}
            self._fetched_at = time.monotonic()
            self._in_flight = False

    def refresh_async(self, token, on_done=None, force=False):
        """Kick off a background refresh if the cache is stale. Returns True if
        a fetch was started. Never blocks the caller."""
        if not token:
            return False
        with self._lock:
            if self._in_flight:
                return False
            if not force and self._data and not self.is_stale():
                return False
            self._in_flight = True

        def worker():
            data = {}
            try:
                url, params = build_devices_request(token)
                r = session.get(url, params=params, timeout=15)
                data = parse_devices(r.json())
            except Exception:
                data = {}
            if data:
                self.set(data)
            else:
                with self._lock:
                    self._in_flight = False
            if on_done:
                try:
                    on_done(data)
                except Exception:
                    pass
        threading.Thread(target=worker, daemon=True).start()
        return True


# module-level cache shared by both tabs
device_status = DeviceStatusCache()
