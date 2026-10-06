import pytest

from conftest import local, make_config
from ems.core.clock import SimulatedClock
from ems.core.models import Capability, Command, CommandAction, DeviceCategory
from ems.devices.base import DriverContext, UnsupportedCommandError
from ems.devices.registry import DriverRegistry, registry
from ems.integrations.mock import MockBattery, MockSmartMeter, MockSolarInverter
from ems.simulator.builder import build_site


def test_registry_discovers_mock_drivers():
    ids = {d.manifest.driver_id for d in registry.list()}
    assert {"mock.smart_meter", "mock.pv_inverter", "mock.battery", "mock.heat_pump", "mock.ev_charger"} <= ids
    battery = {d.manifest.driver_id for d in registry.list(DeviceCategory.BATTERY)}
    assert battery == {"mock.battery", "generic.modbus_tcp", "generic.http_json", "generic.mqtt"}
    with pytest.raises(KeyError, match="beschikbaar"):
        registry.get("vendor.does_not_exist")


def test_registry_rejects_conflicting_ids():
    reg = DriverRegistry()
    reg.register(MockBattery)
    reg.register(MockBattery)  # idempotent

    class Other(MockBattery):
        pass

    with pytest.raises(ValueError, match="already registered"):
        reg.register(Other)


def test_all_manifests_are_honest():
    for drv in registry.list():
        m = drv.manifest
        assert m.categories and m.capabilities
        if m.verified:
            assert m.documentation, f"{m.driver_id}: verified driver needs a documentation source"


def _driver(cls, device_id, cfg=None, site=True):
    cfg = cfg or make_config()
    clock = SimulatedClock(local(2026, 6, 1, 12))
    sim = build_site(cfg, clock.now()) if site else None
    if sim:
        sim.advance(0)
    return cls(cfg.device(device_id), DriverContext(clock, simulator=sim))


async def test_self_test_report_lists_capabilities():
    report = await _driver(MockBattery, "bat").self_test()
    assert report.reachable
    text = report.render()
    assert "✓ apparaat bereikbaar" in text
    assert "✓ batterij-SOC uitleesbaar" in text
    assert "✓ batterijbesturing beschikbaar" in text
    assert not report.unavailable


async def test_self_test_shows_missing_controls():
    class ReadOnlyPV(MockSolarInverter):
        manifest = MockSolarInverter.manifest.__class__(
            **{**MockSolarInverter.manifest.__dict__, "driver_id": "test.readonly_pv",
               "capabilities": frozenset({Capability.READ_PV_POWER})})

    report = await _driver(ReadOnlyPV, "pv").self_test()
    assert report.reachable
    assert Capability.CONTROL_PV_LIMIT.value in report.unavailable
    assert "✗ vermogensbegrenzing beschikbaar" in report.render()


async def test_self_test_unreachable_without_simulator():
    report = await _driver(MockSmartMeter, "p1", site=False).self_test()
    assert not report.reachable
    assert report.unavailable == ["reachable"]


async def test_unsupported_command_rejected():
    drv = _driver(MockSmartMeter, "p1")
    await drv.connect()
    with pytest.raises(UnsupportedCommandError):
        await drv.apply(Command("p1", CommandAction.BATTERY_CHARGE, 1000))
