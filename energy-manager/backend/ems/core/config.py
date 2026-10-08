"""Typed configuration of an EMS installation.

* Loaded from YAML; secrets are referenced as ``${ENV_VAR}`` or
  ``${ENV_VAR:-default}`` and never stored in the file itself.
* Every user-facing field carries UI metadata (Dutch label, help text,
  SIMPLE/ADVANCED/EXPERT level, unit) via ``json_schema_extra`` so the
  Windows/web app can render settings forms from the JSON schema.
* Nothing hardware specific is hard-coded: devices, grid connection and
  policies all come from here.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from ems.core.models import DeviceCategory


def setting(default: Any, *, label: str, help: str = "", level: str = "simple",
            unit: str | None = None, **kwargs: Any) -> Any:
    """Field with UI metadata. level: simple | advanced | expert."""
    extra = {"label_nl": label, "help_nl": help, "level": level}
    if unit:
        extra["unit"] = unit
    return Field(default, json_schema_extra=extra, **kwargs)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RuntimeConfig(_Base):
    mode: Literal["production", "demo"] = setting(
        "production", label="Bedrijfsmodus", level="advanced",
        help="'demo' draait een complete gesimuleerde woning; 'production' werkt alleen met echte apparaten.")
    demo_speed: float = setting(1.0, label="Demo-snelheid", level="expert", gt=0, le=120,
                                help="1 = realtime; hoger = versneld afspelen van de demowoning.")
    simulation_mode: bool = setting(
        True, label="Simulatiemodus",
        help="Het EMS rekent volledig mee maar stuurt alleen gesimuleerde apparaten aan.",
        level="advanced")
    dry_run: bool = setting(
        False, label="Proefdraaien (dry-run)",
        help="Beslissingen worden gelogd ('EMS zou ...') maar nooit uitgevoerd.",
        level="advanced")
    data_dir: Path = setting(Path("data"), label="Datamap", level="expert")
    log_level: str = setting("INFO", label="Logniveau", level="expert")
    log_json: bool = setting(True, label="JSON-logging", level="expert")


class SiteConfig(_Base):
    id: str = setting("home", label="Locatie-ID", level="expert", pattern=r"^[a-z0-9_-]+$")
    name: str = setting("Home", label="Naam locatie")
    latitude: float = setting(52.09, label="Breedtegraad", level="advanced", ge=-90, le=90)
    longitude: float = setting(5.12, label="Lengtegraad", level="advanced", ge=-180, le=180)
    timezone: str = setting("Europe/Amsterdam", label="Tijdzone", level="advanced")
    annual_consumption_kwh: float = setting(
        3500.0, label="Jaarverbruik huishouden", unit="kWh", level="advanced", gt=0,
        help="Startwaarde voor de verbruiksprognose zolang er nog weinig historie is.")


class GridConfig(_Base):
    phases: Literal[1, 3] = setting(3, label="Aantal fasen")
    ampere_per_phase: float = setting(25.0, label="Aansluitwaarde per fase", unit="A", gt=0, le=250)
    voltage_v: float = setting(230.0, label="Nominale spanning", unit="V", level="expert")
    max_import_kw: float | None = setting(
        None, label="Maximale afname", unit="kW", level="advanced",
        help="Leeg = afgeleid van de aansluitwaarde.")
    max_export_kw: float | None = setting(
        None, label="Maximale teruglevering", unit="kW", level="advanced",
        help="Leeg = afgeleid van de aansluitwaarde.")
    phase_safety_margin_pct: float = setting(
        5.0, label="Veiligheidsmarge per fase", unit="%", level="advanced", ge=0, le=50,
        help="Het EMS houdt de fasestroom zoveel procent onder de zekeringwaarde.")

    @property
    def connection_kw(self) -> float:
        return self.phases * self.ampere_per_phase * self.voltage_v / 1000.0

    @property
    def effective_max_import_kw(self) -> float:
        return self.max_import_kw if self.max_import_kw is not None else self.connection_kw

    @property
    def effective_max_export_kw(self) -> float:
        return self.max_export_kw if self.max_export_kw is not None else self.connection_kw

    @property
    def max_phase_current_a(self) -> float:
        return self.ampere_per_phase * (1.0 - self.phase_safety_margin_pct / 100.0)


class ControlConfig(_Base):
    interval_s: float = setting(10.0, label="Regelinterval", unit="s", level="expert", ge=1, le=300)
    device_timeout_s: float = setting(5.0, label="Apparaat-timeout", unit="s", level="expert", gt=0)
    grid_stale_after_s: float = setting(
        30.0, label="Netmeting verouderd na", unit="s", level="expert", gt=0,
        help="Zonder verse netmeting gaat het EMS naar de veilige modus.")
    frozen_after_s: float = setting(
        300.0, label="Bevroren meetwaarde na", unit="s", level="expert", gt=0)
    command_refresh_s: float = setting(
        300.0, label="Commando opnieuw sturen na", unit="s", level="expert", gt=0)
    failsafe_recover_ticks: int = setting(3, label="Herstel na gezonde cycli", level="expert", ge=1)
    reconnect_backoff_s: float = setting(10.0, label="Herverbinden na", unit="s", level="expert", gt=0)
    min_command_interval_s: float = setting(
        5.0, label="Minimale tijd tussen stuurwijzigingen", unit="s", level="expert", ge=0,
        help="Beschermt apparaten tegen te snel wisselende opdrachten (veiligheidscontrole).")


class OptimizerConfig(_Base):
    interval_minutes: int = setting(5, label="Herberekenen elke", unit="min", level="advanced", ge=1)
    horizon_hours: int = setting(36, label="Planningshorizon", unit="uur", level="advanced", ge=1, le=72)
    time_step_minutes: Literal[5, 15, 30, 60] = setting(15, label="Tijdstap", unit="min", level="expert")


class BatteryWearMode(StrEnum):
    BATTERY_SAVER = "battery_saver"
    BALANCED = "balanced"
    PROFIT = "profit"
    AGGRESSIVE = "aggressive"


class BatteryPolicy(_Base):
    min_soc: float = setting(10.0, label="Minimale laadtoestand", unit="%", ge=0, le=100)
    max_soc: float = setting(95.0, label="Maximale laadtoestand", unit="%", ge=0, le=100)
    reserve_soc: float = setting(
        15.0, label="Noodstroomreserve", unit="%", ge=0, le=100,
        help="Deze lading blijft altijd bewaard, bijvoorbeeld voor back-up.")
    degradation_cost_per_kwh: float = setting(
        0.04, label="Batterijslijtage", unit="EUR/kWh", level="advanced", ge=0,
        help="Geschatte slijtagekosten per kWh doorvoer.")
    min_arbitrage_spread_eur: float = setting(
        0.10, label="Minimaal prijsverschil voor handel", unit="EUR/kWh", level="advanced", ge=0)
    max_cycles_per_day: float = setting(1.5, label="Max. cycli per dag", level="advanced", gt=0)
    grid_charging_allowed: bool = setting(True, label="Laden vanaf het net toegestaan")
    grid_export_allowed: bool = setting(True, label="Ontladen naar het net toegestaan")
    wear_mode: BatteryWearMode = setting(BatteryWearMode.BALANCED, label="Batterij sparen")

    @model_validator(mode="after")
    def _ordering(self) -> BatteryPolicy:
        if not self.min_soc <= self.reserve_soc <= self.max_soc:
            raise ValueError("vereist: min_soc <= reserve_soc <= max_soc")
        return self


class HeatPumpPolicy(_Base):
    comfort_temperature: float = setting(21.0, label="Comforttemperatuur", unit="°C", ge=5, le=30)
    min_temperature: float = setting(20.5, label="Minimum bij dure stroom", unit="°C", ge=5, le=30)
    max_preheat_temperature: float = setting(22.0, label="Maximaal voorverwarmen", unit="°C", ge=5, le=30)

    @model_validator(mode="after")
    def _ordering(self) -> HeatPumpPolicy:
        if not self.min_temperature <= self.comfort_temperature <= self.max_preheat_temperature:
            raise ValueError("vereist: min_temperature <= comfort_temperature <= max_preheat_temperature")
        return self


class StrategyProfile(StrEnum):
    LOWEST_COST = "lowest_cost"
    MAXIMUM_PROFIT = "maximum_profit"
    MAXIMUM_SELF_CONSUMPTION = "maximum_self_consumption"
    BALANCED = "balanced"
    ZERO_EXPORT = "zero_export"
    BATTERY_SAVER = "battery_saver"
    PEAK_SHAVING = "peak_shaving"
    COMFORT = "comfort"
    ECO = "eco"
    BACKUP_PRIORITY = "backup_priority"
    CUSTOM = "custom"


class ExportMode(StrEnum):
    UNLIMITED = "unlimited"
    SMART = "smart"
    ZERO = "zero"


SurplusSink = Literal["battery", "flexible", "heat_pump", "boiler", "ev"]


class StrategyConfig(_Base):
    profile: StrategyProfile = setting(StrategyProfile.MAXIMUM_SELF_CONSUMPTION, label="EMS-strategie")
    export_mode: ExportMode = setting(ExportMode.UNLIMITED, label="Teruglevering")
    export_target_w: float = setting(0.0, label="Gewenste export bij begrenzing", unit="W", level="advanced")
    export_tolerance_w: float = setting(100.0, label="Regelmarge", unit="W", level="advanced", ge=0)
    export_price_threshold_eur: float = setting(
        0.0, label="Teruglevering beperken onder", unit="EUR/kWh", level="advanced",
        help="Smart Export: PV wordt afgeregeld zodra de terugleverprijs onder deze grens komt.")
    peak_limit_kw: float | None = setting(
        None, label="Piekbegrenzing afname", unit="kW", level="advanced",
        help="Peak shaving: boven deze afname zet het EMS batterij en flexibele lasten in. Leeg = uit.")
    surplus_priority: list[SurplusSink] = setting(
        ["battery", "flexible", "heat_pump", "boiler", "ev"],
        label="Volgorde PV-overschot", level="advanced",
        help="Na het woningverbruik gaat overschot in deze volgorde; als laatste wordt PV afgeregeld.")

    @model_validator(mode="after")
    def _unique(self) -> StrategyConfig:
        if len(set(self.surplus_priority)) != len(self.surplus_priority):
            raise ValueError("surplus_priority bevat dubbele waarden")
        return self



class ContractType(StrEnum):
    DYNAMIC = "dynamic"
    FIXED = "fixed"
    VARIABLE = "variable"


class TimeOfUsePrice(_Base):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")      # local time, inclusive
    end: str = Field(pattern=r"^\d{2}:\d{2}$")        # local time, exclusive ("00:00" = midnight)
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    price_eur_kwh: float


class TariffConfig(_Base):
    """The user's own energy contract — nothing supplier specific is hard-coded."""

    contract_name: str = setting("Mijn contract", label="Contractnaam")
    supplier: str = setting("", label="Leverancier")
    contract_type: ContractType = setting(ContractType.DYNAMIC, label="Soort contract")
    # Import (afname)
    import_markup_eur_kwh: float = setting(0.02, label="Inkoopopslag", unit="EUR/kWh",
                                           help="Opslag van de leverancier bovenop de marktprijs (excl. btw).")
    energy_tax_mode: Literal["table", "manual"] = setting(
        "manual", label="Energiebelasting bepalen", level="advanced",
        help="'table': per jaar uit de belastingtabel van het land (historie rekent met het tarief van dat jaar). "
             "'manual': het tarief hieronder.")
    tax_country: str = setting("NL", label="Land voor belastingtabel", level="expert")
    energy_tax_eur_kwh: float = setting(0.0, label="Energiebelasting", unit="EUR/kWh",
                                        help="Excl. btw. Vul het actuele tarief van uw contract in.")
    import_other_eur_kwh: float = setting(0.0, label="Overige kosten per kWh", unit="EUR/kWh", level="advanced")
    transaction_fee_eur_kwh: float = setting(0.0, label="Transactiekosten per kWh", unit="EUR/kWh", level="advanced")
    vat_pct: float = setting(21.0, label="Btw", unit="%", ge=0, le=100)
    fixed_import_price_eur_kwh: float | None = setting(
        None, label="Vaste leveringsprijs (incl. alles)", unit="EUR/kWh",
        help="Alleen voor vaste contracten; overschrijft de berekening hierboven.")
    time_of_use: list[TimeOfUsePrice] = setting([], label="Tijdsafhankelijke prijzen", level="expert")
    # Export (teruglevering)
    export_markup_eur_kwh: float = setting(-0.02, label="Terugleveropslag/-afslag", unit="EUR/kWh",
                                           help="Negatief = afslag op de marktprijs.")
    export_fee_eur_kwh: float = setting(0.0, label="Terugleverkosten", unit="EUR/kWh")
    export_vat: bool = setting(False, label="Btw over teruglevering", level="advanced")
    netting: bool = setting(False, label="Salderen",
                            help="Teruggeleverde kWh worden verrekend tegen de afnameprijs (zolang export < import).")
    fixed_export_price_eur_kwh: float | None = setting(None, label="Vaste terugleververgoeding", unit="EUR/kWh")
    # Fixed costs
    fixed_monthly_eur: float = setting(0.0, label="Vaste kosten per maand", unit="EUR", level="advanced")
    fixed_daily_eur: float = setting(0.0, label="Vaste kosten per dag", unit="EUR", level="advanced")
    grid_monthly_eur: float = setting(0.0, label="Netbeheerkosten per maand", unit="EUR", level="advanced")
    service_monthly_eur: float = setting(0.0, label="Servicekosten per maand", unit="EUR", level="advanced")
    energy_tax_credit_yearly_eur: float = setting(0.0, label="Vermindering energiebelasting per jaar",
                                                  unit="EUR", level="advanced")


class PriceConfig(_Base):
    provider: Literal["none", "energyzero", "entsoe", "manual", "demo"] = setting(
        "none", label="Prijsbron",
        help="EnergyZero (Nederland, geen token nodig), ENTSO-E (gratis API-token nodig), "
             "handmatig ingevoerde prijzen, of de demo-prijzen.")
    energyzero_interval: Literal["quarter", "hour"] = setting(
        "quarter", label="EnergyZero: prijzen per",
        help="Kwartierprijzen (nauwkeuriger, sinds de 15-minuten-markt) of uurprijzen.")
    entsoe_token: str = setting("", label="ENTSO-E API-token", level="simple",
                                help="Alleen nodig bij prijsbron ENTSO-E. Wordt versleuteld opgeslagen.")
    bidding_zone: str = setting("10YNL----------L", label="Biedzone (EIC)", level="expert")
    refresh_minutes: int = setting(60, label="Verversen elke", unit="min", level="expert", ge=5)


class ForecastConfig(_Base):
    weather_provider: Literal["none", "open_meteo", "demo"] = setting(
        "none", label="Weerbron",
        help="Open-Meteo (gratis, geen sleutel) voor zon- en temperatuurprognoses.")
    refresh_minutes: int = setting(60, label="Verversen elke", unit="min", level="expert", ge=10)
    history_days: int = setting(28, label="Historie voor verbruiksprognose", unit="dagen", level="expert", ge=3)


class NotificationConfig(_Base):
    webhook_url: str = setting("", label="Webhook-URL", level="advanced",
                               help="Optioneel: meldingen worden als JSON naar deze URL gestuurd.")
    extreme_price_eur_kwh: float = setting(0.40, label="Melding bij importprijs boven", unit="EUR/kWh",
                                           level="advanced")
    phase_load_warn_pct: float = setting(90.0, label="Melding bij fasebelasting boven", unit="%",
                                         level="advanced")


class NodeConfig(_Base):
    name: str = setting("", label="Naam van deze computer",
                        help="Zoals deze Energy Manager-node in het overzicht verschijnt. Leeg = computernaam.")
    role_preset: Literal["all_in_one", "controller", "gateway"] = setting(
        "all_in_one", label="Rol van deze node", level="advanced",
        help="'all_in_one': alles op deze computer (standaard). 'controller': regelt ook apparaten op "
             "gekoppelde nodes. 'gateway': alleen apparaten aansluiten; een andere node regelt.")
    advertise: bool = setting(True, label="Vindbaar voor andere Energy Manager-nodes (mDNS)", level="expert")
    lease_ttl_s: float = setting(30.0, label="Regelrecht verloopt na", unit="s", level="expert", ge=10, le=300,
                                 help="Zonder hartslag van de controller geeft een gateway zijn apparaten na deze "
                                      "tijd terug aan hun eigen regeling.")


class DeviceConfig(_Base):
    """One configured device. ``role='primary_grid_meter'`` marks the meter that is the
    truth at the grid connection (``grid_reference`` is accepted as legacy alias).
    ``control_level`` is the commissioning stage that limits what the EMS may write."""

    id: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    name: str
    category: DeviceCategory
    driver: str
    enabled: bool = True
    role: Literal["primary_grid_meter", "grid_reference"] | None = None
    control_level: Literal["connection_test", "read_only", "shadow", "limited", "full"] = "full"
    limited_fraction: float = Field(0.3, gt=0, le=1)
    phase: Literal["L1", "L2", "L3", "3P"] = "3P"
    connection: dict[str, Any] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)


class EMSConfig(_Base):
    version: int = 1
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    site: SiteConfig = Field(default_factory=SiteConfig)
    grid: GridConfig = Field(default_factory=GridConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    optimizer: OptimizerConfig = Field(default_factory=OptimizerConfig)
    battery: BatteryPolicy = Field(default_factory=BatteryPolicy)
    heatpump: HeatPumpPolicy = Field(default_factory=HeatPumpPolicy)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    tariff: TariffConfig = Field(default_factory=TariffConfig)
    prices: PriceConfig = Field(default_factory=PriceConfig)
    forecast: ForecastConfig = Field(default_factory=ForecastConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    node: NodeConfig = Field(default_factory=NodeConfig)
    devices: list[DeviceConfig] = Field(default_factory=list)
    # JSON paths whose value came from a ${VAR} reference: (path -> original text).
    _env_refs: dict[tuple, tuple[str, Any]] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _devices(self) -> EMSConfig:
        ids = [d.id for d in self.devices]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"dubbele apparaat-id's: {sorted(dupes)}")
        refs = [d.id for d in self.devices if d.role in ("grid_reference", "primary_grid_meter")]
        if len(refs) > 1:
            raise ValueError(f"maximaal één primaire netmeter (grid_reference) toegestaan, gevonden: {refs}")
        if self.grid.phases == 1:
            bad = [d.id for d in self.devices if d.phase in ("L2", "L3")]
            if bad:
                raise ValueError(f"1-fase aansluiting maar apparaten op L2/L3: {bad}")
        return self

    def device(self, device_id: str) -> DeviceConfig:
        for d in self.devices:
            if d.id == device_id:
                return d
        raise KeyError(device_id)

    def devices_of(self, *categories: DeviceCategory) -> list[DeviceConfig]:
        return [d for d in self.devices if d.enabled and d.category in categories]

    def grid_reference(self) -> DeviceConfig | None:
        """The meter whose reading is 'the truth' at the grid connection."""
        enabled = [d for d in self.devices if d.enabled]
        for d in enabled:
            if d.role in ("grid_reference", "primary_grid_meter"):
                return d
        for d in enabled:
            if d.category == DeviceCategory.SMART_METER:
                return d
        return None


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    pass


def _interpolate(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, str):
        def repl(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in env:
                return env[name]
            if default is not None:
                return default
            raise ConfigError(f"omgevingsvariabele ${{{name}}} is niet gezet")
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _interpolate(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v, env) for v in value]
    return value


_TRUE = {"1", "true", "yes", "on", "ja"}
_FALSE = {"0", "false", "no", "off", "nee"}


def _env_bool(env: dict[str, str], *names: str) -> bool | None:
    for name in names:
        raw = env.get(name)
        if raw is None:
            continue
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ConfigError(f"{name}={raw!r} is geen geldige boolean")
    return None


def _collect_refs(value: Any, env: dict[str, str], path: tuple = ()) -> dict[tuple, tuple[str, Any]]:
    refs: dict[tuple, tuple[str, Any]] = {}
    if isinstance(value, str) and _ENV_PATTERN.search(value):
        refs[path] = (value, _interpolate(value, env))
    elif isinstance(value, dict):
        for k, v in value.items():
            refs.update(_collect_refs(v, env, path + (k,)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            refs.update(_collect_refs(v, env, path + (i,)))
    return refs


def config_from_dict(data: dict[str, Any], env: dict[str, str] | None = None) -> EMSConfig:
    env = dict(os.environ) if env is None else env
    refs = _collect_refs(data or {}, env)
    data = _interpolate(data or {}, env)
    runtime = dict(data.get("runtime") or {})
    sim = _env_bool(env, "EMS_SIMULATION_MODE", "SIMULATION_MODE")
    dry = _env_bool(env, "EMS_DRY_RUN", "DRY_RUN")
    if sim is not None:
        runtime["simulation_mode"] = sim
    if dry is not None:
        runtime["dry_run"] = dry
    if env.get("EMS_MODE"):
        runtime["mode"] = env["EMS_MODE"].strip().lower()
    data = {**data, "runtime": runtime}
    try:
        cfg = EMSConfig.model_validate(data)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    cfg._env_refs = refs
    return cfg


def copy_config(config: EMSConfig, data: dict[str, Any] | None = None) -> EMSConfig:
    """Validated copy (optionally from modified data) that keeps the ${VAR} references."""
    new = EMSConfig.model_validate(data if data is not None else config.model_dump(mode="json"))
    new._env_refs = dict(config._env_refs)
    return new


def load_config(path: str | Path, env: dict[str, str] | None = None) -> EMSConfig:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: verwacht een YAML-mapping op het hoogste niveau")
    return config_from_dict(raw, env)


def dump_config(config: EMSConfig) -> str:
    """Serialize to YAML (round-trips through load_config)."""
    data = config.model_dump(mode="json", exclude_none=False)
    for path, (original, resolved) in config._env_refs.items():
        node = data
        try:
            for key in path[:-1]:
                node = node[key]
            if node[path[-1]] == resolved:   # unchanged -> write the reference, never the secret
                node[path[-1]] = original
        except (KeyError, IndexError, TypeError):
            continue
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def save_config(config: EMSConfig, path: str | Path) -> None:
    """Atomic write: never leaves a half-written config behind."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(dump_config(config), encoding="utf-8")
    os.replace(tmp, path)


def settings_schema() -> dict[str, Any]:
    """JSON schema incl. UI metadata, consumed by the frontend settings screens."""
    return EMSConfig.model_json_schema()
