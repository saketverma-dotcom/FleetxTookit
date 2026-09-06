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
from ..http import session

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
                       save_bearer_token, load_bearer_token, clear_bearer_token,
                       save_gh_token)
from ..updater import (apply_update_and_restart, check_update, download_update)
from ..logic import RETRY_BACKOFFS, retry_wait
from .tabs_admin import AdminTabsMixin
from .tabs_commands import CommandTabsMixin
from .tabs_devices import DeviceTabsMixin
from .tabs_misc import MiscTabsMixin
from .tabs_sms import SmsTabMixin
from .tabs_messaging import MessagingTabMixin
from .tabs_sms_auth import SmsAuthMixin


class FleetXToolkit(DeviceTabsMixin, CommandTabsMixin, MiscTabsMixin,
                    SmsTabMixin, MessagingTabMixin, SmsAuthMixin, AdminTabsMixin, tk.Tk):
    """Main window. Core plumbing lives here (login, run loop, settings);
    tab UIs come from the mixins."""

    def __init__(self):
        super().__init__()
        self._enable_dpi_awareness()
        self.title("FleetX SMS / Messaging")
        self.geometry("1000x740")
        self.minsize(820, 560)          # below this the layout can't stay usable
        self.token = None
        self.stop_flag = False
        self.is_admin_user = False
        self.commands = load_commands()
        self._init_ui_scale()
        self._build_sms_front()

    @staticmethod
    def _enable_dpi_awareness():
        """On Windows, tell the OS we handle scaling ourselves so the UI is
        rendered crisply at 125%/150% display scaling instead of being
        bitmap-stretched (which blurs text and overflows layouts)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)   # system DPI aware
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()        # older Windows
        except Exception:
            pass

    # ── UI scale: auto DPI factor × user preference ──

    def _init_ui_scale(self):
        """Because we're DPI-aware, Windows no longer enlarges the app — so at
        150% scaling everything would render tiny. We detect the real scale
        factor and apply it ourselves, then multiply by the user's saved
        preference so they can fine-tune text size."""
        import tkinter.font as tkfont
        # remember each named font's design size once
        self._base_font_sizes = {}
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                     "TkHeadingFont", "TkFixedFont", "TkTooltipFont",
                     "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont"):
            try:
                f = tkfont.nametofont(name)
                self._base_font_sizes[name] = abs(int(f.cget("size"))) or 9
            except Exception:
                pass
        try:
            self._user_scale = float(load_settings().get("ui_scale", 1.0))
        except Exception:
            self._user_scale = 1.0
        self._user_scale = min(1.8, max(0.7, self._user_scale))
        self._apply_ui_scale(persist=False)

    def _dpi_factor(self):
        """1.0 at 96dpi, 1.25 at 120dpi, 1.5 at 144dpi (Windows 150%)."""
        try:
            return max(1.0, float(self.winfo_fpixels("1i")) / 96.0)
        except Exception:
            return 1.0

    def _apply_ui_scale(self, persist=True):
        import tkinter.font as tkfont
        factor = self._dpi_factor() * self._user_scale
        try:
            self.tk.call("tk", "scaling", factor * 1.333)
        except Exception:
            pass
        for name, base in self._base_font_sizes.items():
            try:
                tkfont.nametofont(name).configure(
                    size=max(7, int(round(base * factor))))
            except Exception:
                pass
        if persist:
            try:
                s = load_settings()
                s["ui_scale"] = round(self._user_scale, 2)
                save_settings(s)
            except Exception:
                pass
        lbl = getattr(self, "_scale_lbl", None)
        if lbl is not None:
            try:
                lbl.config(text=f"{int(self._user_scale * 100)}%")
            except Exception:
                pass

    def _scale_step(self, delta):
        self._user_scale = min(1.8, max(0.7, round(self._user_scale + delta, 2)))
        self._apply_ui_scale()

    def _add_scale_controls(self, parent):
        """A− / A+ text-size controls, shown on both post-login headers."""
        sf = ttk.Frame(parent)
        sf.pack(side="right", padx=8)
        ttk.Button(sf, text="A−", width=3,
                   command=lambda: self._scale_step(-0.1)).pack(side="left")
        self._scale_lbl = ttk.Label(sf, width=5, anchor="center",
                                    text=f"{int(self._user_scale * 100)}%")
        self._scale_lbl.pack(side="left")
        ttk.Button(sf, text="A+", width=3,
                   command=lambda: self._scale_step(0.1)).pack(side="left")
        return sf

    def _build_sms_front(self):
        """Default front door: SMS/Messaging login only — NO FleetX token.
        A small link switches to FleetX Tools for those who need the platform."""
        for w in self.winfo_children():
            w.destroy()
        # top bar: version only. The Update button appears AFTER login
        # (on the signed-in bar, or the FleetX header).
        topbar = ttk.Frame(self, padding=(10, 6))
        topbar.pack(fill="x")
        ttk.Label(topbar, text=f"FleetX SMS / Messaging   |   v{APP_VERSION}",
                  foreground="gray").pack(side="left")

        self._sms_front = ttk.Frame(self, padding=30)
        self._sms_front.pack(fill="both", expand=True)
        # host the SMS auth gate directly at top level
        content_host = self._build_sms_gate(self._sms_front)
        self._sms_standalone = True   # signals: no FleetX context
        # secondary access to FleetX Tools
        link = ttk.Frame(self._sms_front); link.pack(side="bottom", pady=8)
        ttk.Button(link, text="FleetX Tools →", command=self._switch_to_fleetx).pack()

    def _add_update_button(self, parent):
        """Show the ⬆ Update button if a newer version is published. Used by
        both the FleetX header and the standalone SMS front door.

        NOTE: the version metadata lives in the access Gist and is only cached
        by load_access(), which previously ran on the FleetX login path only —
        so SMS-only users never had the data and never saw the button. We now
        fetch it here (off-thread) when it's missing.
        """
        from ..access_control import get_remote_meta
        if not get_remote_meta():
            # No metadata yet (standalone SMS user). Fetch it, then add the
            # button. This is scheduled via after() so it starts only once the
            # Tk mainloop is running — calling after() from a worker before the
            # loop exists raises "main thread is not in main loop" and the
            # button would silently never appear.
            def start_fetch():
                def worker():
                    try:
                        snap = load_access()
                        access_control.set_snapshot(snap)
                        upd = check_update()
                    except Exception:
                        return
                    if not upd:
                        return

                    def finish():
                        try:
                            if parent.winfo_exists():
                                ttk.Button(parent, text=f"⬆ Update to v{upd[0]}",
                                           command=lambda u=upd: self._do_self_update(*u)
                                           ).pack(side="left", padx=10)
                        except Exception:
                            pass
                    try:
                        self.after(0, finish)
                    except RuntimeError:
                        pass       # window closing
                threading.Thread(target=worker, daemon=True).start()
            self.after(150, start_fetch)
            return None
        try:
            upd = check_update()
        except Exception:
            upd = None
        if upd:
            ttk.Button(parent, text=f"⬆ Update to v{upd[0]}",
                       command=lambda u=upd: self._do_self_update(*u)).pack(side="left", padx=10)
        return upd

    def _switch_to_fleetx(self):
        for w in self.winfo_children():
            w.destroy()
        self.title("FleetX Toolkit v2")
        self._sms_standalone = False
        self._build_login()

    def _build_login(self):
        # add a way back to SMS/Messaging from the FleetX login
        self._fleetx_login_root = True
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

        ttk.Button(self.login_frame, text="Sign in with Google",
                   command=self.do_google_login).grid(
            row=5, column=0, columnspan=2, pady=(0, 6))

        ttk.Separator(self.login_frame, orient="horizontal").grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=10)
        ttk.Label(self.login_frame, text="Or paste Bearer token directly:").grid(
            row=7, column=0, columnspan=2)
        # v3.13: pre-fill the last Bearer token saved for this email so the
        # user can sign straight back in with "Use Token".
        self.token_var = tk.StringVar(value=load_bearer_token(saved_email) if saved_email else "")
        ttk.Entry(self.login_frame, textvariable=self.token_var, width=48).grid(
            row=8, column=0, columnspan=2, pady=4)
        ttk.Button(self.login_frame, text="Use Token", command=self.use_manual_token).grid(
            row=9, column=0, columnspan=2, pady=6)
        self.login_status = ttk.Label(self.login_frame, text="", foreground="red")
        self.login_status.grid(row=10, column=0, columnspan=2, pady=6)

        # Always offer a way back — nobody should get stranded on a login they
        # have no credentials for.
        ttk.Separator(self.login_frame, orient="horizontal").grid(
            row=11, column=0, columnspan=2, sticky="ew", pady=(12, 8))
        ttk.Button(self.login_frame, text="← SMS / Messaging",
                   command=self._build_sms_front).grid(row=12, column=0, columnspan=2)

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
            resp = session.post(LOGIN_URL, files=fields, headers=login_headers,
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
                        save_bearer_token(email, token)   # v3.13: remember token too
                    else:
                        clear_credentials()
                        clear_bearer_token(email)
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
    def do_google_login(self):
        # Capture the Google ID token via a local One-Tap page, exchange it at
        # FleetX for a Bearer token, then reuse the normal login finish path.
        if getattr(self, "_logging_in", False):
            return
        self._logging_in = True
        self.login_status.config(
            text="Opening Google sign-in in your browser…", foreground="black")
        threading.Thread(target=self._google_worker, daemon=True).start()

    def _google_worker(self):
        from .. import google_auth as ga
        id_token, err = ga.capture_google_token()
        if err or not id_token:
            self._logging_in = False
            self._login_status_safe(err or "Google sign-in was cancelled.")
            return
        claims = ga.decode_id_token(id_token)
        ok, reason = ga.validate_claims(claims)
        if not ok:
            self._logging_in = False
            self._login_status_safe(reason)
            return
        email = ga.google_email(claims)
        self._login_status_safe("Exchanging sign-in with FleetX…", "black")
        bearer, xerr = ga.exchange_google_token(id_token)
        if not bearer:
            self._logging_in = False
            self._login_status_safe(
                (xerr or "FleetX login failed.") + " You can paste a token manually below.")
            return
        snapshot = load_access()

        def finish():
            self._logging_in = False
            access_control.set_snapshot(snapshot)
            if not is_authorized(email):
                self.login_status.config(
                    text="Access denied: this email is not authorized for this tool.\n"
                         "Contact saket.verma@fleetx.io for access.",
                    foreground="red")
                return
            self.token = bearer
            state.user_email = email
            self.is_admin_user = is_admin(email)
            self._enter_main()
        self.after(0, finish)

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
        # v3.13: remember email + token locally for next launch
        save_bearer_token(email, tok)
        try:
            s = load_settings(); s["last_email"] = email; save_settings(s)
        except Exception:
            pass
        self._enter_main()
    def _scrollable_tab(self, text, padding=8):
        """Create a notebook tab whose content scrolls vertically when it's
        taller than the visible area. Returns the inner frame to build into.
        Adjusts to window size; scrollbar appears only when needed."""
        outer = ttk.Frame(self.nb)
        self.nb.add(outer, text=text)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        inner = ttk.Frame(canvas, padding=padding)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_inner)
        # keep inner width matched to the canvas so content fills horizontally
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        # mouse-wheel only while pointer is over this canvas
        def _wheel(ev):
            canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        return inner

    def _enter_main(self):
        self.login_frame.destroy()
        top = ttk.Frame(self, padding=(10, 6))
        top.pack(fill="x")
        who = state.user_email or "manual token"
        role = "ADMIN" if self.is_admin_user else "USER"
        ttk.Label(top, text=f"Logged in: {who}  [{role}]   |   v{APP_VERSION}   |   "
                            f"Token: {self.token[:8]}...",
                  foreground="green").pack(side="left")
        self._add_update_button(top)
        ttk.Button(top, text="Logout", command=self._logout).pack(side="right")
        self._add_scale_controls(top)

        # Runtime settings (persisted)
        s = load_settings()
        self.delay_var = tk.StringVar(value=str(s.get("delay_ms", DELAY_MS)))
        self.dry_run_var = tk.BooleanVar(value=False)   # always starts OFF for safety
        self.auto_retry_var = tk.BooleanVar(value=bool(s.get("auto_retry", True)))

        # Notebook and Live Log share a draggable vertical split, so you can
        # resize how much room each gets (drag the divider). Both expand to fill.
        self._main_paned = ttk.PanedWindow(self, orient="vertical")
        self._main_paned.pack(fill="both", expand=True, padx=8, pady=4)

        nb_holder = ttk.Frame(self._main_paned)
        self.nb = ttk.Notebook(nb_holder)
        self.nb.pack(fill="both", expand=True)
        self._main_paned.add(nb_holder, weight=4)

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

        # SMS Command + Messaging live behind their OWN email+password gate
        # (independent of the FleetX Bearer token), if the user is allowed the
        # "Messaging" controllable tab.
        # SMS Command + Messaging have their OWN email+password login, so they
        # are always reachable from FleetX Tools — access is decided by that
        # login, not by the FleetX access list.
        self._tab_sms_messaging()

        # Settings tab — available to everyone
        self._tab_settings()

        # Admin-only access-control panel (always last, admins only)
        if self.is_admin_user:
            self._tab_user_access()

        if self.nb.index("end") == 0:
            ph = ttk.Frame(self.nb, padding=20)
            self.nb.add(ph, text="No Access")
            ttk.Label(ph, text="You don't have access to any tools yet.\n\n"
                              "Ask the admin (saket.verma@fleetx.io) to grant you access.",
                      font=("Segoe UI", 11)).pack(pady=40)

        logf = ttk.LabelFrame(self._main_paned,
                              text="Live Log (drag divider above to resize)", padding=4)
        self.log_box = scrolledtext.ScrolledText(logf, height=8, state="disabled",
                                                  font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True)
        self.log_box.tag_config("ok", foreground="green")
        self.log_box.tag_config("err", foreground="red")
        self.log_box.tag_config("info", foreground="blue")
        self._log_pane = logf
        self._main_paned.add(logf, weight=1)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="STOP current run", command=self._stop).pack(side="right")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ── SIM device status (battery / online) shared by both SMS tabs ──

    def _sim_refresh_status(self, force=False):
        """Kick off a cached, background refresh of SemySMS device status.
        Never blocks: the dropdown keeps working with the last known values,
        and the labels update when the fetch returns."""
        from .. import device_status as DS
        token = self._current_sms_token()
        if not token:
            return
        DS.device_status.refresh_async(
            token, on_done=lambda data: self.after(0, self._sim_apply_status),
            force=force)
        # paint whatever we already have, immediately
        self._sim_apply_status()

    def _sim_apply_status(self):
        """Update both dropdowns' item labels and the red offline marker.
        Cheap and synchronous — pure string work on at most 6 items."""
        from .. import device_status as DS
        from ..config import (MESSAGING_SIMS, MESSAGING_SIM_NAMES,
                              SEMYSMS_SIMS, SEMYSMS_SIM_NAMES, sim_id_for_name)

        def decorate(names, id_map):
            out = []
            for nm in names:
                did = None
                for k, v in id_map.items():
                    if v == nm:
                        did = k
                        break
                out.append(DS.format_sim_label(nm, DS.device_status.get(did)))
            return out

        # Messaging tab
        cb = getattr(self, "_msg_sim_cb", None)
        if cb is not None:
            try:
                if cb.winfo_exists():
                    current = DS.label_to_name(self.msg_sim.get())
                    cb.config(values=decorate(MESSAGING_SIM_NAMES, MESSAGING_SIMS))
                    st = DS.device_status.get(sim_id_for_name(current))
                    self.msg_sim.set(DS.format_sim_label(current, st))
                    lbl = getattr(self, "msg_sim_state", None)
                    if lbl is not None and lbl.winfo_exists():
                        if DS.is_offline(st):
                            lbl.config(text="  ● OFFLINE", fg="#c62828")
                        else:
                            lbl.config(text="")
            except Exception:
                pass

        # SMS Command tab
        cb2 = getattr(self, "_sms_sim_cb", None)
        if cb2 is not None:
            try:
                if cb2.winfo_exists():
                    current = DS.label_to_name(self.sms_sim.get())
                    cb2.config(values=decorate(SEMYSMS_SIM_NAMES, SEMYSMS_SIMS))
                    st = DS.device_status.get(sim_id_for_name(current))
                    self.sms_sim.set(DS.format_sim_label(current, st))
                    lbl2 = getattr(self, "sms_sim_state", None)
                    if lbl2 is not None and lbl2.winfo_exists():
                        if DS.is_offline(st):
                            lbl2.config(text="  ● OFFLINE", foreground="#c62828")
                        else:
                            lbl2.config(text="")
            except Exception:
                pass

    def _current_sms_token(self):
        """SemySMS token — stored LOCALLY per user in Windows Credential Manager
        (DPAPI). Never comes from the Gist. Entered once after login."""
        from ..storage import load_sms_token
        return load_sms_token()

    def _tab_sms_messaging(self):
        """A single tab hosting the SMS-auth gate; on login it reveals an inner
        notebook with SMS Command + Messaging (+ admin user management)."""
        page = ttk.Frame(self.nb)
        self.nb.add(page, text="SMS / Messaging")
        self._build_sms_gate(page)
        # If they're already FleetX-authenticated and on the SMS user list,
        # sign them in automatically (no second password).
        self._sms_try_auto_auth()

    def _sms_build_feature_tabs(self, inner_nb):
        """Build the SMS Command + Messaging tabs into the inner notebook.
        The existing builders target self.nb, so temporarily point it here.
        Works in standalone mode (no FleetX notebook) too."""
        real_nb = getattr(self, "nb", None)
        self.nb = inner_nb
        try:
            self._tab_sms()
            self._tab_messaging()
        finally:
            if real_nb is not None:
                self.nb = real_nb
            else:
                # standalone: keep inner_nb as self.nb so tab logic (log/render)
                # that references self.nb still resolves
                self.nb = inner_nb

    def _on_tab_changed(self, _event=None):
        if not hasattr(self, "_main_paned") or not hasattr(self, "_log_pane"):
            return   # standalone SMS mode: no Live Log to toggle
        try:
            current = self.nb.tab(self.nb.select(), "text")
        except Exception:
            return
        panes = self._main_paned.panes()
        log_id = str(self._log_pane)
        if current in ("SMS / Messaging", "Messaging"):
            if log_id in panes:
                self._main_paned.forget(self._log_pane)
        else:
            if log_id not in panes:
                self._main_paned.add(self._log_pane, weight=1)
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
            def _swap():
                res = apply_update_and_restart(new_path)
                # Only returns when the swap was refused (file missing/quarantined)
                if isinstance(res, tuple) and not res[0]:
                    messagebox.showerror("Update", res[1])
            self.after(500, _swap)
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
        box = self._active_log_box()
        if box is None:
            return
        box.config(state="normal")
        box.insert("end", msg + "\n", tag)
        # PERF: cap the log so long sessions don't degrade the Text widget.
        self._trim_log(box)
        box.see("end")
        box.config(state="disabled")

    def _active_log_box(self):
        """Which Live Log should receive output right now.

        FIX (v3.12): SMS Command runs go through the shared _loop(), which logs
        via self.log(). That used to land in the MAIN log — so SMS output was
        invisible on the SMS tab and appeared under Tickets etc. instead. When
        the SMS Command tab is the active one, send output to its own log.
        """
        sms_box = getattr(self, "_sms_log_box", None)
        if sms_box is not None:
            try:
                nb = getattr(self, "_sms_inner_nb", None)
                if nb is not None and nb.winfo_exists() \
                        and nb.tab(nb.select(), "text") == "SMS Command":
                    # In FleetX mode the SMS area is itself a tab — only claim
                    # the output when that tab is the one on screen, otherwise
                    # Tickets/Device Add output would land in the SMS log.
                    main_nb = getattr(self, "nb", None)
                    if main_nb is None or main_nb is nb:
                        return sms_box            # standalone SMS mode
                    try:
                        if main_nb.tab(main_nb.select(), "text") == "SMS / Messaging":
                            return sms_box
                    except Exception:
                        return sms_box
            except Exception:
                pass
        box = getattr(self, "log_box", None)
        # standalone SMS mode has no main log at all
        return box if box is not None else sms_box

    LOG_MAX_LINES = 2000

    @staticmethod
    def _trim_log_static(box, max_lines=2000):
        """Drop the oldest lines once the log exceeds max_lines."""
        try:
            total = int(box.index("end-1c").split(".")[0])
        except Exception:
            return
        if total > max_lines:
            box.delete("1.0", f"{total - max_lines + 1}.0")

    def _trim_log(self, box):
        self._trim_log_static(box, self.LOG_MAX_LINES)
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
        # A tab can set self._delay_override (seconds) to use its own gap for
        # one run instead of the shared Settings delay; cleared when None.
        ov = getattr(self, "_delay_override", None)
        if ov is not None:
            try:
                return max(0.0, float(ov))
            except Exception:
                pass
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
        tab = self._scrollable_tab("⚙ Settings", padding=12)

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
    def _download_sample(self, kind, default_name):
        """Save a formatted sample .xlsx for the given tab kind."""
        from ..sms import write_sample
        path = filedialog.asksaveasfilename(
            title="Save sample Excel", defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            write_sample(kind, path)
            self.log(f"  ✓ Sample template saved → {path}", "info")
            messagebox.showinfo("Sample saved",
                f"Template saved to:\n{path}\n\nFill it in and select it as your Excel input.")
        except Exception as e:
            self._ui_error("Sample", f"Could not write sample: {e}")

    def _open_logs(self):
        os.makedirs(LOGS_DIR, exist_ok=True)
        try:
            os.startfile(LOGS_DIR)            # Windows
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", LOGS_DIR])
