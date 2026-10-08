"""Audit acceptance tests 19 and 20: no irrelevant capabilities/actions per category, and every
setting/parameter has a label, unit/help and validated range."""

import pytest

from ems.core.config import EMSConfig, settings_schema
from ems.core.models import ACTION_CAPABILITY, DeviceCategory
from ems.devices.capabilities import PARAM_SCHEMAS, TYPE_SCHEMAS, actions_for, type_capabilities
from ems.devices.registry import registry


@pytest.mark.parametrize("cat", list(DeviceCategory))
def test_19_every_category_offers_only_its_own_actions(cat):
    caps = type_capabilities(cat)
    acts = actions_for(cat, caps, {}, None)
    assert all(ACTION_CAPABILITY[__import__("ems.core.models", fromlist=["CommandAction"]).CommandAction(a.action)] in caps
               for a in acts)
    names = {a.action for a in acts}
    if cat in (DeviceCategory.HEAT_PUMP, DeviceCategory.HEAT_PUMP_BOILER, DeviceCategory.SMART_METER,
               DeviceCategory.ENERGY_METER, DeviceCategory.PV_INVERTER):
        assert not any(n.startswith(("battery", "ev_")) for n in names), (cat, names)
    if cat in (DeviceCategory.SMART_METER, DeviceCategory.ENERGY_METER):
        assert names == set(), names                         # meters are never controllable
    assert TYPE_SCHEMAS[cat].label


def test_19_every_driver_capability_belongs_to_its_device_types():
    for cls in registry.list():
        m = cls.manifest
        allowed = set().union(*(type_capabilities(c) for c in m.categories)) if m.categories else set()
        if m.driver_id == "node.remote":                     # proxies whatever the owner node reports
            continue
        extra = set(m.capabilities) - allowed
        assert not extra, (m.driver_id, sorted(c.value for c in extra))


def test_20_settings_have_labels_and_ranges():
    defs = settings_schema()["$defs"]
    skip = {"DeviceConfig"}                                  # edited through the device form/schema
    for name, d in defs.items():
        if name in skip:
            continue
        for key, p in d.get("properties", {}).items():
            assert "label_nl" in p, f"{name}.{key} heeft geen label"
            base = next((a for a in p.get("anyOf", []) if a.get("type") != "null"), p)
            if base.get("type") in ("number", "integer") and "enum" not in base:
                assert any(k in base or k in p for k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")), \
                    f"{name}.{key} heeft geen bereik"


def test_20_device_parameters_have_ranges_units_and_safe_defaults():
    for cat, specs in PARAM_SCHEMAS.items():
        for p in specs:
            if p.kind == "number":
                assert p.min is not None and p.max is not None and p.min < p.max, (cat, p.key)
                assert p.unit, (cat, p.key)
                if p.recommended is not None:
                    assert p.min <= p.recommended <= p.max, (cat, p.key)


def test_20_defaults_are_safe():
    cfg = EMSConfig()
    assert cfg.runtime.mode == "production" and cfg.devices == []
    from ems.core.config import DeviceConfig
    real = DeviceConfig(id="x", name="x", category="battery", driver="generic.modbus_tcp")
    assert real.control_level == "read_only"                 # a real device never writes by default
    demo = DeviceConfig(id="y", name="y", category="battery", driver="mock.battery")
    assert demo.control_level == "full"                      # simulated Demo devices only
