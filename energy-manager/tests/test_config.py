import pytest

from conftest import EXAMPLE_CONFIG, make_config
from ems.core.config import ConfigError, config_from_dict, load_config, settings_schema


def test_example_config_loads(example_config):
    assert example_config.site.id == "home"
    assert len(example_config.devices) == 6
    assert example_config.grid_reference().id == "p1"
    assert example_config.runtime.simulation_mode is True


def test_grid_derived_values():
    cfg = make_config(grid={"phases": 3, "ampere_per_phase": 25})
    assert cfg.grid.connection_kw == pytest.approx(17.25)
    assert cfg.grid.effective_max_import_kw == pytest.approx(17.25)
    assert cfg.grid.max_phase_current_a == pytest.approx(23.75)
    cfg = make_config(grid={"max_import_kw": 10})
    assert cfg.grid.effective_max_import_kw == 10


def test_env_interpolation_and_default():
    data = {"site": {"name": "${SITE_NAME}"}, "devices": [
        {"id": "x", "name": "x", "category": "smart_meter", "driver": "mock.smart_meter",
         "connection": {"password": "${METER_PW}", "port": "${METER_PORT:-502}"}}]}
    cfg = config_from_dict(data, {"SITE_NAME": "Vakantiehuis", "METER_PW": "geheim"})
    assert cfg.site.name == "Vakantiehuis"
    assert cfg.devices[0].connection == {"password": "geheim", "port": "502"}
    with pytest.raises(ConfigError, match="METER_PW"):
        config_from_dict(data, {"SITE_NAME": "x"})


@pytest.mark.parametrize("name", ["SIMULATION_MODE", "EMS_SIMULATION_MODE"])
def test_env_overrides_runtime(name):
    cfg = load_config(EXAMPLE_CONFIG, env={name: "false", "DRY_RUN": "true"})
    assert cfg.runtime.simulation_mode is False
    assert cfg.runtime.dry_run is True
    with pytest.raises(ConfigError):
        load_config(EXAMPLE_CONFIG, env={"DRY_RUN": "misschien"})


@pytest.mark.parametrize("sections, match", [
    ({"battery": {"min_soc": 30, "reserve_soc": 20, "max_soc": 90}}, "min_soc"),
    ({"heatpump": {"comfort_temperature": 19, "min_temperature": 20, "max_preheat_temperature": 22}}, "min_temperature"),
    ({"strategy": {"surplus_priority": ["ev", "ev"]}}, "dubbele"),
    ({"grid": {"ampere_per_phase": -1}}, "ampere_per_phase"),
])
def test_invalid_policies_rejected(sections, match):
    with pytest.raises(ConfigError, match=match):
        make_config(**sections)


def test_device_validation():
    dev = {"id": "a", "name": "a", "category": "smart_meter", "driver": "mock.smart_meter"}
    with pytest.raises(ConfigError, match="dubbele"):
        make_config([dev, dev])
    with pytest.raises(ConfigError, match="grid_reference"):
        make_config([{**dev, "role": "grid_reference"}, {**dev, "id": "b", "role": "grid_reference"}])
    with pytest.raises(ConfigError, match="L2/L3"):
        make_config([{**dev, "phase": "L2"}], grid={"phases": 1})
    with pytest.raises(ConfigError):
        make_config([{**dev, "id": "Bad ID!"}])
    with pytest.raises(ConfigError):
        make_config([{**dev, "unknown_field": 1}])


def test_settings_schema_has_ui_metadata():
    schema = settings_schema()
    min_soc = schema["$defs"]["BatteryPolicy"]["properties"]["min_soc"]
    assert min_soc["label_nl"] == "Minimale laadtoestand"
    assert min_soc["level"] == "simple"
    assert min_soc["unit"] == "%"
    degradation = schema["$defs"]["BatteryPolicy"]["properties"]["degradation_cost_per_kwh"]
    assert degradation["level"] == "advanced"


def test_save_keeps_env_references(tmp_path):
    from ems.core.config import save_config
    data = {"prices": {"provider": "entsoe", "entsoe_token": "${ENTSOE_TOKEN}"}, "devices": []}
    cfg = config_from_dict(data, {"ENTSOE_TOKEN": "super-secret"})
    assert cfg.prices.entsoe_token == "super-secret"
    path = tmp_path / "ems.yaml"
    save_config(cfg, path)
    text = path.read_text()
    assert "super-secret" not in text and "${ENTSOE_TOKEN}" in text
    again = load_config(path, env={"ENTSOE_TOKEN": "super-secret"})
    assert again.prices.entsoe_token == "super-secret"
