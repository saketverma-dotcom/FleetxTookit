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


class AdminTabsMixin:
    """Admin-only User Access tab (Gist-backed matrix)."""

    def _tab_user_access(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="User Access (Admin)")

        # In-memory working copy of the rules {email: [tabs]}
        self.access_rules = {}

        ttk.Label(tab, text=f"Admin: {', '.join(sorted(ADMIN_EMAILS))}   |   "
                            "Tick tabs to grant access. Changes save straight to the Gist.",
                  foreground="gray").pack(anchor="w")

        # GitHub token row
        trow = ttk.LabelFrame(tab, text="GitHub token (gist scope) — pasted once, remembered on this PC",
                             padding=6)
        trow.pack(fill="x", pady=4)
        self.gh_token_var = tk.StringVar(value=load_gh_token())
        ttk.Entry(trow, textvariable=self.gh_token_var, width=50, show="*").pack(side="left", padx=4)
        ttk.Button(trow, text="Save token", command=self._save_gh_token).pack(side="left", padx=4)
        ttk.Button(trow, text="⟳ Reload rules from Gist",
                   command=self._reload_access_grid).pack(side="left", padx=4)
        ttk.Button(trow, text="✎ Open Gist in browser",
                   command=self._open_gist_editor).pack(side="left", padx=4)

        # Add-email row
        arow = ttk.Frame(tab); arow.pack(fill="x", pady=4)
        ttk.Label(arow, text="Add email:").pack(side="left")
        self.new_user_var = tk.StringVar()
        e = ttk.Entry(arow, textvariable=self.new_user_var, width=32)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self._add_access_user())
        ttk.Button(arow, text="+ Add user", command=self._add_access_user).pack(side="left", padx=4)
        ttk.Label(arow, text="(new users start with all tabs OFF — tick what they need)",
                  foreground="gray").pack(side="left", padx=6)

        # Scrollable checkbox grid
        gridwrap = ttk.LabelFrame(tab, text="Access matrix", padding=4)
        gridwrap.pack(fill="both", expand=True, pady=4)

        canvas = tk.Canvas(gridwrap, highlightthickness=0)
        vbar = ttk.Scrollbar(gridwrap, orient="vertical", command=canvas.yview)
        hbar = ttk.Scrollbar(gridwrap, orient="horizontal", command=canvas.xview)
        self.grid_frame = ttk.Frame(canvas)
        self.grid_frame.bind("<Configure>",
                             lambda ev: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")

        # Save bar
        sbar = ttk.Frame(tab); sbar.pack(fill="x", pady=4)
        ttk.Button(sbar, text="💾 SAVE ALL CHANGES TO GIST",
                   command=self._save_access_to_gist).pack(side="left")
        self.access_status = ttk.Label(sbar, text="", foreground="green")
        self.access_status.pack(side="left", padx=10)

        self._reload_access_grid()
    def _save_gh_token(self):
        tok = self.gh_token_var.get().strip()
        if not tok:
            self.access_status.config(text="Token is empty.", foreground="red")
            return
        if save_gh_token(tok):
            self.access_status.config(
                text="GitHub token saved in Windows Credential Manager.", foreground="green")
        else:
            self.access_status.config(
                text="Could not store token securely (keyring unavailable) — "
                     "it will be used for this session only.", foreground="red")
    def _open_gist_editor(self):
        import webbrowser
        webbrowser.open(ACCESS_URL.split("/raw/")[0])
    def _reload_access_grid(self):
        rules = fetch_remote_access()
        if rules is None:
            try:
                with open(ACCESS_FILE) as f:
                    rules = json.load(f)
                self.access_status.config(text="Offline — showing last cached rules.", foreground="red")
            except Exception:
                rules = {}
                self.access_status.config(text="Could not load rules (offline, no cache).", foreground="red")
        else:
            self.access_status.config(text=f"Loaded {len(rules)} user(s) from Gist.", foreground="green")
        self.access_rules = {k.strip().lower(): list(v) for k, v in rules.items()}
        self._build_grid()
    def _build_grid(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.grid_checks = {}   # (email, tab) -> BooleanVar

        # Header row
        ttk.Label(self.grid_frame, text="User", font=("Segoe UI", 9, "bold"),
                  width=30, anchor="w").grid(row=0, column=0, padx=2, pady=2, sticky="w")
        for ci, tabname in enumerate(CONTROLLABLE_TABS, start=1):
            ttk.Label(self.grid_frame, text=tabname, font=("Segoe UI", 8, "bold"),
                      wraplength=70, justify="center").grid(row=0, column=ci, padx=2, pady=2)
        ttk.Label(self.grid_frame, text="", width=4).grid(row=0, column=len(CONTROLLABLE_TABS)+1)

        # One row per user
        for ri, email in enumerate(sorted(self.access_rules.keys()), start=1):
            ttk.Label(self.grid_frame, text=email, anchor="w", width=30).grid(
                row=ri, column=0, padx=2, pady=1, sticky="w")
            granted = set(self.access_rules.get(email, []))
            for ci, tabname in enumerate(CONTROLLABLE_TABS, start=1):
                var = tk.BooleanVar(value=(tabname in granted))
                self.grid_checks[(email, tabname)] = var
                ttk.Checkbutton(self.grid_frame, variable=var).grid(row=ri, column=ci, padx=2)
            ttk.Button(self.grid_frame, text="✕", width=3,
                       command=lambda em=email: self._remove_access_user(em)).grid(
                       row=ri, column=len(CONTROLLABLE_TABS)+1, padx=2)

        if not self.access_rules:
            ttk.Label(self.grid_frame, text="No users yet — add an email above.",
                      foreground="gray").grid(row=1, column=0, columnspan=4, pady=8, sticky="w")
    def _collect_grid(self):
        """Read checkboxes back into self.access_rules."""
        result = {}
        for (email, tabname), var in self.grid_checks.items():
            result.setdefault(email, [])
            if var.get():
                result[email].append(tabname)
        # keep users that exist but have zero tabs
        for email in self.access_rules:
            result.setdefault(email, [])
        # preserve canonical tab order
        for email in result:
            result[email] = [t for t in CONTROLLABLE_TABS if t in result[email]]
        return result
    def _add_access_user(self):
        email = self.new_user_var.get().strip().lower()
        if not email:
            return
        if not email.endswith(ALLOWED_DOMAIN):
            messagebox.showerror("User Access", f"Only {ALLOWED_DOMAIN} emails are allowed.")
            return
        if email in ADMIN_EMAILS:
            messagebox.showinfo("User Access", "That email is already an admin (full access).")
            return
        # Merge current checkbox state, then add the new user
        self.access_rules = self._collect_grid()
        if email in self.access_rules:
            messagebox.showinfo("User Access", "That user already exists in the grid.")
            return
        self.access_rules[email] = []
        self.new_user_var.set("")
        self._build_grid()
        self.access_status.config(
            text=f"Added {email}. Tick tabs, then SAVE to push to Gist.", foreground="blue")
    def _remove_access_user(self, email):
        if not messagebox.askyesno("Remove", f"Remove {email} from access?"):
            return
        self.access_rules = self._collect_grid()
        self.access_rules.pop(email, None)
        self._build_grid()
        self.access_status.config(text=f"Removed {email}. SAVE to push to Gist.", foreground="blue")
    def _save_access_to_gist(self):
        gh_token = self.gh_token_var.get().strip() or load_gh_token()
        if not gh_token:
            messagebox.showerror("Save", "Paste a GitHub token (gist scope) and click 'Save token' first.")
            return
        final = self._collect_grid()
        ok, msg = push_access_to_gist(final, gh_token)
        if ok:
            self.access_rules = final
            access_control.set_snapshot(final)
            self.access_status.config(text="✓ " + msg + " Users get it on next login.",
                                      foreground="green")
            self.log(f"  Access saved to Gist: {len(final)} user(s)", "info")
        else:
            self.access_status.config(text="✗ " + msg, foreground="red")
            self.log(f"  Gist save failed: {msg}", "err")
