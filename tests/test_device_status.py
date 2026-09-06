"""SIM battery/online status for the dropdowns (v3.13).

Must be cheap and non-blocking: all parsing is pure, fetches are cached with a
TTL and run off the UI thread.
"""
import time

from fleetx_toolkit import device_status as DS


# Fields exactly as documented at semysms.net/api.php (devices.php):
#   bat = battery %, power = service on/off (online), device_name = name,
#   is_work = "use for sending" (a setting, NOT online state)
SAMPLE = {"code": 0, "count": 3, "data": [
    {"id": 350374, "device_name": "Airtel Pulse", "power": 1, "is_work": 1,
     "bat": "87", "date_last_active": "2026-01-09 14:25:29.548563"},
    {"id": 338826, "device_name": "Voda Restrict 1", "power": 0, "is_work": 1,
     "bat": "42"},
    {"id": 355387, "device_name": "Airtel 1", "power": 1, "is_work": 0,
     "bat": 55},
    {"id": "weird"},                      # unknown state must not crash
]}


class TestParse:
    def test_online_and_battery(self):
        d = DS.parse_devices(SAMPLE)
        assert d["350374"]["online"] is True and d["350374"]["battery"] == 87
        assert d["338826"]["online"] is False and d["338826"]["battery"] == 42

    def test_battery_comes_from_bat_not_power(self):
        """REGRESSION: `power` is an on/off flag. Reading it as the battery
        made every SIM display "1%"."""
        d = DS.parse_devices(SAMPLE)
        assert d["350374"]["battery"] == 87        # bat, not power
        assert d["350374"]["battery"] != 1
        assert d["355387"]["battery"] == 55        # bat as an int

    def test_is_work_is_not_online(self):
        """is_work means "use for sending" — a setting, not online state."""
        d = DS.parse_devices(SAMPLE)
        assert d["355387"]["online"] is True       # power=1
        assert d["355387"]["for_sending"] is False  # is_work=0, kept separate

    def test_device_name_field(self):
        d = DS.parse_devices(SAMPLE)
        assert d["350374"]["name"] == "Airtel Pulse"

    def test_docs_example_verbatim(self):
        doc = {"code": 0, "count": 1, "data": [{
            "id": 351, "is_arhive": 0, "is_deliv": 1, "is_work": 1, "power": 1,
            "device_name": "U8186", "dop_name": "", "speed_sms": 1,
            "date_last_active": "2026-01-09 14:25:29.548563", "version": "40",
            "manufacturer": "HUAWEI", "android_version": "2.3.6",
            "bat": "58", "type": 0}]}
        d = DS.parse_devices(doc)["351"]
        assert d["battery"] == 58 and d["online"] is True and d["name"] == "U8186"

    def test_unknown_state_is_none(self):
        d = DS.parse_devices(SAMPLE)
        assert d["weird"]["online"] is None and d["weird"]["battery"] is None

    def test_garbage(self):
        assert DS.parse_devices("nope") == {}
        assert DS.parse_devices({}) == {}
        assert DS.parse_devices({"data": None}) == {}

    def test_battery_clamped(self):
        d = DS.parse_devices({"data": [{"id": 1, "bat": 250},
                                       {"id": 2, "bat": -5}]})
        assert d["1"]["battery"] == 100 and d["2"]["battery"] == 0


class TestLabels:
    def test_online_label(self):
        d = DS.parse_devices(SAMPLE)
        assert DS.format_sim_label("Airtel Pulse", d["350374"]) == "Airtel Pulse — online 87%"

    def test_offline_label(self):
        d = DS.parse_devices(SAMPLE)
        assert DS.format_sim_label("Voda Restrict 1", d["338826"]) == \
            "Voda Restrict 1 — OFFLINE 42%"

    def test_unknown_falls_back_to_bare_name(self):
        assert DS.format_sim_label("Airtel 2", None) == "Airtel 2"
        assert DS.format_sim_label("X", {"online": None, "battery": None}) == "X"

    def test_label_to_name_round_trip(self):
        d = DS.parse_devices(SAMPLE)
        for nm in ("Airtel Pulse", "Voda Restrict 1", "Airtel 2"):
            assert DS.label_to_name(DS.format_sim_label(nm, d["350374"])) == nm

    def test_sim_id_lookup_tolerates_decorated_label(self):
        from fleetx_toolkit.config import sim_id_for_name
        assert sim_id_for_name("Airtel Pulse — online 87%") == "350374"
        assert sim_id_for_name("Airtel Pulse") == "350374"

    def test_is_offline_only_when_known(self):
        d = DS.parse_devices(SAMPLE)
        assert DS.is_offline(d["338826"])
        assert not DS.is_offline(d["350374"])
        assert not DS.is_offline(None)
        assert not DS.is_offline(d["weird"])       # unknown != offline


class TestCache:
    def test_ttl_expiry(self):
        c = DS.DeviceStatusCache(ttl=1)
        assert c.is_stale()
        c.set(DS.parse_devices(SAMPLE))
        assert not c.is_stale()
        assert c.get("350374")["battery"] == 87
        time.sleep(1.05)
        assert c.is_stale()

    def test_no_token_does_not_fetch(self):
        c = DS.DeviceStatusCache()
        assert c.refresh_async("") is False

    def test_fresh_cache_skips_fetch(self):
        c = DS.DeviceStatusCache(ttl=60)
        c.set(DS.parse_devices(SAMPLE))
        assert c.refresh_async("TOKEN") is False    # still fresh -> no network

    def test_get_unknown_device(self):
        c = DS.DeviceStatusCache()
        assert c.get("does-not-exist") is None
