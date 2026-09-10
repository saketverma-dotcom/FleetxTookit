"""Asset assign/attach row parsing and request bodies (v3.14).

Validated against the exact curl requests supplied:
  PUT  /api/v1/assets/account?accountId=17265   body ["<assetId>"]
  POST /api/v1/assets/attach
       body {"assetId":"862798051206510","vehicleId":2793621,
              "type":"CAMERA","accountId":17809}
"""
from fleetx_toolkit import logic as L


class TestAccountPaste:
    def test_asset_only_uses_default_account(self):
        rows, errs = L.parse_account_paste("862798051206510", "17265")
        assert not errs
        assert rows == [{"asset_id": "862798051206510", "account_id": "17265"}]

    def test_per_row_account_overrides_default(self):
        rows, errs = L.parse_account_paste("111,17999", "17265")
        assert rows[0]["account_id"] == "17999"

    def test_blank_lines_ignored(self):
        rows, errs = L.parse_account_paste("111\n\n  \n222", "17265")
        assert len(rows) == 2 and not errs

    def test_missing_account_rejected(self):
        rows, errs = L.parse_account_paste("111", "")
        assert not rows and "no accountId" in errs[0]

    def test_whitespace_tolerated(self):
        rows, errs = L.parse_account_paste("  111 , 17999 ", "")
        assert rows == [{"asset_id": "111", "account_id": "17999"}]


class TestAccountExcel:
    def test_columns_and_default(self):
        recs = [{"assetid": "111", "accountid": 17265}, {"assetid": "222"}]
        rows, errs = L.parse_account_records(recs, "17265")
        assert not errs and [r["account_id"] for r in rows] == ["17265", "17265"]

    def test_missing_asset_reports_row_number(self):
        rows, errs = L.parse_account_records([{"accountid": 9}], "17265")
        assert not rows and "Row 2" in errs[0]

    def test_alternate_column_names(self):
        recs = [{"asset_id": "111", "account_id": 5}]
        rows, errs = L.parse_account_records(recs, "")
        assert rows == [{"asset_id": "111", "account_id": "5"}]


class TestAccountRequest:
    def test_body_is_array_of_one(self):
        params, body = L.build_account_assign(
            {"asset_id": "862798051206510", "account_id": "17265"})
        assert params == {"accountId": "17265"}
        assert body == ["862798051206510"]      # array, one asset per call


class TestAttachPaste:
    def test_asset_and_vehicle(self):
        rows, errs = L.parse_attach_paste("862798051206510,2793621", "17809")
        assert not errs
        assert rows == [{"asset_id": "862798051206510", "vehicle_id": 2793621,
                         "account_id": "17809"}]

    def test_per_row_account(self):
        rows, errs = L.parse_attach_paste("111,222,17999", "17265")
        assert rows[0]["account_id"] == "17999"

    def test_vehicle_must_be_numeric(self):
        rows, errs = L.parse_attach_paste("111,ABC", "17265")
        assert not rows and "must be numeric" in errs[0]

    def test_vehicle_required(self):
        rows, errs = L.parse_attach_paste("111", "17265")
        assert not rows and "need assetId,vehicleId" in errs[0]

    def test_vehicle_id_is_int(self):
        rows, _ = L.parse_attach_paste("111,2793621", "17265")
        assert isinstance(rows[0]["vehicle_id"], int)


class TestAttachExcel:
    def test_columns(self):
        recs = [{"assetid": "862798051206510", "vehicleid": 2793621,
                 "accountid": 17809}]
        rows, errs = L.parse_attach_records(recs, "")
        assert not errs and rows[0]["vehicle_id"] == 2793621

    def test_default_account_when_blank(self):
        recs = [{"assetid": "111", "vehicleid": 222}]
        rows, errs = L.parse_attach_records(recs, "17265")
        assert rows[0]["account_id"] == "17265"

    def test_bad_vehicle_reports_row(self):
        rows, errs = L.parse_attach_records(
            [{"assetid": "111", "vehicleid": "xx"}], "17265")
        assert not rows and "Row 2" in errs[0]


class TestAttachRequest:
    def test_body_matches_supplied_curl(self):
        rows, _ = L.parse_attach_records(
            [{"assetid": "862798051206510", "vehicleid": 2793621,
              "accountid": 17809}], "")
        body = L.build_attach_body(rows[0], "CAMERA")
        assert body == {"assetId": "862798051206510", "vehicleId": 2793621,
                        "type": "CAMERA", "accountId": 17809}

    def test_account_id_numeric_when_possible(self):
        body = L.build_attach_body(
            {"asset_id": "1", "vehicle_id": 2, "account_id": "17809"}, "CAMERA")
        assert body["accountId"] == 17809


class TestTabRegistration:
    def test_tab_is_controllable(self):
        from fleetx_toolkit.config import CONTROLLABLE_TABS
        assert "Asset Assign/Attach" in CONTROLLABLE_TABS

    def test_admin_gets_it_automatically(self):
        from fleetx_toolkit.access_control import allowed_tabs_for
        assert "Asset Assign/Attach" in allowed_tabs_for("saket.verma@fleetx.io")

    def test_non_admin_needs_explicit_grant(self):
        from fleetx_toolkit import access_control as ac
        ac.set_snapshot({"other@fleetx.io": ["Tickets"]})
        assert "Asset Assign/Attach" not in allowed_or_empty("other@fleetx.io")


def allowed_or_empty(email):
    from fleetx_toolkit.access_control import allowed_tabs_for
    return allowed_tabs_for(email)
