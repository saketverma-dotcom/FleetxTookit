"""Gist-backed per-tab access rules: fetch, cache, resolve, push."""
import json
import os
import time

import requests

from .config import (ACCESS_FILE, ACCESS_URL, ADMIN_EMAILS, ALLOWED_DOMAIN,
                     CONTROLLABLE_TABS, GIST_API, GIST_FILENAME)

_ACCESS_SNAPSHOT = None
_REMOTE_META = {}


def set_snapshot(snap):
    """Rules snapshot taken at login; read via _access_snapshot()."""
    global _ACCESS_SNAPSHOT
    _ACCESS_SNAPSHOT = snap


def get_remote_meta():
    return globals().get("_REMOTE_META") or {}


def get_shared_sensor_types():
    """Custom sensor types shared across the team, stored under the Gist meta
    key "_sensor_types" (a list). Empty list if none / offline."""
    v = get_remote_meta().get("_sensor_types")
    return [str(x) for x in v] if isinstance(v, list) else []


def push_sensor_type(new_type, gh_token):
    """Add one custom sensor type to the shared Gist list (idempotent).
    Read-modify-write of only the _sensor_types meta key; user rules untouched.
    Returns (ok, message)."""
    import requests as _rq
    from .config import GIST_API, GIST_FILENAME
    new_type = (new_type or "").strip()
    if not new_type:
        return False, "Empty sensor type."
    hdrs = {"Authorization": f"Bearer {gh_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "FleetXToolkit", "X-GitHub-Api-Version": "2022-11-28"}
    try:
        cur = _rq.get(GIST_API, headers=hdrs, timeout=20).json()
        content = json.loads(cur["files"][GIST_FILENAME]["content"])
        types = content.get("_sensor_types")
        if not isinstance(types, list):
            types = []
        if new_type in types:
            return True, "Already in the shared list."
        types.append(new_type)
        content["_sensor_types"] = types
        body = {"files": {GIST_FILENAME: {"content": json.dumps(content, indent=2)}}}
        r = _rq.patch(GIST_API, headers=hdrs, json=body, timeout=20)
        if r.status_code in (200, 201):
            globals().setdefault("_REMOTE_META", {})["_sensor_types"] = types
            return True, f"Saved '{new_type}' to shared list."
        return False, f"GitHub HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        return False, f"Error: {e}"


def push_access_to_gist(access_map, gh_token):
    """Write access_map back to the Gist via GitHub API. Returns (ok, message)."""
    try:
        payload = {"files": {GIST_FILENAME: {"content": json.dumps(access_map, indent=2)}}}
        r = requests.patch(
            GIST_API,
            json=payload,
            headers={
                "Authorization": f"Bearer {gh_token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "FleetXToolkit",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=20,
        )
        if r.status_code in (200, 201):
            # refresh local cache too
            try:
                with open(ACCESS_FILE, "w") as f:
                    json.dump(access_map, f, indent=2)
            except Exception:
                pass
            return True, "Saved to Gist."
        return False, f"GitHub API HTTP {r.status_code}: {r.text[:150]}"
    except Exception as e:
        return False, f"Error: {e}"

def fetch_remote_access():
    """Fetch rules from ACCESS_URL. On success, cache locally and return dict.
       On any failure, return None so caller can fall back to cache.
       A cache-buster query defeats GitHub's ~5-min CDN cache so edits appear instantly."""
    try:
        buster = str(int(time.time()))
        url = ACCESS_URL + ("&" if "?" in ACCESS_URL else "?") + "_cb=" + buster
        r = requests.get(url, headers={
            "User-Agent": "FleetXToolkit",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        }, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                # Keys starting with "_" are metadata (e.g. "_latest_version"), not users
                globals()["_REMOTE_META"] = {k: v for k, v in data.items()
                                             if str(k).startswith("_")}
                norm = {k.strip().lower(): [t for t in v] if isinstance(v, list) else v
                        for k, v in data.items() if not str(k).startswith("_")}
                try:
                    with open(ACCESS_FILE, "w") as f:
                        json.dump(norm, f, indent=2)
                except Exception:
                    pass
                return norm
    except Exception:
        pass
    return None

def load_access():
    """Live rules from the Gist; fall back to last-cached local copy if offline."""
    remote = fetch_remote_access()
    if remote is not None:
        return remote
    try:
        with open(ACCESS_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k.strip().lower(): v for k, v in data.items()}
    except Exception:
        pass
    return {}

def save_access(access_map):
    """Writes only to the LOCAL cache. Remote Gist is edited in the browser."""
    try:
        with open(ACCESS_FILE, "w") as f:
            json.dump(access_map, f, indent=2)
    except Exception:
        pass

def is_admin(email):
    return email.strip().lower() in ADMIN_EMAILS

def _access_snapshot():
    snap = globals().get("_ACCESS_SNAPSHOT")
    return snap if snap is not None else load_access()

def is_authorized(email):
    """Authorized if fleetx domain AND (admin OR has at least one tab granted)."""
    e = email.strip().lower()
    if not e.endswith(ALLOWED_DOMAIN):
        return False
    if is_admin(e):
        return True
    return bool(_access_snapshot().get(e))

def allowed_tabs_for(email):
    """Tabs this user may see. Admin => everything."""
    e = email.strip().lower()
    if is_admin(e):
        return list(CONTROLLABLE_TABS)
    return [t for t in _access_snapshot().get(e, []) if t in CONTROLLABLE_TABS]
