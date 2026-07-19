"""
FleetX_Toolkit.py — entry point.
═════════════════════════════════════════════════════════════════════════════
Code lives in the fleetx_toolkit/ package:
  config.py          constants, paths, settings store
  state.py           runtime state (logged-in email)
  storage.py         secrets (Windows Credential Manager) + command library
  api_client.py      FleetX API headers
  io_utils.py        pasted-ID / curl / Excel parsing, result logs
  access_control.py  Gist-backed per-tab access rules
  updater.py         self-update (Gist meta -> GitHub Release exe)
  ui/app.py          main window + core run loop
  ui/tabs_*.py       tab mixins (devices, commands, misc, admin)

Dependencies:  pip install requests openpyxl keyring
Run:           python FleetX_Toolkit.py
Build exe:     python -m PyInstaller --onefile --noconsole ^
                   --hidden-import keyring.backends.Windows FleetX_Toolkit.py
═════════════════════════════════════════════════════════════════════════════
"""
from fleetx_toolkit.ui.app import FleetXToolkit

if __name__ == "__main__":
    app = FleetXToolkit()
    app.mainloop()
