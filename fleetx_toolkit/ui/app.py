import datetime
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import requests

from .. import access_control, state
from ..access_control import (allowed_tabs_for, fetch_remote_access, is_admin,
                              is_authorized, load_access, push_access_to_gist)
from ..api_client import api_headers
from ..config import (ACCESS_FILE, ACCESS_URL, ADMIN_EMAILS, ALLOWED_DOMAIN,
                      API_BASE, APP_BASE, APP_VERSION, ASSIGNEE_DIRECTORY,
                      CLIENT_ID, CONTROLLABLE_TABS, DELAY_MS, LOGIN_URL,
                      LOGS_DIR, MOBILE_PARAM, SENSOR_PRESETS, SIM_PROVIDERS,
                      TOKEN_PARAM, load_settings, save_settings)
from ..io_utils import (load_excel_column, load_excel_records, parse_curl_command,
                        parse_pasted_ids, parse_pasted_pairs, save_result_log)
from ..storage import (clear_credentials, load_commands, load_credentials,
                       load_gh_token, save_commands, save_credentials,
                       save_gh_token)
from ..updater import (apply_update_and_restart, check_update, download_update)
from ..logic import RETRY_BACKOFFS, retry_wait
from .tabs_admin import AdminTabsMixin
from .tabs_commands import CommandTabsMixin
from .tabs_devices import DeviceTabsMixin
from .tabs_misc import MiscTabsMixin


class FleetXToolkit(DeviceTabsMixin, CommandTabsMixin, MiscTabsMixin,
                    AdminTabsMixin, tk.Tk):
    """Main window. Core plumbing lives here (login, run loop, settings);
    tab UIs come from the mixins."""

    def __init__(self):
        super().__init__()
        self.title("FleetX Toolkit v2")
        self.geometry("960x740")
        self.token = None
        self.stop_flag = False
        self.is_admin_user = False
        self.commands = load_commands()
        self._build_login()
    def _build_login(self):
        self.login_frame = ttk.Frame(self, padding=40)
        self.login_frame.pack(expand=True)

        ttk.Label(self.login_frame, text="FleetX Toolkit Login",
                  font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 20))

        saved_email, saved_pass = load_credentials()

        ttk.Label(self.login_frame, text="Email:").grid(row=1, column=0, sticky="e", pady=4)
        self.email_var = tk.StringVar(value=saved_email)
        ttk.Entry(self.login_frame, textvariable=self.email_var, width=38).grid(row=1, column=1, pady=4)

        ttk.Label(self.login_frame, text="Password:").grid(row=2, column=0, sticky="e", pady=4)
        self.pass_var = tk.StringVar(value=saved_pass)
        ttk.Entry(self.login_frame, textvariable=self.pass_var, show="*", width=38).grid(row=2, column=1, pady=4)

        self.remember_var = tk.BooleanVar(value=bool(saved_email))
        ttk.Checkbutton(self.login_frame, text="Remember me on this computer",
                        variable=self.remember_var).grid(row=3, column=0, columnspan=2, pady=(4, 0))

        ttk.Button(self.login_frame, text="Login", command=self.do_login).grid(
            row=4, column=0, columnspan=2, pady=(10, 6))

        ttk.Separator(self.login_frame, orient="horizontal").grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(self.login_frame, text="Or paste Bearer token directly:").grid(
            row=6, column=0, columnspan=2)
        self.token_var = tk.StringVar()
        ttk.Entry(self.login_frame, textvariable=self.token_var, width=48).grid(
            row=7, column=0, columnspan=2, pady=4)
        ttk.Button(self.login_frame, text="Use Token", command=self.use_manual_token).grid(
            row=8, column=0, columnspan=2, pady=6)
        self.login_status = ttk.Label(self.login_frame, text="", foreground="red")
        self.login_status.grid(row=9, column=0, columnspan=2, pady=6)

        if saved_email and saved_pass:
            self.after(400, self.do_login)
    def do_login(self):
        # APPROVED FIX: network I/O used to run on the main thread — a hung
        # connection froze the whole window for up to 30s. Now threaded.
        if getattr(self, "_logging_in", False):
            return
        email = self.email_var.get().strip()
        password = self.pass_var.get()
        if not email or not password:
            self.login_status.config(text="Enter email and password.")
            return
        remember = self.remember_var.get()          # read Tk vars on main thread
        self._logging_in = True
        self.login_status.config(text="Logging in...", foreground="black")
        threading.Thread(target=self._login_worker,
                         args=(email, password, remember), daemon=True).start()
    def _login_status_safe(self, text, fg="red"):
        def upd():
            try:
                self.login_status.config(text=text, foreground=fg)
            except tk.TclError:
                pass                                 # login frame already gone
        self.after(0, upd)
    def _login_worker(self, email, password, remember):
        """Network-only: login POST + access fetch. UI updates go via after()."""
        login_headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en",
            "cache-control": "no-cache",
            "clientid": CLIENT_ID,
            "dnt": "1",
            "origin": API_BASE,
            "pragma": "no-cache",
            "referer": f"{API_BASE}/users/login",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        }
        fields = {"username": (None, email), "password": (None, password),
                  "grant_type": (None, "password")}

        resp = None
        last_err = ""
        try:
            resp = requests.post(LOGIN_URL, files=fields, headers=login_headers,
                                 timeout=30, allow_redirects=True)
        except Exception as e:
            last_err = str(e)

        try:
            body = {}
            if resp is not None:
                try:
                    body = resp.json()
                except Exception:
                    body = {}
            # token may be nested under data/result
            def _find_token(d):
                if not isinstance(d, dict):
                    return None
                for k in ("access_token", "token", "value", "accessToken", "authToken", "jwt"):
                    if d.get(k):
                        return d[k]
                for sub in ("data", "result", "payload"):
                    if isinstance(d.get(sub), dict):
                        t = _find_token(d[sub])
                        if t:
                            return t
                return None
            token = _find_token(body)

            if resp is None:
                self._logging_in = False
                self._login_status_safe(f"Login error (network): {last_err}. "
                                        "Paste token manually below.")
                return

            if resp.status_code == 200 and token:
                snapshot = load_access()             # network fetch, still off-thread
                def finish():
                    self._logging_in = False
                    access_control.set_snapshot(snapshot)
                    if not is_authorized(email):
                        self.login_status.config(
                            text="Access denied: this email is not authorized for this tool.\n"
                                 "Contact saket.verma@fleetx.io for access.",
                            foreground="red")
                        return
                    self.token = token
                    state.user_email = email
                    self.is_admin_user = is_admin(email)
                    if remember:
                        save_credentials(email, password)
                    else:
                        clear_credentials()
                    self._enter_main()
                self.after(0, finish)
            else:
                detail = ""
                if isinstance(body, dict):
                    detail = body.get("error_description") or body.get("message") or body.get("error") or ""
                if not detail:
                    detail = (resp.text or "")[:150]
                final_url = resp.url
                self._logging_in = False
                self._login_status_safe(
                    f"Login failed (HTTP {resp.status_code}) at {final_url}\n{detail}")
        except Exception as e:
            self._logging_in = False
            self._login_status_safe(f"Login error: {e}. Paste token manually.")
    def use_manual_token(self):
        tok = self.token_var.get().strip().replace("Bearer ", "")
        if not tok:
            self.login_status.config(text="Token is empty.", foreground="red")
            return
        email = self.email_var.get().strip()
        if not email:
            self.login_status.config(text="Enter your fleetx email above to use a manual token.",
                                     foreground="red")
            return
        access_control.set_snapshot(load_access())
        if not is_authorized(email):
            self.login_status.config(
                text="Access denied: this email is not authorized for this tool.",
                foreground="red")
            return
        self.token = tok
        state.user_email = email
        self.is_admin_user = is_admin(email)
        self._enter_main()
    def _enter_main(self):
        self.login_frame.destroy()
        top = ttk.Frame(self, padding=(10, 6))
        top.pack(fill="x")
        who = state.user_email or "manual token"
        role = "ADMIN" if self.is_admin_user else "USER"
        ttk.Label(top, text=f"Logged in: {who}  [{role}]   |   v{APP_VERSION}   |   "
                            f"Token: {self.token[:8]}...",
                  foreground="green").pack(side="left")
        upd = check_update()
        if upd:
            ttk.Button(top, text=f"⬆ Update to v{upd[0]}",
                       command=lambda u=upd: self._do_self_update(*u)).pack(side="left", padx=10)
        ttk.Button(top, text="Logout", command=self._logout).pack(side="right")

        # Runtime settings (persisted)
        s = load_settings()
        self.delay_var = tk.StringVar(value=str(s.get("delay_ms", DELAY_MS)))
        self.dry_run_var = tk.BooleanVar(value=False)   # always starts OFF for safety
        self.auto_retry_var = tk.BooleanVar(value=bool(s.get("auto_retry", True)))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)

        # Snapshot rules once at login (avoids repeated Gist hits during tab build)
        access_control.set_snapshot(load_access())
        # Which tabs may this user see?
        # SECURITY: no verified email => no tabs (was: all tabs). The manual-token
        # path already requires an email, so normal flows are unaffected.
        allowed = allowed_tabs_for(state.user_email) if state.user_email else []

        tab_builders = {
            "Device Add":         self._tab_device_add,
            "SIM Inventory":      self._tab_sim_inventory,
            "SIM Update":         self._tab_sim_update,
            "Vehicle-Device Map": self._tab_vehicle_map,
            "Send Command":       self._tab_send_command,
            "Sequential 2-Phase": self._tab_seq_commands,
            "SensorType":         self._tab_sensor_type,
            "Assets":             self._tab_assets,
            "Tickets":            self._tab_tickets,
        }
        for tab_name in CONTROLLABLE_TABS:
            if tab_name in allowed and tab_name in tab_builders:
                tab_builders[tab_name]()

        # Settings tab — available to everyone
        self._tab_settings()

        # Admin-only access-control panel (always last, admins only)
        if self.is_admin_user:
            self._tab_user_access()

        if self.nb.index("end") == 0:
            # No tabs granted — show a friendly placeholder
            ph = ttk.Frame(self.nb, padding=20)
            self.nb.add(ph, text="No Access")
            ttk.Label(ph, text="You don't have access to any tools yet.\n\n"
                              "Ask the admin (saket.verma@fleetx.io) to grant you access.",
                      font=("Segoe UI", 11)).pack(pady=40)

        logf = ttk.LabelFrame(self, text="Live Log", padding=4)
        logf.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log_box = scrolledtext.ScrolledText(logf, height=11, state="disabled",
                                                  font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True)
        self.log_box.tag_config("ok", foreground="green")
        self.log_box.tag_config("err", foreground="red")
        self.log_box.tag_config("info", foreground="blue")

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="STOP current run", command=self._stop).pack(side="right")
    def _do_self_update(self, latest, url, sha):
        """Download off the main thread, verify, then swap + relaunch."""
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Update", "Auto-update only runs from the built exe.\n"
                                          f"Dev mode: pull v{latest} from the repo instead.")
            return
        if getattr(self, "_busy", False):
            messagebox.showwarning("Update", "Finish or STOP the current run first.")
            return
        if not messagebox.askyesno("Update",
                f"Update FleetX Toolkit v{APP_VERSION} → v{latest}?\n\n"
                "The app will restart automatically."):
            return
        self._busy = True   # block bulk runs while updating
        self.log(f"\n⬆ Downloading v{latest}...", "info")
        def worker():
            last = [0]
            def prog(done, total):
                if done - last[0] >= 2 * 1048576 or done == total:   # every ~2 MB
                    last[0] = done
                    self.log(f"    {done // 1048576} / {total // 1048576} MB", "info")
            new_path, err = download_update(url, sha, prog)
            if err:
                self._busy = False
                self.log(f"  ✗ Update failed: {err}", "err")
                self._ui_error("Update", err)
                return
            self.log("  ✓ Verified. Restarting...", "info")
            self.after(500, lambda: apply_update_and_restart(new_path))
        threading.Thread(target=worker, daemon=True).start()
    def _logout(self):
        if load_credentials()[0]:
            if messagebox.askyesno("Logout", "Also forget saved credentials on this computer?"):
                clear_credentials()
        for w in self.winfo_children():
            w.destroy()
        self.token = None
        self._build_login()
    def _stop(self):
        self.stop_flag = True
        self.log("  ⏹ Stop requested — finishing current request...", "err")
    def log(self, msg, tag=None):
        # FIX-1: Tk widgets may only be touched from the main thread.
        # Worker threads marshal via after(); behavior otherwise identical.
        if threading.current_thread() is not threading.main_thread():
            self.after(0, lambda: self.log(msg, tag))
            return
        self.log_box.config(state="normal")
        self.log_box.insert("end", msg + "\n", tag)
        self.log_box.see("end")
        self.log_box.config(state="disabled")
    def _ui_error(self, title, msg):
        if threading.current_thread() is threading.main_thread():
            messagebox.showerror(title, msg)
        else:
            self.after(0, lambda: messagebox.showerror(title, msg))
    def _ui_askyesno(self, title, msg):
        if threading.current_thread() is threading.main_thread():
            return messagebox.askyesno(title, msg)
        result = {"v": False}
        done = threading.Event()
        def show():
            result["v"] = messagebox.askyesno(title, msg)
            done.set()
        self.after(0, show)
        done.wait()
        return result["v"]
    def _input_source(self, parent, label="Input Source"):
        frame = ttk.LabelFrame(parent, text=label, padding=6)
        frame.pack(fill="x", pady=4)
        mode = tk.StringVar(value="paste")
        ttk.Radiobutton(frame, text="Paste (one per line)", variable=mode,
                        value="paste").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(frame, text="Excel file", variable=mode,
                        value="excel").grid(row=0, column=1, sticky="w", padx=16)
        path_var = tk.StringVar()
        ttk.Entry(frame, textvariable=path_var, width=52).grid(row=1, column=0, columnspan=2,
                                                                sticky="w", pady=3)
        ttk.Button(frame, text="Browse...",
                   command=lambda: path_var.set(
                       filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")]))
                   ).grid(row=1, column=2, padx=6)
        paste = scrolledtext.ScrolledText(frame, height=5, width=60, font=("Consolas", 9))
        paste.grid(row=2, column=0, columnspan=3, sticky="ew", pady=3)
        return {"mode": mode, "path": path_var, "paste": paste}
    def _get_ids(self, src):
        if src["mode"].get() == "excel":
            path = src["path"].get().strip()
            if not path:
                self._ui_error("Input", "Select an Excel file.")
                return None
            vals = self._load_excel_safe(load_excel_column, path)
            if vals is None:
                return None
            return self._dedupe(vals)
        ids = parse_pasted_ids(src["paste"].get("1.0", "end"))
        if not ids:
            self._ui_error("Input", "Paste at least one ID.")
            return None
        return self._dedupe(ids)
    def _load_excel_safe(self, loader, path):
        """FIX-3: bad/locked/.xls files used to kill the worker thread silently."""
        try:
            return loader(path)
        except Exception as e:
            self.log(f"  ✗ Could not read Excel '{path}': {e!r}", "err")
            self._ui_error("Excel",
                f"Could not read the Excel file:\n{e}\n\n"
                "• Close the file if it's open in Excel\n"
                "• Only .xlsx is supported (not .xls)")
            return None
    def _dedupe(self, ids):
        seen, out = set(), []
        for x in ids:
            if x not in seen:
                seen.add(x)
                out.append(x)
        removed = len(ids) - len(out)
        if removed:
            self.log(f"  ℹ Removed {removed} duplicate ID(s) — {len(out)} unique remain.", "info")
        return out
    def _run_thread(self, fn):
        # FIX-2: one run at a time; any uncaught error in a worker is logged
        # instead of killing the thread silently.
        if getattr(self, "_busy", False):
            messagebox.showwarning("Busy",
                "A run is already in progress.\nPress STOP or wait for it to finish.")
            return
        self._busy = True
        self.stop_flag = False
        def _wrapper():
            try:
                fn()
            except Exception as e:
                self.log(f"  ✗ Run aborted — unexpected error: {e!r}", "err")
            finally:
                self._busy = False
        threading.Thread(target=_wrapper, daemon=True).start()
    def _current_delay(self):
        try:
            return max(0.2, float(self.delay_var.get()) / 1000)
        except Exception:
            return DELAY_MS / 1000
    def _do_one(self, item, fn, columns):
        """Execute one request with 429 + network-error backoff. Returns (result_dict, http_status)."""
        attempt = 0
        while True:
            try:
                fields, r = fn(item)
                wait = retry_wait(attempt)
                if r.status_code == 429 and wait is not None:
                    attempt += 1
                    self.log(f"      429 rate-limited — waiting {wait}s "
                             f"(retry {attempt}/{len(RETRY_BACKOFFS)})...", "err")
                    time.sleep(wait)
                    continue
                ok = 200 <= r.status_code < 300
                return ({"fields": fields, "status": r.status_code,
                         "body": r.text[:250],
                         "ts": f"{datetime.datetime.now():%H:%M:%S}"},
                        r.status_code, fields, ok)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                # APPROVED FIX: transient network drop — back off and retry
                # instead of failing the rest of the list at full speed.
                wait = retry_wait(attempt)
                if wait is not None and not self.stop_flag:
                    attempt += 1
                    self.log(f"      ⚡ network error — waiting {wait}s "
                             f"(retry {attempt}/{len(RETRY_BACKOFFS)}): {type(e).__name__}", "err")
                    for _ in range(wait):          # STOP-interruptible wait
                        if self.stop_flag:
                            break
                        time.sleep(1)
                    continue
                fields = (str(item)[:40],) + ("",) * (len(columns) - 1)
                return ({"fields": fields, "status": 0, "body": str(e),
                         "ts": f"{datetime.datetime.now():%H:%M:%S}"},
                        0, fields, False)
            except Exception as e:
                fields = (str(item)[:40],) + ("",) * (len(columns) - 1)
                return ({"fields": fields, "status": 0, "body": str(e),
                         "ts": f"{datetime.datetime.now():%H:%M:%S}"},
                        0, fields, False)
    def _loop(self, items, label, fn, columns, save=True):
        """Request loop with: dry-run, 429 backoff, 401 halt, progress/ETA,
        one automatic retry pass over failures.
        Returns (results, halted). save=False lets callers combine multiple
        phases into one result log (used by Sequential 2-Phase)."""
        # ── Dry run: preview only, zero API calls ──
        if getattr(self, "dry_run_var", None) and self.dry_run_var.get():
            self.log(f"\n══ [DRY RUN] {label} — {len(items)} items — NO requests sent ══", "info")
            for i, item in enumerate(items[:50], 1):
                self.log(f"  [DRY {i}/{len(items)}] would process: {str(item)[:60]}")
            if len(items) > 50:
                self.log(f"  ... and {len(items) - 50} more.", "info")
            self.log("  Dry run complete. Untick 'Dry run' in Settings to execute.", "info")
            return [], False

        self.log(f"\n══ {label} — {len(items)} items ══", "info")
        results = []
        failed_items = []
        start = time.time()
        halted = False

        for i, item in enumerate(items, 1):
            if self.stop_flag:
                break
            res, status, fields, ok = self._do_one(item, fn, columns)
            results.append(res)
            self.log(f"  [{i}/{len(items)}] {fields[0]} → HTTP {status}",
                     "ok" if ok else "err")
            if not ok:
                failed_items.append(item)

            # Token expiry: halt instead of burning through the whole list
            if status == 401:
                self.log("\n  ⛔ HTTP 401 — token expired/invalid. HALTING run. "
                         "Logout and login again, then use 'retry failed' output.", "err")
                halted = True
                break

            # Progress + ETA every 10 items
            if i % 10 == 0 and i < len(items):
                elapsed = time.time() - start
                per_item = elapsed / i
                remaining = per_item * (len(items) - i)
                eta = datetime.datetime.now() + datetime.timedelta(seconds=remaining)
                self.log(f"      ▸ {i}/{len(items)} ({i*100//len(items)}%) — "
                         f"ETA {eta:%H:%M:%S} (~{int(remaining//60)}m {int(remaining%60)}s left)",
                         "info")
            time.sleep(self._current_delay())

        # ── One automatic retry pass over failures (skip if halted/stopped) ──
        if (failed_items and not halted and not self.stop_flag
                and getattr(self, "auto_retry_var", None) and self.auto_retry_var.get()):
            self.log(f"\n  ↻ Auto-retrying {len(failed_items)} failed item(s) once...", "info")
            time.sleep(3)
            still_failed = []
            for j, item in enumerate(failed_items, 1):
                if self.stop_flag:
                    break
                res, status, fields, ok = self._do_one(item, fn, columns)
                res["fields"] = tuple(list(res["fields"])) if ok else res["fields"]
                results.append({**res, "body": "[RETRY] " + res["body"]})
                self.log(f"  [retry {j}/{len(failed_items)}] {fields[0]} → HTTP {status}",
                         "ok" if ok else "err")
                if not ok:
                    still_failed.append(item)
                time.sleep(self._current_delay())
            failed_items = still_failed

        # Surface remaining failures as a paste-ready list for a manual re-run
        if failed_items:
            self.log(f"\n  ✗ {len(failed_items)} item(s) still failed. "
                     "Paste-ready list (copy from here to re-run just these):", "err")
            self.log("\n".join(str(x) for x in failed_items))

        if save:
            save_result_log(results, columns, label, self.log)
        return results, halted
    def _tab_settings(self):
        tab = ttk.Frame(self.nb, padding=12)
        self.nb.add(tab, text="⚙ Settings")

        f = ttk.LabelFrame(tab, text="Request behaviour", padding=10)
        f.pack(fill="x", pady=4)

        r1 = ttk.Frame(f); r1.pack(fill="x", pady=3)
        ttk.Label(r1, text="Delay between requests (ms):").pack(side="left")
        ttk.Entry(r1, textvariable=self.delay_var, width=8).pack(side="left", padx=6)
        ttk.Label(r1, text="(1250 recommended; raise if you see 429 rate-limits)",
                  foreground="gray").pack(side="left")

        ttk.Checkbutton(f, text="Auto-retry failed items once at end of each run",
                        variable=self.auto_retry_var).pack(anchor="w", pady=3)

        ttk.Checkbutton(f, text="🧪 DRY RUN — preview items without sending any request "
                                "(applies to every tab until unticked)",
                        variable=self.dry_run_var).pack(anchor="w", pady=3)

        ttk.Button(f, text="💾 Save settings",
                   command=self._save_settings_ui).pack(anchor="w", pady=6)
        self.settings_status = ttk.Label(f, text="", foreground="green")
        self.settings_status.pack(anchor="w")

        g = ttk.LabelFrame(tab, text="Logs", padding=10)
        g.pack(fill="x", pady=8)
        ttk.Label(g, text=f"All run results save to:  {LOGS_DIR}",
                  foreground="gray").pack(anchor="w")
        ttk.Button(g, text="📂 Open logs folder", command=self._open_logs).pack(anchor="w", pady=4)

        h = ttk.LabelFrame(tab, text="About", padding=10)
        h.pack(fill="x", pady=4)
        ttk.Label(h, text=f"FleetX Toolkit v{APP_VERSION}\n"
                          "429 rate-limits: auto-backoff 5s/15s/30s per item.\n"
                          "401 token expiry: run halts immediately; remaining failures are "
                          "printed as a paste-ready list.").pack(anchor="w")
    def _save_settings_ui(self):
        try:
            d = int(self.delay_var.get())
            if d < 200:
                raise ValueError
        except ValueError:
            self.settings_status.config(text="Delay must be a number ≥ 200 ms.", foreground="red")
            return
        save_settings({"delay_ms": d, "auto_retry": bool(self.auto_retry_var.get())})
        self.settings_status.config(text="Settings saved.", foreground="green")
    def _open_logs(self):
        os.makedirs(LOGS_DIR, exist_ok=True)
        try:
            os.startfile(LOGS_DIR)            # Windows
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", LOGS_DIR])
