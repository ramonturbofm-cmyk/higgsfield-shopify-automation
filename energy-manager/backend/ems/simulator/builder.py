"""Build a SimulatedSite from an EMSConfig (mock devices only)."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from typing import Any

from ems.core.config import EMSConfig
from ems.core.models import DeviceCategory
from ems.simulator.components import SimBaseLoad, SimBattery, SimEVCharger, SimHeatPump, SimPV
from ems.simulator.environment import Environment
from ems.simulator.site import SimulatedSite

_CATEGORY_COMPONENT = {
    DeviceCategory.PV_INVERTER: SimPV,
    DeviceCategory.BATTERY: SimBattery,
    DeviceCategory.HEAT_PUMP: SimHeatPump,
    DeviceCategory.EV_CHARGER: SimEVCharger,
}


def _kwargs(cls: type, params: dict[str, Any]) -> dict[str, Any]:
    allowed = {f.name for f in fields(cls) if not f.name.startswith("_")} - {"id", "phase"}
    unknown = set(params) - allowed
    if unknown:
        raise ValueError(f"{cls.__name__}: onbekende simulatieparameters {sorted(unknown)}")
    return dict(params)


def build_site(config: EMSConfig, start: datetime, seed: int = 1,
               base_load_kwh: float = 3500.0) -> SimulatedSite:
    env = Environment(config.site.latitude, config.site.longitude, config.site.timezone, seed=seed)
    site = SimulatedSite(
        env=env, start=start,
        grid_phases=config.grid.phases,
        ampere_per_phase=config.grid.ampere_per_phase,
        voltage_v=config.grid.voltage_v,
        max_import_w=config.grid.effective_max_import_kw * 1000,
        max_export_w=config.grid.effective_max_export_kw * 1000,
    )
    site.add(SimBaseLoad(id="__base_load__", annual_kwh=base_load_kwh, seed=seed))
    for dev in config.devices:
        if not dev.enabled or not dev.driver.startswith("mock."):
            continue
        if dev.category == DeviceCategory.SMART_METER:
            site.meter_id = dev.id
            continue
        cls = _CATEGORY_COMPONENT.get(dev.category)
        if cls is None:
            raise ValueError(f"geen simulatiemodel voor categorie {dev.category}")
        params = _kwargs(cls, dev.params.get("sim", {}))
        if cls is SimBattery:
            params.setdefault("capacity_kwh", dev.params.get("capacity_kwh", 10.0))
            params.setdefault("auto_min_soc_pct", config.battery.min_soc)
        if cls is SimHeatPump:
            params.setdefault("comfort_temperature", config.heatpump.comfort_temperature)
            params.setdefault("indoor_temp_c", config.heatpump.comfort_temperature)
        if cls is SimEVCharger:
            params.setdefault("phases", 3 if dev.phase == "3P" and config.grid.phases == 3 else 1)
            params.setdefault("voltage_v", config.grid.voltage_v)
        site.add(cls(id=dev.id, phase=dev.phase, **params))
    return site
