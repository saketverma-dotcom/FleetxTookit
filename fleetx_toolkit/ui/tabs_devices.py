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


class DeviceTabsMixin:
    """Tabs 1-4: Device Add, SIM Inventory, SIM Update, Vehicle-Device Map."""

    def _tab_device_add(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Device Add")

        # -- Full register (Excel) --
        f1 = ttk.LabelFrame(tab, text="Register with SIM / Serial (Excel: id | device_type | sim | serial_number)",
                            padding=6)
        f1.pack(fill="x", pady=4)
        self.reg_path = tk.StringVar()
        ttk.Entry(f1, textvariable=self.reg_path, width=52).grid(row=0, column=0, padx=4)
        ttk.Button(f1, text="Browse...",
                   command=lambda: self.reg_path.set(
                       filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")]))).grid(row=0, column=1)
        ttk.Button(f1, text="▶ Register",
                   command=lambda: self._run_thread(self._run_register)).grid(row=0, column=2, padx=8)
        ttk.Button(f1, text="⬇ Sample Excel",
                   command=lambda: self._download_sample("device_add", "device_add_sample.xlsx")
                   ).grid(row=0, column=3, padx=4)

        # -- Camera 2CH quick add (IMEIs only) --
        f2 = ttk.LabelFrame(tab, text="Camera 2-Channel Quick Add (LCD40AI-2CH — IMEIs only, no SIM)",
                            padding=6)
        f2.pack(fill="both", expand=True, pady=4)
        row = ttk.Frame(f2); row.pack(fill="x")
        ttk.Label(row, text="Device Type:").pack(side="left")
        self.cam_type = tk.StringVar(value="LCD40AI-2CH")
        ttk.Combobox(row, textvariable=self.cam_type, width=22,
                     values=["LCD40AI-2CH", "LCD40", "LCD603", "FMB920",
                             "Cello-CANiQ 2G K-Line"]).pack(side="left", padx=6)
        self.cam_src = self._input_source(f2, "IMEIs")
        ttk.Button(f2, text="▶ Add Devices (id = imei, supplier CLIENT)",
                   command=lambda: self._run_thread(self._run_camera_add)).pack(anchor="w", pady=4)
    def _run_register(self):
        path = self.reg_path.get().strip()
        if not path:
            self.log("Select an Excel file first.", "err"); return
        records = self._load_excel_safe(load_excel_records, path)
        if records is None: return

        def fn(rec):
            dev_id = rec.get("id", "")
            sim = str(rec.get("sim", "")).strip()
            payload = {"id": dev_id, "imei": dev_id,
                       "deviceType": str(rec.get("device_type") or "FMB920").strip(),
                       "deviceSupplier": "CLIENT", "sim": sim, "mobile": sim,
                       "serialNumber": str(rec.get("serial_number", "")).strip()}
            payload = {k: v for k, v in payload.items() if v not in (None, "", "None")}
            r = requests.post(f"{API_BASE}/api/v1/devices/", json=payload,
                              headers=api_headers(self.token), timeout=30)
            return (dev_id, sim), r
        self._loop(records, "Device Register", fn, ["ID", "SIM"])
    def _run_camera_add(self):
        imeis = self._get_ids(self.cam_src)
        if not imeis: return
        dtype = self.cam_type.get().strip()

        def fn(imei):
            payload = {"id": int(imei), "imei": int(imei),
                       "deviceType": dtype, "deviceSupplier": "CLIENT"}
            r = requests.post(f"{API_BASE}/api/v1/devices/", json=payload,
                              headers=api_headers(self.token), timeout=30)
            return (imei, dtype), r
        self._loop(imeis, "Camera Device Add", fn, ["IMEI", "Type"])
    def _tab_sim_inventory(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="SIM Inventory")
        row = ttk.Frame(tab); row.pack(fill="x", pady=2)
        ttk.Label(row, text="Provider/Supplier:").pack(side="left")
        self.sim_provider = tk.StringVar(value="ONOMONDO")
        ttk.Combobox(row, textvariable=self.sim_provider, width=16,
                     values=SIM_PROVIDERS).pack(side="left", padx=6)
        ttk.Label(row, text="Status:").pack(side="left", padx=(12, 0))
        self.sim_status = tk.StringVar(value="ACTIVE")
        ttk.Combobox(row, textvariable=self.sim_status, width=10,
                     values=["ACTIVE", "INACTIVE"]).pack(side="left", padx=6)
        ttk.Label(row, text="Received Date:").pack(side="left", padx=(12, 0))
        self.sim_date = tk.StringVar(value=f"{datetime.date.today()}")
        ttk.Entry(row, textvariable=self.sim_date, width=12).pack(side="left", padx=6)
        ttk.Label(tab, text="Input = SIM numbers (mobile = SIM if not in Excel 'mobile' column)",
                  foreground="gray").pack(anchor="w")
        self.simi_src = self._input_source(tab, "SIM Numbers")
        ttk.Button(tab, text="▶ Add SIMs to Inventory",
                   command=lambda: self._run_thread(self._run_sim_inventory)).pack(anchor="w", pady=4)
    def _run_sim_inventory(self):
        sims = self._get_ids(self.simi_src)
        if not sims: return

        def fn(sim):
            payload = {"simNumber": sim, "mobileNumber": sim,
                       "simProvider": self.sim_provider.get(),
                       "simStatus": self.sim_status.get(),
                       "simSupplier": self.sim_provider.get(),
                       "reveived_date": self.sim_date.get()}
            r = requests.post(f"{API_BASE}/api/v1/inventory/sim", json=payload,
                              headers=api_headers(self.token), timeout=30)
            return (sim, self.sim_provider.get()), r
        self._loop(sims, "SIM Inventory Add", fn, ["SIM", "Provider"])
    def _tab_sim_update(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="SIM Update")
        ttk.Label(tab, text="Excel columns: device_id | sim | mobile",
                  foreground="gray").pack(anchor="w")
        f = ttk.Frame(tab); f.pack(fill="x", pady=4)
        self.simu_path = tk.StringVar()
        ttk.Entry(f, textvariable=self.simu_path, width=52).pack(side="left", padx=4)
        ttk.Button(f, text="Browse...",
                   command=lambda: self.simu_path.set(
                       filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")]))).pack(side="left")
        ttk.Button(f, text="⬇ Sample Excel",
                   command=lambda: self._download_sample("sim_update", "sim_update_sample.xlsx")
                   ).pack(side="left", padx=4)
        ttk.Button(tab, text="▶ Update SIM Mapping",
                   command=lambda: self._run_thread(self._run_sim_update)).pack(anchor="w", pady=8)
    def _run_sim_update(self):
        path = self.simu_path.get().strip()
        if not path:
            self.log("Select an Excel file first.", "err"); return
        records = self._load_excel_safe(load_excel_records, path)
        if records is None: return

        def fn(rec):
            did = rec.get("device_id", "")
            sim = str(rec.get("sim", "")).strip()
            mob = str(rec.get("mobile", sim)).strip()
            r = requests.put(f"{API_BASE}/api/v1/devices/{did}",
                             json={"sim": sim, "mobile": mob},
                             headers=api_headers(self.token), timeout=30)
            return (did, sim), r
        self._loop(records, "SIM Update", fn, ["Device ID", "SIM"])
    def _tab_vehicle_map(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Vehicle-Device Map")
        ttk.Label(tab, text="Paste pairs:  deviceId,vehicleId  (one pair per line)  —  "
                            "or Excel with columns: device_id | vehicle_id",
                  foreground="gray").pack(anchor="w")
        self.map_src = self._input_source(tab, "Device–Vehicle Pairs")
        mrow = ttk.Frame(tab); mrow.pack(anchor="w", pady=4)
        ttk.Button(mrow, text="▶ Map Devices to Vehicles",
                   command=lambda: self._run_thread(self._run_vehicle_map)).pack(side="left")
        ttk.Button(mrow, text="⬇ Sample Excel",
                   command=lambda: self._download_sample("vehicle_map", "vehicle_map_sample.xlsx")
                   ).pack(side="left", padx=6)
    def _run_vehicle_map(self):
        if self.map_src["mode"].get() == "excel":
            path = self.map_src["path"].get().strip()
            if not path:
                self.log("Select an Excel file.", "err"); return
            records = self._load_excel_safe(load_excel_records, path)
            if records is None: return
            pairs = [(str(r.get("device_id", "")).split(".")[0],
                      str(r.get("vehicle_id", "")).split(".")[0]) for r in records]
        else:
            pairs = parse_pasted_pairs(self.map_src["paste"].get("1.0", "end"))
        pairs = [(d, v) for d, v in pairs if d and v]
        if not pairs:
            self.log("No valid device/vehicle pairs found.", "err"); return

        def fn(pair):
            device_id, vehicle_id = pair
            r = requests.post(f"{API_BASE}/api/v1/vehicles/device",
                              files={"deviceId": (None, device_id),
                                     "vehicleId": (None, vehicle_id)},
                              headers={k: v for k, v in api_headers(self.token).items()
                                       if k != "content-type"},
                              timeout=30)
            return (device_id, vehicle_id), r
        self._loop(pairs, "Vehicle-Device Mapping", fn, ["Device ID", "Vehicle ID"])
