import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import openpyxl

from ..config import (ASSET_ACCOUNT_URL, ASSET_ATTACH_TYPE, ASSET_ATTACH_URL)
from ..api_client import api_headers
from ..http import session
from ..io_utils import load_excel_records
from .. import logic as L


class AssetAssignTabMixin:
    """Asset → Account assignment and Asset → Vehicle attachment.

    Both support a paste flow (comma-separated per line) and an Excel flow.
    accountId can be given per row; the field below acts as the default when a
    row omits it. One API call per asset id so every row gets its own result.
    """

    def _tab_asset_assign(self):
        tab = self._scrollable_tab("Asset Assign/Attach", padding=8)

        # ── Section 1: assign asset(s) to an account ──
        f1 = ttk.LabelFrame(
            tab, text="1) Assign Asset to Account  (PUT /assets/account)", padding=6)
        f1.pack(fill="x", pady=2)
        r1 = ttk.Frame(f1); r1.pack(fill="x")
        ttk.Label(r1, text="Default accountId:").pack(side="left")
        self.aa_account = tk.StringVar()
        ttk.Entry(r1, textvariable=self.aa_account, width=12).pack(side="left", padx=4)
        ttk.Label(r1, text="(used when a row has no accountId of its own)",
                  foreground="gray").pack(side="left", padx=6)

        ttk.Label(f1, text="Paste:  assetId   or   assetId,accountId",
                  foreground="gray").pack(anchor="w", pady=(4, 0))
        self.aa_src = self._input_source(f1, "Asset IDs (+ optional accountId)")
        b1 = ttk.Frame(f1); b1.pack(fill="x")
        ttk.Button(b1, text="▶ Assign to Account",
                   command=lambda: self._run_thread(self._run_asset_account)
                   ).pack(side="left", pady=2)
        ttk.Button(b1, text="Sample Excel",
                   command=self._aa_sample_account).pack(side="left", padx=8)

        # ── Section 2: attach asset to a vehicle ──
        f2 = ttk.LabelFrame(
            tab, text=f"2) Attach Asset to Vehicle  (POST /assets/attach, "
                      f"type={ASSET_ATTACH_TYPE})", padding=6)
        f2.pack(fill="x", pady=8)
        r2 = ttk.Frame(f2); r2.pack(fill="x")
        ttk.Label(r2, text="Default accountId:").pack(side="left")
        self.at_account = tk.StringVar()
        ttk.Entry(r2, textvariable=self.at_account, width=12).pack(side="left", padx=4)
        ttk.Label(r2, text=f"type is fixed to {ASSET_ATTACH_TYPE}",
                  foreground="gray").pack(side="left", padx=6)

        ttk.Label(f2, text="Paste:  assetId,vehicleId   or   assetId,vehicleId,accountId",
                  foreground="gray").pack(anchor="w", pady=(4, 0))
        self.at_src = self._input_source(f2, "assetId,vehicleId (+ optional accountId)")
        b2 = ttk.Frame(f2); b2.pack(fill="x")
        ttk.Button(b2, text="▶ Attach to Vehicle",
                   command=lambda: self._run_thread(self._run_asset_attach)
                   ).pack(side="left", pady=2)
        ttk.Button(b2, text="Sample Excel",
                   command=self._aa_sample_attach).pack(side="left", padx=8)

    # ── input collection (paste or Excel) ──

    def _aa_collect(self, src, default_account, paste_parser, record_parser):
        """Returns parsed rows, or None if the user should fix something first."""
        if src["mode"].get() == "excel":
            path = src["path"].get().strip()
            if not path:
                self._ui_error("Input", "Select an Excel file.")
                return None
            records = self._load_excel_safe(load_excel_records, path)
            if records is None:
                return None
            rows, errors = record_parser(records, default_account)
        else:
            rows, errors = paste_parser(src["paste"].get("1.0", "end"), default_account)

        for e in errors[:20]:
            self.log(f"  ⚠ {e}", "err")
        if len(errors) > 20:
            self.log(f"  ⚠ ...and {len(errors) - 20} more skipped rows.", "err")
        if not rows:
            self._ui_error("Input", "No valid rows to process."
                           + ("\n\n" + errors[0] if errors else ""))
            return None
        if errors and not self._ui_askyesno(
                "Skip invalid rows?",
                f"{len(errors)} row(s) will be skipped.\n"
                f"Continue with the {len(rows)} valid row(s)?"):
            return None
        return rows

    # ── runs ──

    def _run_asset_account(self):
        rows = self._aa_collect(self.aa_src, self.aa_account.get(),
                                L.parse_account_paste, L.parse_account_records)
        if not rows:
            return

        def fn(row):
            params, body = L.build_account_assign(row)
            r = session.put(ASSET_ACCOUNT_URL, params=params, json=body,
                            headers=api_headers(self.token,
                                                content_type="application/json"),
                            timeout=30)
            return (row["asset_id"], row["account_id"]), r
        self._loop(rows, "Assign Asset to Account", fn, ["Asset ID", "Account ID"])

    def _run_asset_attach(self):
        rows = self._aa_collect(self.at_src, self.at_account.get(),
                                L.parse_attach_paste, L.parse_attach_records)
        if not rows:
            return

        def fn(row):
            body = L.build_attach_body(row, ASSET_ATTACH_TYPE)
            r = session.post(ASSET_ATTACH_URL, json=body,
                             headers=api_headers(self.token,
                                                 content_type="application/json"),
                             timeout=30)
            return (row["asset_id"], row["vehicle_id"], row["account_id"],
                    ASSET_ATTACH_TYPE), r
        self._loop(rows, "Attach Asset to Vehicle", fn,
                   ["Asset ID", "Vehicle ID", "Account ID", "Type"])

    # ── sample workbooks ──

    def _aa_sample(self, headers, example_rows, default_name):
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx", initialfile=default_name,
            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Input"
            ws.append(headers)
            for row in example_rows:
                ws.append(row)
            for i in range(1, len(headers) + 1):
                ws.column_dimensions[chr(64 + i)].width = 20
            wb.save(path)
            messagebox.showinfo("Sample Excel", f"Saved:\n{path}")
        except Exception as e:
            self._ui_error("Sample Excel", f"Could not save:\n{e}")

    def _aa_sample_account(self):
        self._aa_sample(["assetId", "accountId"],
                        [["862798051206510", 17265],
                         ["862798051206511", ""]],
                        "assign_to_account_sample.xlsx")

    def _aa_sample_attach(self):
        self._aa_sample(["assetId", "vehicleId", "accountId"],
                        [["862798051206510", 2793621, 17809],
                         ["862798051206511", 2793622, ""]],
                        "attach_to_vehicle_sample.xlsx")
