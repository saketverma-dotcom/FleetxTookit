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


class CommandTabsMixin:
    """Tabs 5-6: Send Command (+ library manager), Sequential 2-Phase."""

    def _cmd_display_list(self):
        return [f"{c['name']}  [{c['id'][:8]}…]  ({c['deviceType']}, {c['style']})"
                for c in self.commands]
    def _tab_send_command(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Send Command")

        f = ttk.LabelFrame(tab, text="Command (scroll to pick from library)", padding=6)
        f.pack(fill="x", pady=2)
        self.cmd_pick = ttk.Combobox(f, values=self._cmd_display_list(), width=72, state="readonly")
        self.cmd_pick.grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        if self.commands:
            self.cmd_pick.current(0)
        ttk.Button(f, text="＋ Add / Manage Commands",
                   command=self._open_command_manager).grid(row=0, column=2, padx=8)

        self.cmd_src = self._input_source(tab, "IMEIs")
        ttk.Button(tab, text="▶ Send Command to All",
                   command=lambda: self._run_thread(self._run_send_command)).pack(anchor="w", pady=4)
    def _selected_command(self, combo):
        idx = combo.current()
        if idx < 0 or idx >= len(self.commands):
            return None
        return self.commands[idx]
    def _run_send_command(self):
        cmd = self._selected_command(self.cmd_pick)
        if not cmd:
            self.log("Pick a command from the list.", "err"); return
        imeis = self._get_ids(self.cmd_src)
        if not imeis: return
        warn = "\n⚠ THIS IS A FIRMWARE UPDATE." if "FOTA" in cmd["name"].upper() else ""
        if not self._ui_askyesno("Confirm",
                f"Send '{cmd['name']}' to {len(imeis)} devices?{warn}\n\nThis cannot be undone."):
            return

        def fn(imei):
            r = self._send_cmd_request(imei, cmd)
            return (imei, cmd["name"]), r
        self._loop(imeis, f"Command {cmd['name']}", fn, ["IMEI", "Command"])
    def _send_cmd_request(self, imei, cmd):
        if cmd["style"] == "form":
            data = {"token": TOKEN_PARAM, "mobile": MOBILE_PARAM,
                    "type": "DYNAMIC_COMMAND_SETTING_TRIGGER",
                    "deviceid": imei, "mergedDeviceId": imei, "actualDeviceId": imei,
                    "userEmail": state.user_email or "toolkit@fleetx.io",
                    "commandId": cmd["id"], "commandName": cmd["name"],
                    "deviceType": cmd["deviceType"]}
            return requests.post(f"{APP_BASE}/trigger/sendcommands", data=data,
                                 headers=api_headers(self.token, form=True), timeout=30)
        payload = {"commandId": cmd["id"], "commandName": cmd["name"], "imei": imei,
                   "token": TOKEN_PARAM, "mobile": MOBILE_PARAM,
                   "deviceType": cmd["deviceType"],
                   "userEmail": state.user_email or "toolkit@fleetx.io"}
        return requests.post(f"{APP_BASE}/trigger/sendcommands", json=payload,
                             headers=api_headers(self.token), timeout=30)
    def _open_command_manager(self):
        win = tk.Toplevel(self)
        win.title("Command Library")
        win.geometry("720x520")
        win.grab_set()

        # Existing commands list
        lf = ttk.LabelFrame(win, text="Saved commands", padding=6)
        lf.pack(fill="both", expand=True, padx=8, pady=6)
        lb = tk.Listbox(lf, font=("Consolas", 9))
        lb.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, command=lb.yview)
        sb.pack(side="right", fill="y")
        lb.config(yscrollcommand=sb.set)

        def refresh():
            lb.delete(0, "end")
            for c in self.commands:
                lb.insert("end", f"{c['name']}  |  {c['id']}  |  {c['deviceType']}  |  {c['style']}")
            self.cmd_pick.config(values=self._cmd_display_list())
            self.p1_pick.config(values=self._cmd_display_list())
            self.p2_pick.config(values=self._cmd_display_list())
        refresh()

        def delete_sel():
            sel = lb.curselection()
            if not sel: return
            del self.commands[sel[0]]
            save_commands(self.commands)
            refresh()
        ttk.Button(win, text="Delete selected", command=delete_sel).pack(anchor="w", padx=8)

        # Add manually
        mf = ttk.LabelFrame(win, text="Add by Command ID", padding=6)
        mf.pack(fill="x", padx=8, pady=4)
        name_v, id_v, type_v = tk.StringVar(), tk.StringVar(), tk.StringVar(value="FX10")
        style_v = tk.StringVar(value="json")
        ttk.Label(mf, text="Name:").grid(row=0, column=0, sticky="e")
        ttk.Entry(mf, textvariable=name_v, width=34).grid(row=0, column=1, padx=4)
        ttk.Label(mf, text="Command ID:").grid(row=0, column=2, sticky="e")
        ttk.Entry(mf, textvariable=id_v, width=28).grid(row=0, column=3, padx=4)
        ttk.Label(mf, text="Device Type:").grid(row=1, column=0, sticky="e")
        ttk.Entry(mf, textvariable=type_v, width=12).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Radiobutton(mf, text="JSON", variable=style_v, value="json").grid(row=1, column=2)
        ttk.Radiobutton(mf, text="Form (DYNAMIC)", variable=style_v, value="form").grid(row=1, column=3, sticky="w")

        def add_manual():
            if not name_v.get().strip() or not id_v.get().strip():
                messagebox.showerror("Add", "Name and Command ID required.", parent=win)
                return
            self.commands.append({"name": name_v.get().strip(), "id": id_v.get().strip(),
                                  "deviceType": type_v.get().strip() or "FX10",
                                  "style": style_v.get()})
            save_commands(self.commands)
            name_v.set(""); id_v.set("")
            refresh()
        ttk.Button(mf, text="Add", command=add_manual).grid(row=1, column=4, padx=8)

        # Add from curl
        cf = ttk.LabelFrame(win, text="Add from curl (paste full curl — commandId / commandName auto-detected)",
                            padding=6)
        cf.pack(fill="x", padx=8, pady=4)
        curl_box = scrolledtext.ScrolledText(cf, height=5, font=("Consolas", 8))
        curl_box.pack(fill="x")

        def add_curl():
            parsed = parse_curl_command(curl_box.get("1.0", "end"))
            if not parsed["id"]:
                messagebox.showerror("Parse", "Could not find a commandId in the curl.", parent=win)
                return
            if not parsed["name"]:
                parsed["name"] = f"CMD_{parsed['id'][:8]}"
            self.commands.append(parsed)
            save_commands(self.commands)
            curl_box.delete("1.0", "end")
            refresh()
            messagebox.showinfo("Added", f"Added: {parsed['name']}\nID: {parsed['id']}\n"
                                          f"Type: {parsed['deviceType']}  Style: {parsed['style']}",
                                parent=win)
        ttk.Button(cf, text="Parse & Add", command=add_curl).pack(anchor="w", pady=4)
    def _tab_seq_commands(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Sequential 2-Phase")

        f = ttk.Frame(tab); f.pack(fill="x", pady=2)
        ttk.Label(f, text="Phase 1:").grid(row=0, column=0, sticky="e")
        self.p1_pick = ttk.Combobox(f, values=self._cmd_display_list(), width=64, state="readonly")
        self.p1_pick.grid(row=0, column=1, padx=4, pady=2)
        ttk.Label(f, text="Phase 2:").grid(row=1, column=0, sticky="e")
        self.p2_pick = ttk.Combobox(f, values=self._cmd_display_list(), width=64, state="readonly")
        self.p2_pick.grid(row=1, column=1, padx=4, pady=2)
        for i, c in enumerate(self.commands):
            if c["name"] == "SPEED_ON_FX10_GPRS":  self.p1_pick.current(i)
            if c["name"] == "SPEED_OFF_FX10_GPRS": self.p2_pick.current(i)
        ttk.Label(f, text="Gap (s):").grid(row=2, column=0, sticky="e")
        self.gap_var = tk.StringVar(value="20")
        ttk.Entry(f, textvariable=self.gap_var, width=8).grid(row=2, column=1, sticky="w", padx=4)

        self.seq_src = self._input_source(tab, "IMEIs")
        ttk.Button(tab, text="▶ Run 2-Phase Sequence",
                   command=lambda: self._run_thread(self._run_seq)).pack(anchor="w", pady=4)
    def _run_seq(self):
        c1 = self._selected_command(self.p1_pick)
        c2 = self._selected_command(self.p2_pick)
        if not c1 or not c2:
            self.log("Pick both phase commands.", "err"); return
        imeis = self._get_ids(self.seq_src)
        if not imeis: return
        if not self._ui_askyesno("Confirm",
                f"Phase 1: {c1['name']}\nPhase 2: {c2['name']}\nDevices: {len(imeis)}\n\nProceed?"):
            return
        # FIX-5: non-numeric gap used to raise ValueError and kill the thread
        try:
            gap = int(float(self.gap_var.get().strip() or 20))
            if gap < 0:
                raise ValueError
        except ValueError:
            self.log(f"  ✗ Invalid gap '{self.gap_var.get()}' — must be seconds ≥ 0.", "err")
            self._ui_error("Sequential", "Gap must be a number of seconds (e.g. 20).")
            return
        # APPROVED FIX: both phases now go through _loop — Sequential 2-Phase
        # gets dry-run, 429 + network backoff, 401 halt, Settings delay, and
        # the auto-retry pass, same as every other tab. One combined log file.
        all_results = []
        for phase, cmd in [(1, c1), (2, c2)]:
            def fn(imei, _cmd=cmd, _p=phase):
                r = self._send_cmd_request(imei, _cmd)
                return (imei, f"Phase {_p}", _cmd["name"]), r
            res, halted = self._loop(imeis, f"Phase {phase}: {cmd['name']}", fn,
                                     ["IMEI", "Phase", "Command"], save=False)
            all_results.extend(res)
            if halted or self.stop_flag:
                break
            if phase == 1:
                self.log(f"  Waiting {gap}s before Phase 2...", "info")
                for _ in range(gap):               # STOP-interruptible
                    if self.stop_flag:
                        break
                    time.sleep(1)
        if all_results:
            save_result_log(all_results, ["IMEI", "Phase", "Command"],
                            "Sequential Commands", self.log)
