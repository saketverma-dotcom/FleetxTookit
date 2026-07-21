import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

import requests

from ..config import (SEMYSMS_API, SEMYSMS_SIM_NAMES, sim_id_for_name)
from ..io_utils import load_excel_records
from ..sms import build_sms_params, normalize_number, sms_success
from ..storage import load_sms_token, save_sms_token


class SmsTabMixin:
    """SMS Command tab — bulk SMS via SemySMS, per-row SIM selection by name."""

    def _tab_sms(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="SMS Command")

        # ── Token row (stored in Windows Credential Manager) ──
        tf = ttk.LabelFrame(tab, text="SemySMS token (stored securely on this PC)", padding=6)
        tf.pack(fill="x")
        self.sms_token = tk.StringVar(value=load_sms_token())
        ttk.Entry(tf, textvariable=self.sms_token, width=40, show="•").pack(side="left", padx=4)
        ttk.Button(tf, text="Save token", command=self._save_sms_token).pack(side="left", padx=4)
        self.sms_token_status = ttk.Label(tf, text="", foreground="green")
        self.sms_token_status.pack(side="left", padx=6)

        # ── SIM selector (tab default) ──
        sf = ttk.Frame(tab); sf.pack(fill="x", pady=(8, 2))
        ttk.Label(sf, text="SIM to send from:").pack(side="left")
        self.sms_sim = tk.StringVar(value=SEMYSMS_SIM_NAMES[0])
        ttk.Combobox(sf, textvariable=self.sms_sim, width=18, state="readonly",
                     values=SEMYSMS_SIM_NAMES).pack(side="left", padx=6)
        ttk.Label(sf, text="(used for pasted numbers, and for Excel rows with a blank SIM Name)",
                  foreground="gray").pack(side="left")

        # ── Mode selector ──
        self.sms_mode = tk.StringVar(value="paste")
        mf = ttk.Frame(tab); mf.pack(fill="x", pady=(8, 2))
        ttk.Radiobutton(mf, text="Same message → many numbers", value="paste",
                        variable=self.sms_mode, command=self._sms_mode_switch).pack(side="left")
        ttk.Radiobutton(mf, text="Per-row from Excel (Mobile | Message | SIM Name)", value="excel",
                        variable=self.sms_mode, command=self._sms_mode_switch).pack(side="left", padx=12)

        # ── Paste frame ──
        self.sms_paste_frame = ttk.Frame(tab)
        ttk.Label(self.sms_paste_frame, text="Numbers (one per line):").pack(anchor="w")
        self.sms_numbers = scrolledtext.ScrolledText(self.sms_paste_frame, height=6, width=40)
        self.sms_numbers.pack(fill="x")
        ttk.Label(self.sms_paste_frame, text="Message:").pack(anchor="w", pady=(6, 0))
        self.sms_msg = scrolledtext.ScrolledText(self.sms_paste_frame, height=3, width=40)
        self.sms_msg.pack(fill="x")

        # ── Excel frame ──
        self.sms_excel_frame = ttk.Frame(tab)
        er = ttk.Frame(self.sms_excel_frame); er.pack(fill="x")
        self.sms_path = tk.StringVar()
        ttk.Entry(er, textvariable=self.sms_path, width=52).pack(side="left", padx=4)
        ttk.Button(er, text="Browse...",
                   command=lambda: self.sms_path.set(
                       filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")]))).pack(side="left")
        ttk.Button(er, text="⬇ Sample Excel",
                   command=lambda: self._download_sample("sms", "sms_sample.xlsx")).pack(side="left", padx=4)

        self._sms_mode_switch()

        ttk.Label(tab, text="Country code to prepend (blank = send as-is):",
                  foreground="gray").pack(anchor="w", pady=(8, 0))
        self.sms_cc = tk.StringVar(value="")
        ttk.Entry(tab, textvariable=self.sms_cc, width=8).pack(anchor="w")

        dr = ttk.Frame(tab); dr.pack(anchor="w", pady=(8, 0))
        ttk.Label(dr, text="Delay between SMS (seconds):").pack(side="left")
        self.sms_delay = tk.StringVar(value="5")
        ttk.Entry(dr, textvariable=self.sms_delay, width=6).pack(side="left", padx=6)
        ttk.Label(dr, text="(overrides the global Settings delay for SMS only)",
                  foreground="gray").pack(side="left")

        ttk.Button(tab, text="▶ Send SMS",
                   command=lambda: self._run_thread(self._run_sms)).pack(anchor="w", pady=8)

    def _sms_mode_switch(self):
        if self.sms_mode.get() == "paste":
            self.sms_excel_frame.pack_forget()
            self.sms_paste_frame.pack(fill="x", pady=4)
        else:
            self.sms_paste_frame.pack_forget()
            self.sms_excel_frame.pack(fill="x", pady=4)

    def _save_sms_token(self):
        tok = self.sms_token.get().strip()
        if not tok:
            self.sms_token_status.config(text="Enter a token first.", foreground="red")
            return
        if save_sms_token(tok):
            self.sms_token_status.config(text="Saved securely.", foreground="green")
        else:
            self.sms_token_status.config(
                text="Could not store securely (session only).", foreground="red")

    def _sms_rows(self):
        """Build [(phone, msg, sim_id), ...] from the active mode, or None on error.
        Applies number normalization and per-row SIM name -> id resolution."""
        cc = self.sms_cc.get().strip()
        default_sim = sim_id_for_name(self.sms_sim.get())
        rows = []
        if self.sms_mode.get() == "paste":
            msg = self.sms_msg.get("1.0", "end").strip()
            if not msg:
                self._ui_error("SMS", "Enter a message."); return None
            nums = [n for n in (normalize_number(x, cc)
                                for x in self.sms_numbers.get("1.0", "end").splitlines()) if n]
            if not nums:
                self._ui_error("SMS", "Enter at least one number."); return None
            rows = [(n, msg, default_sim) for n in nums]
        else:
            path = self.sms_path.get().strip()
            if not path:
                self._ui_error("SMS", "Select an Excel file."); return None
            records = self._load_excel_safe(load_excel_records, path)
            if records is None:
                return None
            for rec in records:
                phone = normalize_number(rec.get("mobile", ""), cc)
                msg = str(rec.get("message", "") or "").strip()
                raw_sim = rec.get("sim_name", "")
                sim_name = "" if raw_sim is None else str(raw_sim).strip()
                if sim_name.lower() == "none":
                    sim_name = ""
                sim_id = sim_id_for_name(sim_name) if sim_name else default_sim
                if not phone or not msg:
                    continue
                if not sim_id:
                    self._ui_error("SMS",
                        f"Row for {phone}: unknown SIM Name '{sim_name}'.\n"
                        f"Use one of: {', '.join(SEMYSMS_SIM_NAMES)}")
                    return None
                rows.append((phone, msg, sim_id))
            if not rows:
                self._ui_error("SMS", "No valid rows (need Mobile + Message)."); return None
        return rows

    def _run_sms(self):
        tok = self.sms_token.get().strip() or load_sms_token()
        if not tok:
            self.log("  ✗ No SemySMS token — enter and Save it first.", "err")
            self._ui_error("SMS", "Enter and save the SemySMS token first.")
            return
        rows = self._sms_rows()
        if not rows:
            return

        # Dedicated SMS delay (seconds) overrides the global Settings delay for
        # this run only; restored in finally so other tabs are unaffected.
        try:
            sms_delay = float(self.sms_delay.get().strip() or 5)
            if sms_delay < 0:
                raise ValueError
        except ValueError:
            self.log(f"  ✗ Invalid SMS delay '{self.sms_delay.get()}' — must be seconds ≥ 0.", "err")
            self._ui_error("SMS", "Delay between SMS must be a number of seconds (e.g. 5).")
            return

        def fn(row):
            phone, msg, sim_id = row
            r = requests.post(SEMYSMS_API,
                              data=build_sms_params(tok, sim_id, phone, msg), timeout=30)
            try:
                ok = sms_success(r.json())
            except Exception:
                ok = False
            if r.status_code == 200 and not ok:
                r.status_code = 422   # surface API rejection as a failed row
            return (phone, msg[:40], sim_id), r

        self._delay_override = sms_delay
        try:
            self._loop(rows, "SMS Send", fn, ["Mobile", "Message", "SIM ID"])
        finally:
            self._delay_override = None
