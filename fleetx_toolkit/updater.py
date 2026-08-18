"""Auto-update: version check from Gist meta, verified download, bat-swap restart."""
import hashlib
import os
import re
import subprocess
import sys

import requests
from .http import session

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
        with session.get(url, stream=True, timeout=180,
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
       waits for this process to exit, swaps the file, relaunches, deletes itself.

       ROLLBACK-SAFE (v3.11): the old exe is RENAMED to a .bak (not deleted), and
       the new file is only moved into place after that succeeds. If anything goes
       wrong — most commonly antivirus quarantining the .new file mid-swap, which
       previously left users with a broken install — the backup is restored so the
       app always starts, just on the old version."""
    exe = sys.executable
    # Refuse to start a swap if the downloaded file vanished (e.g. quarantined)
    if not os.path.exists(new_path) or os.path.getsize(new_path) == 0:
        return False, ("The downloaded update is missing or empty — it was most "
                       "likely quarantined by antivirus. Update cancelled; the "
                       "current version is untouched.")
    bak = exe + ".bak"
    bat = os.path.join(os.path.dirname(exe), "_fleetx_update.bat")
    with open(bat, "w") as f:
        f.write(f'''@echo off
rem Wait for the running app to exit
:wait
timeout /t 1 /nobreak >nul
2>nul (>>"{exe}" call ) && (goto swap) || (goto wait)

:swap
rem Abort if the new file disappeared (antivirus) - leave the old exe alone
if not exist "{new_path}" goto abort
del "{bak}" 2>nul
move /y "{exe}" "{bak}" >nul
if errorlevel 1 goto abort
move /y "{new_path}" "{exe}" >nul
if errorlevel 1 goto rollback
if not exist "{exe}" goto rollback
start "" "{exe}"
del "{bak}" 2>nul
goto done

:rollback
rem Swap failed - put the working version back so the app still runs
move /y "{bak}" "{exe}" >nul
start "" "{exe}"
goto done

:abort
rem Nothing was changed; relaunch the existing version
start "" "{exe}"

:done
del "%~f0"
''')
    subprocess.Popen(["cmd", "/c", bat],
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    os._exit(0)
