from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ems.core.clock import SimulatedClock
from ems.core.config import EMSConfig, config_from_dict, load_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = ROOT / "config" / "ems.example.yaml"
TZ = ZoneInfo("Europe/Amsterdam")


def local(*args: int) -> datetime:
    return datetime(*args, tzinfo=TZ)


BASE_DEVICES = [
    {"id": "p1", "name": "Meter", "category": "smart_meter", "driver": "mock.smart_meter", "role": "grid_reference"},
    {"id": "pv", "name": "PV", "category": "pv_inverter", "driver": "mock.pv_inverter",
     "params": {"sim": {"peak_power_kw": 8.0}}},
    {"id": "bat", "name": "Batterij", "category": "battery", "driver": "mock.battery",
     "params": {"capacity_kwh": 10.0, "sim": {"soc_pct": 50}}},
    {"id": "hp", "name": "Warmtepomp", "category": "heat_pump", "driver": "mock.heat_pump", "phase": "L1"},
    {"id": "ev", "name": "Laadpaal", "category": "ev_charger", "driver": "mock.ev_charger",
     "params": {"charge_mode": "pv_only", "sim": {"arrive": "00:00", "depart": "23:59", "arrival_soc_pct": 20}}},
]


def make_config(devices: list[dict] | None = None, env: dict | None = None, **sections) -> EMSConfig:
    data = {"devices": BASE_DEVICES if devices is None else devices, **sections}
    return config_from_dict(data, env or {})


@pytest.fixture
def example_config() -> EMSConfig:
    return load_config(EXAMPLE_CONFIG, env={})


@pytest.fixture
def clock() -> SimulatedClock:
    return SimulatedClock(local(2026, 6, 15, 12, 0))


async def make_engine(config, start, controller=None, registry=None, prepare_site=None):
    """Engine wired to a simulated site, stepped manually by the test."""
    from ems.control.self_consumption import SelfConsumptionController
    from ems.core.engine import EMSEngine
    from ems.devices.base import DriverContext
    from ems.devices.manager import DeviceManager
    from ems.devices.registry import registry as default_registry
    from ems.simulator.builder import build_site

    clock = SimulatedClock(start)
    site = build_site(config, start)
    if prepare_site:
        prepare_site(site)
    site.advance(0)
    dm = DeviceManager(config, DriverContext(clock, simulator=site), registry or default_registry)
    engine = EMSEngine(config, dm, controller or SelfConsumptionController(), clock)
    await engine.start()
    return engine, site, clock


async def step(engine, site, clock, seconds: float = 10.0):
    result = await engine.tick()
    site.advance(seconds)
    clock.advance(seconds)
    return result
