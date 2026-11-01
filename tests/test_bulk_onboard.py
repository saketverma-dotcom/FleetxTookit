"""Bulk Onboard 5-step sequential flow (v3.15).

Row shape: deviceId, vehicleId, accountId [, sim [, serialNumber [, deviceType]]]
assetId == deviceId. Run-level defaults fill any value a row omits.
"""
from fleetx_toolkit import logic as L

DEFAULTS = {"deviceType": "LCD40AI-2CH",   # NOTE: no accountId default
            "assetSupplier": "HOWEN", "assetModel": "LCD40AI-2CH",
            "assetType": "CAMERA", "issuedToUserId": "14203"}


class TestPaste:
    def test_full_row(self):
        rows, errs = L.parse_onboard_paste(
            "862798051206510,2793621,17809,8991000012345,SN123,FMB920", DEFAULTS)
        assert not errs
        r = rows[0]
        assert r["device_id"] == "862798051206510"
        assert r["asset_id"] == r["device_id"]        # assetId == deviceId
        assert r["vehicle_id"] == 2793621 and isinstance(r["vehicle_id"], int)
        assert r["sim"] == "8991000012345" and r["serial_number"] == "SN123"
        assert r["device_type"] == "FMB920"           # per-row wins

    def test_defaults_fill_optional_fields_only(self):
        rows, errs = L.parse_onboard_paste("862798051206510,2793621,17809", DEFAULTS)
        assert not errs
        assert rows[0]["device_type"] == "LCD40AI-2CH"   # run-level default
        assert rows[0]["sim"] == "" and rows[0]["serial_number"] == ""

    def test_account_is_required_no_default(self):
        """accountId must be supplied per row; a missing value stops the row."""
        rows, errs = L.parse_onboard_paste("862798051206510,2793621", DEFAULTS)
        assert not rows and "accountId is required" in errs[0]

    def test_blank_account_field_rejected(self):
        rows, errs = L.parse_onboard_paste("862798051206510,2793621, ", DEFAULTS)
        assert not rows and "accountId is required" in errs[0]

    def test_blank_lines_skipped(self):
        rows, errs = L.parse_onboard_paste(
            "111111,2,3\n\n   \n222222,4,5", DEFAULTS)
        assert len(rows) == 2 and not errs

    def test_non_numeric_device_rejected(self):
        rows, errs = L.parse_onboard_paste("abc,2793621,17809", DEFAULTS)
        assert not rows and "must be numeric (IMEI)" in errs[0]

    def test_non_numeric_vehicle_rejected(self):
        rows, errs = L.parse_onboard_paste("862798051206510,XYZ,17809", DEFAULTS)
        assert not rows and "vehicleId" in errs[0]

    def test_missing_vehicle_rejected(self):
        rows, errs = L.parse_onboard_paste("862798051206510", DEFAULTS)
        assert not rows and "missing vehicleId" in errs[0]

    def test_missing_account_rejected(self):
        rows, errs = L.parse_onboard_paste("862798051206510,2793621",
                                           {"deviceType": "X"})
        assert not rows and "accountId is required" in errs[0]


class TestExcel:
    def test_columns(self):
        recs = [{"deviceId": "862798051206510", "vehicleId": 2793621,
                 "accountId": 17809, "sim": "899", "serialNumber": "SN",
                 "deviceType": "LCD40"}]
        rows, errs = L.parse_onboard_records(recs, DEFAULTS)
        assert not errs and rows[0]["device_type"] == "LCD40"

    def test_blank_devicetype_uses_default_but_account_required(self):
        recs = [{"deviceId": "862798051206511", "vehicleId": 2793622,
                 "accountId": 17809, "deviceType": None}]
        rows, errs = L.parse_onboard_records(recs, DEFAULTS)
        assert rows[0]["device_type"] == "LCD40AI-2CH"

    def test_blank_account_cell_rejected(self):
        recs = [{"deviceId": "862798051206511", "vehicleId": 2793622,
                 "accountId": None}]
        rows, errs = L.parse_onboard_records(recs, DEFAULTS)
        assert not rows and "accountId is required" in errs[0]

    def test_row_number_in_error(self):
        rows, errs = L.parse_onboard_records([{"vehicleId": 1}], DEFAULTS)
        assert not rows and "Row 2" in errs[0]


class TestBodies:
    def _row(self):
        rows, _ = L.parse_onboard_paste(
            "862798051206510,2793621,17809,8991000012345,SN123", DEFAULTS)
        return rows[0]

    def test_device_body_keeps_client_supplier(self):
        b = L.build_onboard_device(self._row())
        assert b["deviceSupplier"] == "CLIENT"      # device supplier unchanged
        assert b["sim"] == b["mobile"] == "8991000012345"

    def test_device_id_and_imei_are_integers(self):
        """REGRESSION: sending id/imei as strings differed from the working
        Device Add tab (which casts to int) and caused 409 responses."""
        b = L.build_onboard_device(self._row())
        assert b["id"] == b["imei"] == 862798051206510
        assert isinstance(b["id"], int) and isinstance(b["imei"], int)

    def test_device_body_drops_blanks(self):
        rows, _ = L.parse_onboard_paste("862798051206510,2793621,17809", DEFAULTS)
        b = L.build_onboard_device(rows[0])
        assert "sim" not in b and "serialNumber" not in b

    def test_asset_body_uses_selected_supplier(self):
        b = L.build_onboard_asset(self._row(), DEFAULTS)
        assert b["supplier"] == "HOWEN"
        assert b["assetId"] == b["name"] == b["productId"] == 862798051206510
        assert b["status"] == "ACTIVE" and b["issuedToUserId"] == 14203

    def test_account_and_attach_reuse_shared_builders(self):
        row = self._row()
        params, body = L.build_account_assign(row)
        assert params == {"accountId": "17809"} and body == ["862798051206510"]
        attach = L.build_attach_body(row, "CAMERA")
        assert attach == {"assetId": "862798051206510", "vehicleId": 2793621,
                          "type": "CAMERA", "accountId": 17809}


class TestStepsAndAccess:
    def test_five_steps_named_in_order(self):
        assert L.ONBOARD_STEPS == ["Device Add", "Asset Add", "Vehicle Map",
                                   "Asset→Account", "Asset→Vehicle"]

    def test_tab_controllable_and_admin_enabled(self):
        from fleetx_toolkit.config import CONTROLLABLE_TABS
        from fleetx_toolkit.access_control import allowed_tabs_for
        assert "Bulk Onboard" in CONTROLLABLE_TABS
        assert "Bulk Onboard" in allowed_tabs_for("saket.verma@fleetx.io")


class TestDeviceSupplier:
    """v3.16: device supplier is selectable; CLIENT remains the default."""

    def _row(self):
        rows, _ = L.parse_onboard_paste("862798051206510,2793621,17809", DEFAULTS)
        return rows[0]

    def test_defaults_to_client(self):
        b = L.build_onboard_device(self._row())
        assert b["deviceSupplier"] == "CLIENT"

    def test_defaults_to_client_when_blank(self):
        b = L.build_onboard_device(self._row(), {"deviceSupplier": "   "})
        assert b["deviceSupplier"] == "CLIENT"

    def test_uses_selected_supplier(self):
        b = L.build_onboard_device(self._row(), {"deviceSupplier": "TELTONIKA"})
        assert b["deviceSupplier"] == "TELTONIKA"

    def test_supplier_list_has_client_first(self):
        from fleetx_toolkit.config import DEVICE_SUPPLIERS
        assert DEVICE_SUPPLIERS[0] == "CLIENT"


class TestRealWorldSheetHeaders:
    """v3.19.1: accept the column names people actually use.

    A real sheet looks like:
      Plate Number | Service | Device Type | IMEI | SIM | ICCID | Vehicle ID | Account
    "Vehicle ID" contains a space, which the old normaliser (underscores only)
    failed to match, so every row was rejected for a missing vehicleId.
    """

    SHEET = [{"Plate Number": "CBN9071", "Service": "2GO LOGISTICS INC.",
              "Device Type": "2CH LCD40AI", "IMEI": 867191084121291,
              "SIM": 9494200511, "ICCID": 3.25e14,
              "Vehicle ID": 3146847, "Account": 17413}]

    def test_spaced_headers_match(self):
        rows, errs = L.parse_onboard_records(self.SHEET, {"deviceType": "X"})
        assert not errs and len(rows) == 1

    def test_fields_map_correctly(self):
        rows, _ = L.parse_onboard_records(self.SHEET, {"deviceType": "X"})
        r = rows[0]
        assert r["device_id"] == "867191084121291"     # IMEI
        assert r["vehicle_id"] == 3146847              # Vehicle ID
        assert r["account_id"] == "17413"              # Account
        assert r["sim"] == "9494200511"                # SIM

    def test_extra_columns_ignored(self):
        """Plate Number / Service / ICCID must not break parsing."""
        rows, errs = L.parse_onboard_records(self.SHEET, {"deviceType": "X"})
        assert not errs and rows[0]["serial_number"] == ""

    def test_per_row_accounts_are_kept_apart(self):
        sheet = self.SHEET + [dict(self.SHEET[0], **{"IMEI": 867191084135200,
                                                     "Vehicle ID": 3147081,
                                                     "Account": 18364})]
        rows, _ = L.parse_onboard_records(sheet, {"deviceType": "X"})
        assert [r["account_id"] for r in rows] == ["17413", "18364"]

    def test_sheet_device_type_wins_by_default(self):
        rows, _ = L.parse_onboard_records(self.SHEET, {"deviceType": "LCD40AI-2CH"})
        assert rows[0]["device_type"] == "2CH LCD40AI"

    def test_sheet_device_type_can_be_ignored(self):
        """The sheet often carries a human label, not the API's deviceType."""
        rows, _ = L.parse_onboard_records(
            self.SHEET, {"deviceType": "LCD40AI-2CH", "ignoreSheetDeviceType": True})
        assert rows[0]["device_type"] == "LCD40AI-2CH"


class TestPreflight:
    """v3.20: summarise a batch before any write, and require an explicit
    acknowledgement when it spans several accounts — a wrong accountId is only
    visible after assets have landed in the wrong customer's fleet."""

    @staticmethod
    def _row(dev, veh, acc, typ="LCD40AI-2CH"):
        return {"device_id": dev, "asset_id": dev, "vehicle_id": veh,
                "account_id": acc, "sim": "", "serial_number": "",
                "device_type": typ}

    def test_counts_rows_and_writes(self):
        s = L.preflight_summary([self._row("1", 2, "9"), self._row("3", 4, "9")])
        assert s["count"] == 2
        assert s["writes"] == 2 * len(L.ONBOARD_STEPS) == 10

    def test_single_account_needs_no_ack(self):
        s = L.preflight_summary([self._row("1", 2, "17413"),
                                 self._row("3", 4, "17413")])
        assert s["warnings"] == [] and s["needs_ack"] is False

    def test_multiple_accounts_warn_and_require_ack(self):
        s = L.preflight_summary([self._row("1", 2, "17413"),
                                 self._row("3", 4, "18364")])
        assert s["needs_ack"] is True
        assert any(k == "accounts" for k, _ in s["warnings"])

    def test_account_spread_is_counted(self):
        rows = ([self._row(str(i), i, "17413") for i in range(6)]
                + [self._row("x", 99, "18364")])
        s = L.preflight_summary(rows)
        assert s["accounts"] == {"17413": 6, "18364": 1}

    def test_duplicate_device_ids_flagged(self):
        s = L.preflight_summary([self._row("111", 5, "1"), self._row("111", 6, "1")])
        assert any(k == "duplicate-device" for k, _ in s["warnings"])

    def test_duplicate_vehicle_ids_flagged(self):
        s = L.preflight_summary([self._row("111", 5, "1"), self._row("222", 5, "1")])
        assert any(k == "duplicate-vehicle" for k, _ in s["warnings"])

    def test_mixed_device_types_flagged(self):
        s = L.preflight_summary([self._row("1", 2, "9", "LCD40AI-2CH"),
                                 self._row("3", 4, "9", "FMB920")])
        assert any(k == "device-type" for k, _ in s["warnings"])

    def test_preview_is_capped_at_five(self):
        s = L.preflight_summary([self._row(str(i), i, "9") for i in range(9)])
        assert len(s["preview"]) == 5 and s["more"] == 4

    def test_empty_batch(self):
        s = L.preflight_summary([])
        assert s["count"] == 0 and s["writes"] == 0 and not s["needs_ack"]
