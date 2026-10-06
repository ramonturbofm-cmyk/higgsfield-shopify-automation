from datetime import timedelta

from conftest import local
from ems.core.models import Metric
from ems.core.validation import FrozenValueDetector, validate_values


def test_validate_values():
    ok, bad = validate_values({
        Metric.BATTERY_SOC_PCT: 140.0,
        Metric.GRID_POWER_W: float("nan"),
        Metric.PV_POWER_W: "abc",
        Metric.EV_CONNECTED: True,
        Metric.HP_MODE: "eco",
        Metric.INDOOR_TEMP_C: 21,
        Metric.OUTDOOR_TEMP_C: "4.5",
        Metric.HP_POWER_W: True,
    })
    assert set(bad) == {Metric.BATTERY_SOC_PCT, Metric.GRID_POWER_W, Metric.PV_POWER_W, Metric.HP_POWER_W}
    assert ok == {Metric.EV_CONNECTED: True, Metric.HP_MODE: "eco", Metric.INDOOR_TEMP_C: 21.0,
                  Metric.OUTDOOR_TEMP_C: 4.5}


def test_frozen_detector_uses_full_fingerprint():
    det = FrozenValueDetector(frozen_after_s=60)
    t0 = local(2026, 1, 1, 0, 0)
    # Grid power constantly 0 W (battery balancing) but voltage moves: not frozen.
    for i in range(20):
        values = {Metric.GRID_POWER_W: 0.0, Metric.GRID_VOLTAGE_L1_V: 230.0 + (i % 3) * 0.1}
        assert not det.is_frozen("p1", values, t0 + timedelta(seconds=10 * i))
    # Everything identical for >= 60 s: frozen.
    same = {Metric.GRID_POWER_W: 512.0, Metric.GRID_VOLTAGE_L1_V: 231.0}
    assert not det.is_frozen("p1", same, t0)
    assert not det.is_frozen("p1", same, t0 + timedelta(seconds=59))
    assert det.is_frozen("p1", same, t0 + timedelta(seconds=60))
    det.reset("p1")
    assert not det.is_frozen("p1", same, t0 + timedelta(seconds=120))


def test_frozen_detector_ignores_devices_without_grid_power():
    det = FrozenValueDetector(frozen_after_s=1)
    t0 = local(2026, 1, 1)
    v = {Metric.PV_POWER_W: 0.0}
    assert not det.is_frozen("pv", v, t0)
    assert not det.is_frozen("pv", v, t0 + timedelta(hours=1))
