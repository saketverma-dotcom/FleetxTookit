"""FleetX Toolkit pure-logic test suite. No Tk, no network.
Run from the repo root:  pytest -q
"""
import base64
import json
import os

import pytest

from fleetx_toolkit import access_control as ac
from fleetx_toolkit import config as cf
from fleetx_toolkit import io_utils as io
from fleetx_toolkit import logic
from fleetx_toolkit import storage as st
from fleetx_toolkit import updater as up


# ═══════════════════ input parsing ═══════════════════

class TestParsePastedIds:
    def test_newlines_commas_mixed(self):
        assert io.parse_pasted_ids("123, 456\n789") == ["123", "456", "789"]

    def test_excel_float_artifacts_trimmed(self):
        assert io.parse_pasted_ids("869123456789012.0\n123.0") == \
            ["869123456789012", "123"]

    def test_junk_and_blank_lines_dropped(self):
        assert io.parse_pasted_ids("abc\n\n  \n12x34\n55") == ["55"]

    def test_empty_input(self):
        assert io.parse_pasted_ids("") == []


class TestParsePastedPairs:
    def test_space_comma_tab_separators(self):
        assert io.parse_pasted_pairs("11 22\n33,44\n55\t66") == \
            [("11", "22"), ("33", "44"), ("55", "66")]

    def test_lines_without_two_numbers_skipped(self):
        assert io.parse_pasted_pairs("only 1number\n77 88") == [("77", "88")]

    def test_float_artifacts_trimmed(self):
        assert io.parse_pasted_pairs("11.0 22.0") == [("11", "22")]


class TestParseCurl:
    def test_form_style_detected_from_sendcommands_url(self):
        c = io.parse_curl_command(
            "curl 'https://app.fleetx.io/trigger/sendcommands' "
            "--data 'commandId=69b3c416e7270303577102b8&commandName=SPEED_ON'")
        assert c["id"] == "69b3c416e7270303577102b8"
        assert c["name"] == "SPEED_ON"
        assert c["style"] == "form"

    def test_json_style_default(self):
        c = io.parse_curl_command(
            'curl -X POST --data \'{"commandId": "69faa7f1b59269a4efe3cd82"}\'')
        assert c["id"] == "69faa7f1b59269a4efe3cd82"
        assert c["style"] == "json"

    def test_no_command_id(self):
        assert io.parse_curl_command("curl https://x.y")["id"] == ""

    def test_device_type_extracted(self):
        c = io.parse_curl_command("--data deviceType=FX11 commandId=aabbccddeeff00112233")
        assert c["deviceType"] == "FX11"


class TestExcel:
    def _wb(self, tmp_path, rows):
        import openpyxl
        wb = openpyxl.Workbook()
        for r in rows:
            wb.active.append(r)
        p = tmp_path / "t.xlsx"
        wb.save(p)
        return str(p)

    def test_prefers_imei_column_over_first(self, tmp_path):
        p = self._wb(tmp_path, [("serial", "IMEI"), ("s1", 869000000000001),
                                ("s2", 869000000000002)])
        assert io.load_excel_column(p) == ["869000000000001", "869000000000002"]

    def test_falls_back_to_first_column(self, tmp_path):
        p = self._wb(tmp_path, [("whatever", "x"), (101, "a"), (102, "b")])
        assert io.load_excel_column(p) == ["101", "102"]

    def test_float_and_blank_handling(self, tmp_path):
        p = self._wb(tmp_path, [("id",), (123.0,), (None,), ("",), (456,)])
        assert io.load_excel_column(p) == ["123", "456"]

    def test_records_header_normalized(self, tmp_path):
        p = self._wb(tmp_path, [("Device ID", "SIM"), (1, "89910000"), (2, "89910001")])
        recs = io.load_excel_records(p)
        assert recs == [{"device_id": 1, "sim": "89910000"},
                        {"device_id": 2, "sim": "89910001"}]

    def test_empty_sheet(self, tmp_path):
        p = self._wb(tmp_path, [])
        assert io.load_excel_column(p) == []
        assert io.load_excel_records(p) == []


# ═══════════════════ quota splitting ═══════════════════

class TestSplitByCounts:
    T = [str(i) for i in range(1, 11)]        # 10 tickets

    def test_exact_counts_in_order(self):
        a, un, inv = logic.split_tickets_by_counts(self.T, [(1, "4"), (2, "6")])
        assert inv is None and un == 0
        assert [x for x, i in a if i == 1] == self.T[:4]
        assert [x for x, i in a if i == 2] == self.T[4:]

    def test_rest_takes_remainder(self):
        a, un, inv = logic.split_tickets_by_counts(self.T, [(1, "3"), (2, "rest")])
        assert un == 0 and inv is None
        assert [x for x, i in a if i == 2] == self.T[3:]

    def test_blank_means_rest(self):
        a, un, inv = logic.split_tickets_by_counts(self.T, [(1, "8"), (2, "")])
        assert [x for x, i in a if i == 2] == self.T[8:]

    def test_multiple_rest_round_robin(self):
        a, un, inv = logic.split_tickets_by_counts(self.T, [(1, "4"), (2, "rest"), (3, "rest")])
        assert [x for x, i in a if i == 2] == ["5", "7", "9"]
        assert [x for x, i in a if i == 3] == ["6", "8", "10"]

    def test_counts_exceed_total(self):
        a, un, inv = logic.split_tickets_by_counts(self.T, [(1, "7"), (2, "7")])
        assert inv is None and un == 0
        assert len(a) == 10 and [x for x, i in a if i == 2] == self.T[7:]

    def test_counts_below_total_no_rest_reports_unassigned(self):
        a, un, inv = logic.split_tickets_by_counts(self.T, [(1, "3"), (2, "4")])
        assert un == 3 and len(a) == 7

    def test_invalid_count_reports_position_and_value(self):
        a, un, inv = logic.split_tickets_by_counts(self.T, [(1, "3"), (2, "ten")])
        assert a == [] and inv == (1, "ten")

    def test_rest_string_case_insensitive(self):
        a, un, inv = logic.split_tickets_by_counts(self.T, [(1, " REST "), (2, "10")])
        assert inv is None
        assert [x for x, i in a if i == 1] == []   # count-10 consumed everything

    def test_no_tickets(self):
        assert logic.split_tickets_by_counts([], [(1, "rest")]) == ([], 0, None)


class TestSplitEqual:
    class FakeRng:
        def shuffle(self, x):        # deterministic: reverse
            x.reverse()

    def test_round_robin_balanced(self):
        a = logic.split_tickets_equal(["1", "2", "3", "4", "5"], [10, 20], rng=self.FakeRng())
        per = {}
        for _, aid in a:
            per[aid] = per.get(aid, 0) + 1
        assert per == {10: 3, 20: 2}

    def test_original_list_not_mutated(self):
        t = ["1", "2", "3"]
        logic.split_tickets_equal(t, [1], rng=self.FakeRng())
        assert t == ["1", "2", "3"]

    def test_all_tickets_assigned_exactly_once(self):
        t = [str(i) for i in range(97)]
        a = logic.split_tickets_equal(t, [1, 2, 3])
        assert sorted(x for x, _ in a) == sorted(t)


# ═══════════════════ retry backoff ═══════════════════

class TestBackoff:
    def test_ladder_then_exhaustion(self):
        assert [logic.retry_wait(i) for i in range(5)] == [5, 15, 30, None, None]

    def test_negative_attempt_is_none(self):
        assert logic.retry_wait(-1) is None

    def test_custom_ladder(self):
        assert logic.retry_wait(1, [2, 4]) == 4
        assert logic.retry_wait(2, [2, 4]) is None


# ═══════════════════ access resolution ═══════════════════

@pytest.fixture
def rules():
    snap = {"vinay.tyagi@fleetx.io": ["Tickets", "Send Command"],
            "komal.bisht@fleetx.io": []}
    ac.set_snapshot(snap)
    yield snap
    ac.set_snapshot(None)


class TestAccess:
    def test_admin_case_insensitive(self):
        assert ac.is_admin("SAKET.VERMA@FLEETX.IO")
        assert ac.is_admin("  saket.verma@fleetx.io  ")

    def test_admin_gets_every_tab(self, rules):
        assert ac.allowed_tabs_for("saket.verma@fleetx.io") == cf.CONTROLLABLE_TABS

    def test_granted_user(self, rules):
        assert ac.is_authorized("vinay.tyagi@fleetx.io")
        assert set(ac.allowed_tabs_for("vinay.tyagi@fleetx.io")) == \
            {"Tickets", "Send Command"}

    def test_zero_tab_user_not_authorized(self, rules):
        assert not ac.is_authorized("komal.bisht@fleetx.io")

    def test_unknown_fleetx_user_denied(self, rules):
        assert not ac.is_authorized("nobody@fleetx.io")

    def test_wrong_domain_denied_even_if_listed(self):
        ac.set_snapshot({"someone@gmail.com": ["Tickets"]})
        assert not ac.is_authorized("someone@gmail.com")
        ac.set_snapshot(None)

    def test_unknown_tab_names_filtered_out(self):
        ac.set_snapshot({"a@fleetx.io": ["Tickets", "Nuke Prod", "Assets"]})
        assert ac.allowed_tabs_for("a@fleetx.io") == ["Assets", "Tickets"] or \
            set(ac.allowed_tabs_for("a@fleetx.io")) == {"Tickets", "Assets"}
        ac.set_snapshot(None)

    def test_snapshot_none_falls_back_to_load_access(self, monkeypatch):
        ac.set_snapshot(None)
        monkeypatch.setattr(ac, "load_access",
                            lambda: {"x@fleetx.io": ["Assets"]})
        assert ac.allowed_tabs_for("x@fleetx.io") == ["Assets"]


# ═══════════════════ updater version logic ═══════════════════

class TestUpdater:
    def _meta(self, monkeypatch, **kw):
        monkeypatch.setattr(ac, "_REMOTE_META", kw, raising=False)

    def test_newer_version_offered(self, monkeypatch):
        self._meta(monkeypatch, _latest_version="3.1",
                   _download_url="https://x/e.exe", _sha256="ABC")
        assert up.check_update() == ("3.1", "https://x/e.exe", "abc")

    def test_same_or_older_not_offered(self, monkeypatch):
        for v in (cf.APP_VERSION, "0.9"):
            self._meta(monkeypatch, _latest_version=v, _download_url="https://x")
            assert up.check_update() is None

    def test_missing_url_not_offered(self, monkeypatch):
        self._meta(monkeypatch, _latest_version="99.0")
        assert up.check_update() is None

    def test_numeric_not_lexicographic(self):
        assert up._ver_tuple("3.10") > up._ver_tuple("3.9")
        assert up._ver_tuple("v3.1.2") == (3, 1, 2)

    def test_download_refused_in_dev_mode(self):
        p, err = up.download_update("https://x", "")
        assert p is None and "dev mode" in err


# ═══════════════════ secret storage (in-memory keyring) ═══════════════════

@pytest.fixture
def memkeyring(tmp_path, monkeypatch):
    import keyring, keyring.backend

    class Mem(keyring.backend.KeyringBackend):
        priority = 1
        def __init__(self):
            self.store = {}
        def set_password(self, s, u, p): self.store[(s, u)] = p
        def get_password(self, s, u): return self.store.get((s, u))
        def delete_password(self, s, u): self.store.pop((s, u), None)

    keyring.set_keyring(Mem())
    monkeypatch.setattr(st, "CRED_FILE", str(tmp_path / "creds.json"))
    monkeypatch.setattr(st, "GHTOKEN_FILE", str(tmp_path / "gh.json"))
    monkeypatch.setattr(cf, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(st, "_KEYRING_OK", True)
    yield


class TestSecretStorage:
    def test_round_trip_and_clear(self, memkeyring):
        assert st.save_credentials("a@fleetx.io", "pw!") is True
        assert st.load_credentials() == ("a@fleetx.io", "pw!")
        st.clear_credentials()
        assert st.load_credentials() == ("", "")

    def test_no_secret_on_disk(self, memkeyring, tmp_path):
        st.save_credentials("a@fleetx.io", "SuperSecret9")
        joined = "".join(p.read_text() for p in tmp_path.iterdir())
        assert "SuperSecret9" not in joined

    def test_legacy_base64_migrated_and_deleted(self, memkeyring):
        json.dump({"email": "b@fleetx.io",
                   "password": base64.b64encode(b"old").decode()},
                  open(st.CRED_FILE, "w"))
        assert st.load_credentials() == ("b@fleetx.io", "old")
        assert not os.path.exists(st.CRED_FILE)
        assert st.load_credentials() == ("b@fleetx.io", "old")   # from keyring now

    def test_gh_token_legacy_migration(self, memkeyring):
        json.dump({"token": "ghp_legacy"}, open(st.GHTOKEN_FILE, "w"))
        assert st.load_gh_token() == "ghp_legacy"
        assert not os.path.exists(st.GHTOKEN_FILE)

    def test_keyring_unavailable_never_writes_plaintext(self, memkeyring, monkeypatch):
        monkeypatch.setattr(st, "_KEYRING_OK", False)
        assert st.save_credentials("c@fleetx.io", "x") is False
        assert st.save_gh_token("t") is False
        assert not os.path.exists(st.CRED_FILE)
        assert not os.path.exists(st.GHTOKEN_FILE)
