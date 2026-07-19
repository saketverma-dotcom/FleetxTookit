"""Auto-update: version check from Gist meta, verified download, bat-swap restart."""
import hashlib
import os
import re
import subprocess
import sys

import requests

from .access_control import get_remote_meta
from .config import APP_VERSION


def _ver_tuple(v):
    nums = re.findall(r"\d+", str(v))
    return tuple(int(x) for x in (nums or ["0"])[:4])

def check_update():
    """(latest, url, sha256) if the Gist advertises a newer version, else None."""
    meta = get_remote_meta()
    latest = str(meta.get("_latest_version", "")).strip()
    url    = str(meta.get("_download_url", "")).strip()
    sha    = str(meta.get("_sha256", "")).strip().lower()
    if latest and url and _ver_tuple(latest) > _ver_tuple(APP_VERSION):
        return latest, url, sha
    return None

def download_update(url, sha256, progress_cb=None):
    """Download the new exe next to the running one as <exe>.new and verify SHA256.
       Returns (new_path, None) on success or (None, error_message)."""
    if not getattr(sys, "frozen", False):
        return None, "Auto-update only works in the built exe (dev mode detected)."
    exe = sys.executable
    new_path = exe + ".new"
    try:
        h = hashlib.sha256()
        with requests.get(url, stream=True, timeout=180,
                          headers={"User-Agent": "FleetXToolkit"}) as r:
            if r.status_code != 200:
                return None, f"Download HTTP {r.status_code}"
            total = int(r.headers.get("content-length") or 0)
            done = 0
            with open(new_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=262144):
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        progress_cb(done, total)
        if sha256 and h.hexdigest().lower() != sha256:
            os.remove(new_path)
            return None, "SHA256 mismatch — download corrupted or tampered. Update aborted."
        return new_path, None
    except Exception as e:
        try:
            if os.path.exists(new_path):
                os.remove(new_path)
        except Exception:
            pass
        return None, f"Download failed: {e}"

def apply_update_and_restart(new_path):
    """A running exe can't overwrite itself on Windows: spawn a detached .bat that
       waits for this process to exit, swaps the file, relaunches, deletes itself."""
    exe = sys.executable
    bat = os.path.join(os.path.dirname(exe), "_fleetx_update.bat")
    with open(bat, "w") as f:
        f.write(f'''@echo off
:wait
timeout /t 1 /nobreak >nul
del "{exe}" 2>nul
if exist "{exe}" goto wait
move /y "{new_path}" "{exe}" >nul
start "" "{exe}"
del "%~f0"
''')
    subprocess.Popen(["cmd", "/c", bat],
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    os._exit(0)
