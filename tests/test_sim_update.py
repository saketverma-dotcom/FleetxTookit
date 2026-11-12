"""SIM Update request shape (v4.0.3).

The working request, confirmed against the platform:

    PUT /api/v1/devices/{id}
    content-type: application/json;charset=UTF-8
    {"id": "<id>", "sim": "<sim>", "mobile": "<sim>"}

The toolkit was sending {"sim","mobile"} with no "id" and no explicit
content-type. These tests pin the shape so it cannot drift back.
"""
import inspect

from fleetx_toolkit.ui import tabs_devices


SRC = inspect.getsource(tabs_devices.DeviceTabsMixin._run_sim_update)


class TestRequestShape:
    def test_uses_put(self):
        assert "session.put(" in SRC
        assert "session.patch(" not in SRC and "session.post(" not in SRC

    def test_url_targets_the_device_id(self):
        assert "/api/v1/devices/{did}" in SRC

    def test_reads_the_device_before_writing(self):
        """PUT needs the FULL device object, so the current one is fetched
        first and only sim/mobile are changed."""
        assert "session.get(" in SRC
        assert "apply_sim_update" in SRC

    def test_does_not_send_a_three_field_stub(self):
        """The old body was {"id","sim","mobile"} and was rejected."""
        assert '{"id": did, "sim": sim, "mobile": mob}' not in SRC

    def test_sends_json_content_type(self):
        assert "application/json;charset=UTF-8" in SRC


class TestBlankCells:
    """openpyxl returns None for an empty cell. str(None) is "None", which
    would have been sent as a real mobile number."""

    @staticmethod
    def _resolve(rec):
        cell = lambda k: "" if rec.get(k) is None else str(rec.get(k)).strip()
        return cell("mobile") or cell("sim")

    def test_blank_mobile_falls_back_to_sim(self):
        assert self._resolve({"sim": "8945730000", "mobile": None}) == "8945730000"

    def test_missing_mobile_key_falls_back_to_sim(self):
        assert self._resolve({"sim": "8945730000"}) == "8945730000"

    def test_whitespace_mobile_falls_back_to_sim(self):
        assert self._resolve({"sim": "8945730000", "mobile": "   "}) == "8945730000"

    def test_explicit_mobile_is_kept(self):
        assert self._resolve({"sim": "8945730000", "mobile": "9876543210"}) == "9876543210"

    def test_never_emits_the_string_none(self):
        for rec in ({"sim": "1", "mobile": None}, {"sim": "1"}, {"sim": "1", "mobile": ""}):
            assert self._resolve(rec) != "None"


class TestReadModifyWrite:
    """v4.1: PUT /api/v1/devices/{id} needs the FULL device object.

    The real request carries createdDate, createdBy, deviceType, imei,
    serialNumber, status, blockInstall, blockInstallReason and the rest — a
    three-field stub was rejected.
    """

    DEVICE = {
        "createdDate": 1794468696000, "lastModifiedDate": 1794468696000,
        "createdBy": 156286, "lastModifiedBy": 156286,
        "id": "354002392459689", "deviceType": "FMB920",
        "deviceSupplier": "CLIENT", "imei": "354002392459689",
        "serialNumber": "FXTZ -003195", "status": "ACTIVE",
        "blockInstall": False, "sim": "OLDSIM", "mobile": "OLDSIM",
        "blockInstallReason": None,
    }
    NEW = "89457300000053998301"

    def test_sets_sim_and_mobile(self):
        from fleetx_toolkit import logic as L
        out, prev = L.apply_sim_update(self.DEVICE, self.NEW)
        assert out["sim"] == out["mobile"] == self.NEW
        assert prev == "OLDSIM"

    def test_every_other_field_survives(self):
        from fleetx_toolkit import logic as L
        out, _ = L.apply_sim_update(self.DEVICE, self.NEW)
        for k, v in self.DEVICE.items():
            if k not in ("sim", "mobile"):
                assert out[k] == v, f"{k} was altered"

    def test_null_fields_are_preserved_as_null(self):
        from fleetx_toolkit import logic as L
        out, _ = L.apply_sim_update(self.DEVICE, self.NEW)
        assert "blockInstallReason" in out and out["blockInstallReason"] is None

    def test_body_is_not_a_stub(self):
        from fleetx_toolkit import logic as L
        out, _ = L.apply_sim_update(self.DEVICE, self.NEW)
        assert len(out) == len(self.DEVICE) >= 13

    def test_explicit_mobile_is_used(self):
        from fleetx_toolkit import logic as L
        out, _ = L.apply_sim_update(self.DEVICE, self.NEW, "9876543210")
        assert out["sim"] == self.NEW and out["mobile"] == "9876543210"

    def test_blank_mobile_falls_back_to_sim(self):
        from fleetx_toolkit import logic as L
        for blank in ("", None):
            out, _ = L.apply_sim_update(self.DEVICE, self.NEW, blank)
            assert out["mobile"] == self.NEW

    def test_original_not_mutated(self):
        from fleetx_toolkit import logic as L
        L.apply_sim_update(self.DEVICE, self.NEW)
        assert self.DEVICE["sim"] == "OLDSIM"

    def test_bad_payload_rejected(self):
        import pytest
        from fleetx_toolkit import logic as L
        with pytest.raises(ValueError):
            L.apply_sim_update("not a dict", self.NEW)

    def test_skip_when_already_set(self):
        from fleetx_toolkit import logic as L
        assert L.sim_update_needed(self.DEVICE, self.NEW)
        done = dict(self.DEVICE, sim=self.NEW, mobile=self.NEW)
        assert not L.sim_update_needed(done, self.NEW)

    def test_label_prefers_imei(self):
        from fleetx_toolkit import logic as L
        assert L.device_label(self.DEVICE) == "354002392459689"
        assert L.device_label({"serialNumber": "FXTZ-1"}) == "FXTZ-1"
        assert L.device_label({"id": "9"}) == "9"


class TestMatchesBrowserRequestExactly:
    """The GET returns 13 fields and omits blockInstallReason, but the
    browser's working PUT sends it as null. We add it only when absent, so the
    body we send is field-identical to the request known to succeed."""

    GET_RESPONSE = {
        "createdDate": 1794468696000, "lastModifiedDate": 1794477420000,
        "createdBy": 156286, "lastModifiedBy": 156286,
        "id": "354002392459689", "deviceType": "FMB920",
        "deviceSupplier": "CLIENT", "imei": "354002392459689",
        "sim": "89457300000053998301", "mobile": "89457300000053998301",
        "serialNumber": "FXTZ -003195", "status": "ACTIVE",
        "blockInstall": False,
    }
    WORKING_PUT_FIELDS = {
        "createdDate", "lastModifiedDate", "createdBy", "lastModifiedBy",
        "id", "deviceType", "deviceSupplier", "imei", "serialNumber",
        "status", "blockInstall", "sim", "mobile", "blockInstallReason",
    }

    def test_round_trip_matches_the_working_body(self):
        from fleetx_toolkit import logic as L
        out, _ = L.apply_sim_update(self.GET_RESPONSE, "8945730000009999")
        assert set(out) == self.WORKING_PUT_FIELDS

    def test_block_install_reason_added_as_null_when_absent(self):
        from fleetx_toolkit import logic as L
        out, _ = L.apply_sim_update(self.GET_RESPONSE, "8945730000009999")
        assert out["blockInstallReason"] is None

    def test_existing_block_install_reason_is_not_overwritten(self):
        from fleetx_toolkit import logic as L
        src = dict(self.GET_RESPONSE, blockInstallReason="damaged")
        out, _ = L.apply_sim_update(src, "8945730000009999")
        assert out["blockInstallReason"] == "damaged"
