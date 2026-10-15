"""SensorType update via read-modify-write (v3.19).

PATCH /api/v1/vehicles/{id} requires the FULL vehicle object. Sending only
{"id","sensorType"} was answered with 403, so the tab now GETs the vehicle,
changes one field, and PATCHes the complete object back.
"""
import pytest

from fleetx_toolkit import logic as L

VEHICLE = {
    "id": 2367151, "licensePlate": "KA03NV3646", "name": "3646",
    "accountId": 16058, "sensorType": "OLD_SENSOR", "sos": 1,
    "kmRun": 18180.77, "immobiliser": False,
    "fuelType": {"id": 5, "name": "Electric", "measurementUnit": "FULL_CHARGE"},
    "vehicleModel": {"id": 1800, "name": "NEXON EV XZ+"},
    "tags": [{"id": 52750, "name": "Microsoft BLR"}],
    "vehicleStatus": {"id": 94569, "name": "Active"},
    "lastDeviceId": "353742371106097",
}
NEW = "TATA_EXPRESS_T_EV_WITH_ZIPTRON_BATTERY_TFT100_SOS_AIN2"


class TestApplySensorType:
    def test_sets_the_new_type(self):
        out, prev = L.apply_sensor_type(VEHICLE, NEW)
        assert out["sensorType"] == NEW and prev == "OLD_SENSOR"

    def test_every_other_field_is_preserved(self):
        out, _ = L.apply_sensor_type(VEHICLE, NEW)
        for k, v in VEHICLE.items():
            if k != "sensorType":
                assert out[k] == v, f"{k} was altered"

    def test_nested_objects_survive(self):
        out, _ = L.apply_sensor_type(VEHICLE, NEW)
        assert out["tags"][0]["name"] == "Microsoft BLR"
        assert out["vehicleModel"]["name"] == "NEXON EV XZ+"
        assert out["fuelType"]["measurementUnit"] == "FULL_CHARGE"

    def test_body_is_not_a_two_field_stub(self):
        """The 403 came from a 2-field body; the new one is the whole object."""
        out, _ = L.apply_sensor_type(VEHICLE, NEW)
        assert len(out) == len(VEHICLE) >= 10

    def test_original_is_not_mutated(self):
        L.apply_sensor_type(VEHICLE, NEW)
        assert VEHICLE["sensorType"] == "OLD_SENSOR"

    def test_missing_sensor_type_key(self):
        out, prev = L.apply_sensor_type({"id": 1}, NEW)
        assert prev is None and out["sensorType"] == NEW

    def test_bad_payload_rejected(self):
        with pytest.raises(ValueError):
            L.apply_sensor_type("not a dict", NEW)
        with pytest.raises(ValueError):
            L.apply_sensor_type(None, NEW)


class TestSkipLogic:
    def test_update_needed_when_different(self):
        assert L.sensor_update_needed(VEHICLE, NEW)

    def test_not_needed_when_already_set(self):
        assert not L.sensor_update_needed(dict(VEHICLE, sensorType=NEW), NEW)

    def test_missing_key_counts_as_needed(self):
        assert L.sensor_update_needed({"id": 1}, NEW)
        assert L.sensor_update_needed(None, NEW)


class TestVehicleLabel:
    def test_prefers_plate(self):
        assert L.vehicle_label(VEHICLE) == "KA03NV3646"

    def test_falls_back_to_name_then_id(self):
        assert L.vehicle_label({"name": "3646", "id": 9}) == "3646"
        assert L.vehicle_label({"id": 9}) == "9"
        assert L.vehicle_label({}) == "?"
