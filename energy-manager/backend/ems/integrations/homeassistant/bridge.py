"""Home Assistant as an optional local bridge (Home Assistant REST API documentation:
``GET /api/``, ``GET /api/states``, ``GET /api/states/<entity_id>``, ``POST /api/services/<domain>/<service>``,
authentication with a long-lived access token as ``Authorization: Bearer``).

Values are mapped per EMS metric to an entity; units are converted (kW->W, Wh->kWh). Controls call
documented services (switch.turn_on/off, number.set_value, climate/water_heater.set_temperature,
select.select_option) — only when the EMS engine sends a command, i.e. after can_execute and the
SafetyValidator. Controlling a device through Home Assistant requires the installer to confirm that the
same device is not controlled directly by another driver (one control owner per device).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ems.core.models import Capability, Command, CommandAction, DeviceCategory, Metric
from ems.devices.base import DeviceDriver, DeviceUnavailableError, DriverError, DriverManifest, UnsupportedCommandError
from ems.devices.registry import register_driver
from ems.integrations.generic.mapping import capabilities_for, parse_entries, resolve_secret

HA_DOC = "Home Assistant REST API (developers.home-assistant.io/docs/api/rest)"
POWER_UNITS = {"W": 1.0, "kW": 1000.0, "MW": 1e6}
ENERGY_UNITS = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}
SERVICES = {  # action -> {entity domain: (service, payload key)}
    CommandAction.SWITCH: {"switch": ("turn_{onoff}", None), "input_boolean": ("turn_{onoff}", None)},
    CommandAction.EV_CURRENT: {"number": ("set_value", "value"), "input_number": ("set_value", "value")},
    CommandAction.PV_LIMIT: {"number": ("set_value", "value"), "input_number": ("set_value", "value")},
    CommandAction.TEMP_SETPOINT: {"climate": ("set_temperature", "temperature"),
                                  "water_heater": ("set_temperature", "temperature"),
                                  "number": ("set_value", "value")},
    CommandAction.HP_MODE: {"select": ("select_option", "option"), "input_select": ("select_option", "option")},
}


class HAClient:
    def __init__(self, url: str, token: str | None, timeout: float = 6.0, transport=None) -> None:
        if not url:
            raise DriverError("geen Home Assistant-adres ingesteld")
        self.url, self.token, self.timeout, self.transport = url.rstrip("/"), token, timeout, transport

    def _c(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return httpx.AsyncClient(base_url=self.url, headers=headers, timeout=self.timeout, follow_redirects=False,
                                 transport=self.transport)

    async def _req(self, method: str, path: str, **kw):
        try:
            async with self._c() as c:
                r = await c.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise DeviceUnavailableError(f"Home Assistant niet bereikbaar ({type(exc).__name__})") from exc
        if r.status_code == 401:
            raise DriverError("Home Assistant: token ongeldig (maak een 'long-lived access token' in uw profiel)")
        if r.status_code >= 400:
            raise DriverError(f"Home Assistant: HTTP {r.status_code}")
        return r.json() if r.content else None

    async def ping(self) -> dict:
        return await self._req("GET", "/api/")

    async def states(self) -> list[dict]:
        return await self._req("GET", "/api/states")

    async def state(self, entity_id: str) -> dict:
        return await self._req("GET", f"/api/states/{entity_id}")

    async def call(self, domain: str, service: str, data: dict) -> Any:
        return await self._req("POST", f"/api/services/{domain}/{service}", json=data)


def suggest(states: list[dict]) -> list[dict]:
    """Entities that look useful for the EMS (power/energy/battery/temperature/switch/number/climate)."""
    out = []
    for s in states:
        eid, attrs = s.get("entity_id", ""), s.get("attributes") or {}
        dom, unit, dc = eid.split(".")[0], attrs.get("unit_of_measurement"), attrs.get("device_class")
        kind = None
        if dom == "sensor" and unit in POWER_UNITS:
            kind = "power"
        elif dom == "sensor" and unit in ENERGY_UNITS:
            kind = "energy"
        elif dom == "sensor" and dc == "battery" and unit == "%":
            kind = "soc"
        elif dom == "sensor" and dc == "temperature":
            kind = "temperature"
        elif dom in ("switch", "input_boolean", "number", "input_number", "climate", "water_heater", "select"):
            kind = dom
        if kind:
            out.append({"entity_id": eid, "name": attrs.get("friendly_name", eid), "kind": kind, "unit": unit,
                        "device_class": dc, "state": s.get("state")})
    return out


@register_driver
class HomeAssistantDriver(DeviceDriver):
    manifest = DriverManifest(
        driver_id="homeassistant.entities",
        display_name="Via Home Assistant (lokale bridge)",
        vendor="Home Assistant",
        categories=tuple(DeviceCategory),
        capabilities=frozenset(),
        connection_types=("http",),
        documentation=HA_DOC,
        write_capable=True,
        notes="Gebruikt entiteiten uit uw eigen Home Assistant. Bestuur een apparaat niet tegelijk rechtstreeks én via "
              "Home Assistant.",
        connection_schema={
            "url": {"type": "string", "default": "http://homeassistant.local:8123", "label_nl": "Home Assistant-adres"},
            "token": {"type": "string", "default": "", "label_nl": "Long-lived access token", "secret": True},
            "values": {"type": "array", "label_nl": "Meetwaarden: [{metric, entity_id}]", "level": "advanced"},
            "controls": {"type": "array", "label_nl": "Bediening: [{action, entity_id, options?}]", "level": "advanced"},
            "exclusive_control_confirmed": {"type": "boolean", "default": False,
                                            "label_nl": "Ik bevestig: dit apparaat wordt niet ook rechtstreeks bestuurd"},
        },
    )
    transport = None

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        c = config.connection
        self.client = HAClient(str(c.get("url", "")), resolve_secret(c.get("token"), context.secrets),
                               transport=self.transport)
        self.diag: dict[str, Any] = {}

    def _entries(self) -> list[dict]:
        try:
            return parse_entries(self.config.connection.get("values"), ("entity_id",))
        except DriverError:
            return []

    def _controls(self) -> dict[CommandAction, dict]:
        out = {}
        if not self.config.connection.get("exclusive_control_confirmed"):
            return out
        for c in self.config.connection.get("controls") or []:
            try:
                action = CommandAction(c.get("action"))
            except ValueError:
                continue
            dom = str(c.get("entity_id", "")).split(".")[0]
            if dom in SERVICES.get(action, {}):
                out[action] = c
        return out

    def capabilities(self) -> frozenset[Capability]:
        from ems.core.models import ACTION_CAPABILITY
        caps = set(capabilities_for(self._entries())) if self._entries() else set()
        caps |= {ACTION_CAPABILITY[a] for a in self._controls()}
        return frozenset(caps)

    def write_documented(self) -> bool:
        return bool(self._controls())

    def hardware_verified(self) -> bool:
        return False

    def simulated_target(self) -> bool:
        """True only for the virtual Home Assistant of the Testomgeving on this computer."""
        from urllib.parse import urlsplit
        host = urlsplit(str(self.config.connection.get("url", ""))).hostname or ""
        return host in ("127.0.0.1", "localhost") and getattr(self.context.simulator, "lab", None) is not None

    def documentation_ref(self) -> str | None:
        return HA_DOC

    async def connect(self) -> None:
        self.diag["api"] = (await self.client.ping() or {}).get("message")

    async def read(self) -> dict[Metric, Any]:
        out: dict[Metric, Any] = {}
        for e in self._entries():
            try:
                st = await self.client.state(e["entity_id"])
            except DriverError as exc:
                self.diag["last_error"] = f"{e['entity_id']}: {exc}"
                continue
            raw, unit = st.get("state"), (st.get("attributes") or {}).get("unit_of_measurement")
            if raw in (None, "unknown", "unavailable"):
                continue
            metric: Metric = e["metric"]
            if raw in ("on", "off"):
                out[metric] = raw == "on"
                continue
            try:
                v = float(raw)
            except ValueError:
                out[metric] = raw
                continue
            if metric.value.endswith("_w") and unit in POWER_UNITS:
                v *= POWER_UNITS[unit]
            elif metric.value.endswith("_kwh") and unit in ENERGY_UNITS:
                v *= ENERGY_UNITS[unit]
            out[metric] = -v * float(e.get("scale", 1.0)) if e.get("invert") else v * float(e.get("scale", 1.0))
        if not out and self._entries():
            raise DeviceUnavailableError(self.diag.get("last_error", "geen waarden van Home Assistant"))
        self.diag["last_read"] = time.time()
        return out

    async def apply(self, command: Command) -> None:
        ctl = self._controls().get(command.action)
        if ctl is None:
            raise UnsupportedCommandError(f"{command.action.value}: geen Home Assistant-entiteit gekoppeld "
                                          "(of exclusieve bediening niet bevestigd)")
        eid = ctl["entity_id"]
        dom = eid.split(".")[0]
        service, key = SERVICES[command.action][dom]
        data: dict[str, Any] = {"entity_id": eid}
        if command.action == CommandAction.SWITCH:
            service = service.format(onoff="on" if str(command.value).lower() in ("on", "1", "true") else "off")
        elif command.action == CommandAction.HP_MODE:
            options = ctl.get("options") or {}
            if str(command.value) not in options:
                raise UnsupportedCommandError(f"geen Home Assistant-optie voor modus {command.value!r}")
            data[key] = options[str(command.value)]
        else:
            data[key] = float(command.value)
        await self.client.call(dom, service, data)
        self.diag["last_call"] = {"service": f"{dom}.{service}", "data": data, "ts": time.time()}

    async def release_control(self) -> None:
        return None   # Home Assistant automations/the device keep their own behaviour

    def diagnostics(self) -> dict[str, Any]:
        return dict(self.diag)
