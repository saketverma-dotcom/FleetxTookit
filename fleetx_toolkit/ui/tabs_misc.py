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
from ..logic import split_tickets_by_counts, split_tickets_equal


class MiscTabsMixin:
    """Tabs 7-9: SensorType, Assets, Tickets."""

    def _tab_sensor_type(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="SensorType")
        f = ttk.Frame(tab); f.pack(fill="x")
        ttk.Label(f, text="Sensor Type:").pack(side="left")
        self.sensor_var = tk.StringVar(value=SENSOR_PRESETS[0])
        self.sensor_combo = ttk.Combobox(f, textvariable=self.sensor_var, width=38,
                                         values=self._sensor_values())
        self.sensor_combo.pack(side="left", padx=6)
        ttk.Button(f, text="+ Save to shared list",
                   command=self._save_sensor_type).pack(side="left", padx=4)
        ttk.Label(tab, text="(editable — type a custom sensorType, then '+ Save' to share it with the team)"
                            "  |  Input = Vehicle IDs",
                  foreground="gray").pack(anchor="w", pady=(2, 0))
        self.sens_src = self._input_source(tab, "Vehicle IDs")
        ttk.Button(tab, text="▶ Update SensorType",
                   command=lambda: self._run_thread(self._run_sensor)).pack(anchor="w", pady=4)
    def _sensor_values(self):
        """Built-in presets + team-shared custom types (deduped, order kept)."""
        vals = list(SENSOR_PRESETS)
        for t in access_control.get_shared_sensor_types():
            if t not in vals:
                vals.append(t)
        return vals

    def _save_sensor_type(self):
        stype = self.sensor_var.get().strip()
        if not stype:
            self._ui_error("SensorType", "Type a sensor type first.")
            return
        if stype in self._sensor_values():
            self._ui_error("SensorType", "That sensor type is already in the list.")
            return
        tok = load_gh_token()
        if not tok:
            self._ui_error("SensorType",
                "Saving to the shared list needs a GitHub token.\n"
                "Ask an admin to paste one in the User Access tab (admins only).")
            return
        def worker():
            ok, msg = access_control.push_sensor_type(stype, tok)
            def finish():
                self.log(("  ✓ " if ok else "  ✗ ") + msg, "info" if ok else "err")
                if ok:
                    self.sensor_combo.config(values=self._sensor_values())
            self.after(0, finish)
        threading.Thread(target=worker, daemon=True).start()

    def _run_sensor(self):
        vids = self._get_ids(self.sens_src)
        if not vids: return
        stype = self.sensor_var.get().strip()

        def fn(vid):
            r = requests.patch(f"{API_BASE}/api/v1/vehicles/{vid}",
                               json={"id": int(vid), "sensorType": stype},
                               headers=api_headers(self.token, content_type="application/json"),
                               timeout=30)
            return (vid, stype), r
        self._loop(vids, "SensorType Update", fn, ["Vehicle ID", "Sensor Type"])
    def _tab_assets(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Assets")

        f1 = ttk.LabelFrame(tab, text="Add Fuel-Sensor Assets (e.g. ESCORT_TDBLE)", padding=6)
        f1.pack(fill="x", pady=2)
        r1 = ttk.Frame(f1); r1.pack(fill="x")
        ttk.Label(r1, text="Model:").pack(side="left")
        self.asset_model = tk.StringVar(value="ESCORT_TDBLE")
        ttk.Entry(r1, textvariable=self.asset_model, width=18).pack(side="left", padx=4)
        ttk.Label(r1, text="Type:").pack(side="left", padx=(10, 0))
        self.asset_type = tk.StringVar(value="FUEL_SENSOR")
        ttk.Entry(r1, textvariable=self.asset_type, width=16).pack(side="left", padx=4)
        ttk.Label(r1, text="issuedToUserId:").pack(side="left", padx=(10, 0))
        self.asset_user = tk.StringVar(value="14203")
        ttk.Entry(r1, textvariable=self.asset_user, width=10).pack(side="left", padx=4)

        self.asset_src = self._input_source(f1, "Asset IDs (= name = productId)")
        ttk.Button(f1, text="▶ Add Assets",
                   command=lambda: self._run_thread(self._run_asset_add)).pack(anchor="w", pady=2)

        f2 = ttk.LabelFrame(tab, text="Update Asset Supplier (PATCH /assets/update)", padding=6)
        f2.pack(fill="x", pady=6)
        r2 = ttk.Frame(f2); r2.pack(fill="x")
        ttk.Label(r2, text="New Supplier:").pack(side="left")
        self.asset_supplier = tk.StringVar(value="HHD")
        ttk.Entry(r2, textvariable=self.asset_supplier, width=14).pack(side="left", padx=4)
        ttk.Label(r2, text="Paste Asset IDs in box above (reuses same input)  →").pack(side="left", padx=8)
        ttk.Button(r2, text="▶ Update Supplier",
                   command=lambda: self._run_thread(self._run_asset_update)).pack(side="left", padx=6)
    def _run_asset_add(self):
        ids = self._get_ids(self.asset_src)
        if not ids: return

        def fn(aid):
            payload = {"assetId": int(aid), "name": int(aid), "supplier": "CLIENT",
                       "model": self.asset_model.get().strip(),
                       "type": self.asset_type.get().strip(),
                       "productId": int(aid), "status": "ACTIVE",
                       "issuedToUserId": int(self.asset_user.get() or 0)}
            r = requests.post(f"{API_BASE}/api/v1/assets", json=payload,
                              headers=api_headers(self.token, content_type="application/json"),
                              timeout=30)
            return (aid, self.asset_model.get()), r
        self._loop(ids, "Asset Add", fn, ["Asset ID", "Model"])
    def _run_asset_update(self):
        ids = self._get_ids(self.asset_src)
        if not ids: return

        def fn(aid):
            r = requests.patch(f"{API_BASE}/api/v1/assets/update",
                               json={"assetId": str(aid),
                                     "supplier": self.asset_supplier.get().strip()},
                               headers=api_headers(self.token, content_type="application/json"),
                               timeout=30)
            return (aid, self.asset_supplier.get()), r
        self._loop(ids, "Asset Supplier Update", fn, ["Asset ID", "Supplier"])
    def _tab_tickets(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Tickets")

        ttk.Label(tab, text="Tick who to assign, set each one's count "
                            "(blank or 'rest' = split remaining). Use ▲▼ to set the order "
                            "tickets are handed out.",
                  foreground="gray").pack(anchor="w")

        # Working order + per-assignee state (persists across re-renders)
        self.assignee_order = list(ASSIGNEE_DIRECTORY.keys())
        self.assignee_pick = {}
        for name, aid in ASSIGNEE_DIRECTORY.items():
            self.assignee_pick[name] = {"on": tk.BooleanVar(value=False),
                                        "count": tk.StringVar(value=""),
                                        "id": aid}

        self.pick_frame = ttk.LabelFrame(tab, text="Assignees (top = assigned first)", padding=6)
        self.pick_frame.pack(fill="x", pady=4)
        self._render_assignee_grid()

        hint = ttk.Frame(tab); hint.pack(fill="x")
        ttk.Label(hint, text="Split mode:", foreground="gray").pack(side="left")
        self.tick_split = tk.StringVar(value="counts")
        ttk.Radiobutton(hint, text="Use the counts above", variable=self.tick_split,
                        value="counts").pack(side="left", padx=4)
        ttk.Radiobutton(hint, text="Ignore counts — split equally among ticked",
                        variable=self.tick_split, value="equal").pack(side="left", padx=4)

        self.tick_src = self._input_source(tab, "Ticket IDs")
        ttk.Button(tab, text="▶ Assign Tickets",
                   command=lambda: self._run_thread(self._run_tickets)).pack(anchor="w", pady=4)
    def _render_assignee_grid(self):
        for w in self.pick_frame.winfo_children():
            w.destroy()

        ttk.Label(self.pick_frame, text="Order", font=("Segoe UI", 9, "bold"),
                  width=6).grid(row=0, column=0)
        ttk.Label(self.pick_frame, text="", width=3).grid(row=0, column=1)
        ttk.Label(self.pick_frame, text="Name", font=("Segoe UI", 9, "bold"),
                  width=22, anchor="w").grid(row=0, column=2, sticky="w")
        ttk.Label(self.pick_frame, text="Assignee ID", font=("Segoe UI", 9, "bold"),
                  width=12).grid(row=0, column=3)
        ttk.Label(self.pick_frame, text="Count", font=("Segoe UI", 9, "bold"),
                  width=8).grid(row=0, column=4)

        for ri, name in enumerate(self.assignee_order, start=1):
            d = self.assignee_pick[name]
            # ▲▼ buttons
            btnf = ttk.Frame(self.pick_frame)
            btnf.grid(row=ri, column=0)
            up = ttk.Button(btnf, text="▲", width=2,
                            command=lambda n=name: self._move_assignee(n, -1))
            up.pack(side="left")
            dn = ttk.Button(btnf, text="▼", width=2,
                            command=lambda n=name: self._move_assignee(n, +1))
            dn.pack(side="left")
            if ri == 1:
                up.state(["disabled"])
            if ri == len(self.assignee_order):
                dn.state(["disabled"])

            ttk.Checkbutton(self.pick_frame, variable=d["on"]).grid(row=ri, column=1)
            ttk.Label(self.pick_frame, text=name, anchor="w", width=22).grid(
                row=ri, column=2, sticky="w")
            ttk.Label(self.pick_frame, text=str(d["id"]), width=12).grid(row=ri, column=3)
            ttk.Entry(self.pick_frame, textvariable=d["count"], width=8).grid(
                row=ri, column=4, padx=2)
    def _move_assignee(self, name, delta):
        i = self.assignee_order.index(name)
        j = i + delta
        if 0 <= j < len(self.assignee_order):
            self.assignee_order[i], self.assignee_order[j] = \
                self.assignee_order[j], self.assignee_order[i]
            self._render_assignee_grid()
    def _run_tickets(self):
        tickets = self._get_ids(self.tick_src)
        if not tickets:
            return

        # Which assignees are ticked? Respect the on-screen ORDER (▲▼).
        chosen = [(name, self.assignee_pick[name]) for name in self.assignee_order
                  if self.assignee_pick[name]["on"].get()]
        if not chosen:
            self.log("Tick at least one assignee.", "err")
            self._ui_error("Tickets", "Tick at least one assignee.")
            return

        if self.tick_split.get() == "equal":
            # Even round-robin split across ticked assignees
            ids = [d["id"] for _, d in chosen]
            assignments = split_tickets_equal(tickets, ids)
        else:
            # Explicit counts; blank / 'rest' = remainder (logic.py, unit-tested)
            pairs = [(d["id"], d["count"].get()) for _, d in chosen]
            assignments, unassigned, invalid = split_tickets_by_counts(tickets, pairs)
            if invalid is not None:
                name = chosen[invalid[0]][0]
                self.log(f"  Invalid count for {name}: '{invalid[1]}'", "err")
                self._ui_error("Tickets", f"Invalid count for {name}: '{invalid[1]}'")
                return
            if unassigned:
                self.log(f"  ⚠ {unassigned} tickets unassigned (counts < total, "
                         "and nobody set to 'rest').", "err")

        # Preview by name
        id_to_name = {d["id"]: name for name, d in self.assignee_pick.items()}
        preview = {}
        for _, aid in assignments:
            preview[aid] = preview.get(aid, 0) + 1
        preview_txt = "\n".join(f"{id_to_name.get(aid, aid)} ({aid}) → {cnt}"
                                 for aid, cnt in preview.items())
        if not self._ui_askyesno("Confirm",
                f"Assign {len(assignments)} tickets?\n\n{preview_txt}"):
            return

        def fn(pair):
            tid, aid = pair
            r = requests.put(f"{API_BASE}/api/v1/internal/issue/change-assignment",
                             json={"id": int(tid), "assignedToId": int(aid)},
                             headers=api_headers(self.token), timeout=30)
            return (tid, id_to_name.get(aid, aid)), r
        self._loop(assignments, "Ticket Assignment", fn, ["Ticket ID", "Assignee"])
