"""Drivers on the Shelly Gen2+ Switch component.

``shelly.switch``   one relay channel: on/off, measured power where the model measures it (``apower``).
                    Minimum on/off times are enforced; the relay's own rating must suit the load.
``sgready.relay``   SG Ready via two relay contacts. SG Ready states (BWP label): 1 = 1:0 (blocked),
                    2 = 0:0 (normal), 3 = 0:1 (switch-on recommended), 4 = 1:1 (forced on). Only the
                    states the user enabled are used; normal is the safe default and the state after a
                    release, a failed command or an EMS outage (relay watchdog ``toggle_after``).
                    The heat pump keeps its own protection (compressor, frost, defrost, legionella,
                    minimum run times): SG Ready is a *request*, not power control.
"""

from __future__ import annotations

import time
from typing import Any

from ems.core.models import Capability, Command, CommandAction, DeviceCategory, Metric
from ems.devices.base import DeviceDriver, DriverError, DriverManifest, UnsupportedCommandError
from ems.devices.registry import register_driver
from ems.integrations.generic.mapping import resolve_secret
from ems.integrations.shelly.client import ShellyClient

SHELLY_DOC = "Shelly Gen2+ API documentation: RPC protocol, Shelly.GetDeviceInfo (/shelly), Switch component"
SG_STATES = {1: (True, False), 2: (False, False), 3: (False, True), 4: (True, True)}
SG_LABELS = {1: "1 — geblokkeerd (EVU-sperre)", 2: "2 — normaal bedrijf", 3: "3 — inschakelen aanbevolen",
             4: "4 — inschakelen geforceerd"}
MODE_STATE = {"normal": 2, "boost": 3, "eco": 1, "forced": 4}


class _ShellyBase(DeviceDriver):
    transport = None            # tests/lab: injected httpx transport

    def _client(self) -> ShellyClient:
        c = self.config.connection
        return ShellyClient(str(c.get("host", "")), resolve_secret(c.get("password"), self.context.secrets),
                            float(c.get("timeout_s", 4)), transport=self.transport)

    def write_documented(self) -> bool:
        return True

    def hardware_verified(self) -> bool:
        return False

    def simulated_target(self) -> bool:
        host = str(self.config.connection.get("host", ""))
        return host.startswith(("127.0.0.1", "localhost")) and getattr(self.context.simulator, "lab", None) is not None

    def documentation_ref(self) -> str | None:
        return SHELLY_DOC


@register_driver
class ShellySwitchDriver(_ShellyBase):
    manifest = DriverManifest(
        driver_id="shelly.switch",
        display_name="Shelly Gen2+ relais / slimme stekker",
        vendor="Shelly",
        categories=(DeviceCategory.SMART_PLUG, DeviceCategory.SMART_RELAY, DeviceCategory.BOILER,
                    DeviceCategory.FLEXIBLE_LOAD, DeviceCategory.HEAT_PUMP_BOILER),
        capabilities=frozenset({Capability.READ_LOAD_POWER, Capability.CONTROL_SWITCH}),
        connection_types=("http",),
        documentation=SHELLY_DOC,
        write_capable=True,
        models=("Plus 1PM", "Plus 2PM", "Pro 1PM", "Pro 2PM", "Pro 4PM", "Plus Plug S", "Plus 1", "Pro 1", "Pro 2"),
        notes="Lokaal via het netwerk. Controleer dat het relais geschikt is voor het vermogen van de aangesloten "
              "verbruiker; voor zware boilers/warmtepompen is een geschikt (installatie)relais of magneetschakelaar nodig.",
        connection_schema={
            "host": {"type": "string", "label_nl": "IP-adres / hostnaam"},
            "channel": {"type": "integer", "default": 0, "label_nl": "Kanaal (0 = eerste relais)", "level": "advanced"},
            "password": {"type": "string", "default": "", "label_nl": "Wachtwoord (als ingesteld op de Shelly)",
                         "secret": True, "level": "advanced"},
            "min_on_s": {"type": "integer", "default": 300, "label_nl": "Minimale aan-tijd (s)", "level": "advanced"},
            "min_off_s": {"type": "integer", "default": 300, "label_nl": "Minimale uit-tijd (s)", "level": "advanced"},
            "timeout_s": {"type": "integer", "default": 4, "label_nl": "Time-out (s)", "level": "expert"},
        },
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        self.channel = int(config.connection.get("channel", 0))
        self._changed_at: float | None = None
        self._on: bool | None = None
        self.has_power = True
        self.diag: dict[str, Any] = {}

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.CONTROL_SWITCH} | ({Capability.READ_LOAD_POWER} if self.has_power else set()))

    async def connect(self) -> None:
        info = await self._client().info()
        self.diag["identity"] = {k: info.get(k) for k in ("id", "model", "app", "ver", "mac")}

    async def read(self) -> dict[Metric, Any]:
        st = await self._client().switch_status(self.channel)
        self._on = bool(st.get("output"))
        self.has_power = "apower" in st
        out: dict[Metric, Any] = {}
        if self.has_power:
            out[Metric.LOAD_POWER_W] = float(st["apower"])
        self.diag.update({"output": self._on, "temperature_c": (st.get("temperature") or {}).get("tC"),
                          "voltage_v": st.get("voltage"), "last_read": time.time()})
        return out

    async def apply(self, command: Command) -> None:
        if command.action != CommandAction.SWITCH:
            raise UnsupportedCommandError("dit relais kan alleen aan/uit schakelen")
        on = str(command.value).lower() in ("on", "1", "true", "aan")
        if self._on is not None and on != self._on and self._changed_at is not None:
            c = self.config.connection
            need = float(c.get("min_on_s", 300) if self._on else c.get("min_off_s", 300))
            if time.monotonic() - self._changed_at < need:
                raise UnsupportedCommandError(f"minimale {'aan' if self._on else 'uit'}-tijd ({need:.0f} s) nog niet verstreken")
        client = self._client()
        await client.switch_set(self.channel, on)
        st = await client.switch_status(self.channel)
        if bool(st.get("output")) != on:
            raise DriverError("relais meldt een andere stand dan opgedragen")
        if on != self._on:
            self._changed_at = time.monotonic()
        self._on = on

    async def release_control(self) -> None:
        return None   # a plain relay has no 'own regulation'; the EMS stops switching it

    def diagnostics(self) -> dict[str, Any]:
        return dict(self.diag)


@register_driver
class SGReadyRelayDriver(_ShellyBase):
    manifest = DriverManifest(
        driver_id="sgready.relay",
        display_name="SG Ready via netwerkrelais (Shelly Gen2+, 2 contacten)",
        vendor="SG Ready (BWP)",
        categories=(DeviceCategory.HEAT_PUMP, DeviceCategory.HEAT_PUMP_BOILER),
        capabilities=frozenset({Capability.CONTROL_HP_MODE}),
        connection_types=("http",),
        documentation=f"SG Ready-label (Bundesverband Wärmepumpe, BWP), bedrijfstoestanden 1–4; {SHELLY_DOC}",
        write_capable=True,
        notes="Twee potentiaalvrije relaiscontacten op de SG Ready-ingangen van de warmtepomp. De aansluitklemmen "
              "staan in de installatiehandleiding van uw warmtepomp; laat de bedrading door een vakbekwaam installateur "
              "uitvoeren. Het EMS vraagt een stand aan; de warmtepomp blijft zelf verantwoordelijk voor zijn beveiliging.",
        connection_schema={
            "host": {"type": "string", "label_nl": "IP-adres relaismodule (contact 1)"},
            "contact1_channel": {"type": "integer", "default": 0, "label_nl": "Kanaal voor SG Ready-contact 1"},
            "host2": {"type": "string", "default": "", "label_nl": "IP-adres voor contact 2 (leeg = zelfde module)",
                      "level": "advanced"},
            "contact2_channel": {"type": "integer", "default": 1, "label_nl": "Kanaal voor SG Ready-contact 2"},
            "allowed_states": {"type": "string", "default": "2,3", "label_nl": "Toegestane standen (komma's)",
                               "help_nl": "Standaard alleen 2 (normaal) en 3 (inschakelen aanbevolen). Stand 1 (blokkeren) en "
                                          "4 (geforceerd) alleen als uw warmtepomp dat ondersteunt en u dat bewust wilt."},
            "watchdog_s": {"type": "integer", "default": 900, "label_nl": "Relais valt terug naar normaal na (s)",
                           "level": "advanced"},
            "min_state_s": {"type": "integer", "default": 600, "label_nl": "Minimale tijd per stand (s)", "level": "advanced"},
            "password": {"type": "string", "default": "", "label_nl": "Wachtwoord Shelly (indien ingesteld)",
                         "secret": True, "level": "advanced"},
        },
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        c = config.connection
        self.allowed = sorted({int(s) for s in str(c.get("allowed_states", "2,3")).replace(" ", "").split(",") if s} | {2})
        bad = [s for s in self.allowed if s not in SG_STATES]
        if bad:
            raise DriverError(f"ongeldige SG Ready-stand(en): {bad}")
        self.watchdog_s = max(60, int(c.get("watchdog_s", 900)))
        self.min_state_s = int(c.get("min_state_s", 600))
        self.state: int | None = None
        self._since: float | None = None
        self._refreshed = 0.0
        self.diag: dict[str, Any] = {"allowed_states": [SG_LABELS[s] for s in self.allowed]}

    def _clients(self) -> tuple[ShellyClient, ShellyClient]:
        c1 = self._client()
        host2 = str(self.config.connection.get("host2") or "")
        if not host2:
            return c1, c1
        c = self.config.connection
        return c1, ShellyClient(host2, resolve_secret(c.get("password"), self.context.secrets),
                                float(c.get("timeout_s", 4)), transport=self.transport)

    def modes(self) -> list[str]:
        return [m for m, s in MODE_STATE.items() if s in self.allowed]

    async def connect(self) -> None:
        c1, c2 = self._clients()
        await c1.info()
        if c2 is not c1:
            await c2.info()

    async def _contacts(self) -> tuple[bool, bool]:
        c1, c2 = self._clients()
        cc = self.config.connection
        a = await c1.switch_status(int(cc.get("contact1_channel", 0)))
        b = await c2.switch_status(int(cc.get("contact2_channel", 1)))
        return bool(a.get("output")), bool(b.get("output"))

    async def read(self) -> dict[Metric, Any]:
        contacts = await self._contacts()
        state = next((s for s, v in SG_STATES.items() if v == contacts), None)
        self.state = state
        self.diag.update({"contacts": contacts, "sg_ready_state": SG_LABELS.get(state, "onbekend"), "last_read": time.time()})
        if state not in (None, 2) and time.monotonic() - self._refreshed > self.watchdog_s / 2:
            await self._set(state)      # keep the relay watchdog from expiring while the EMS is alive
        mode = next((m for m, s in MODE_STATE.items() if s == state), "unknown")
        return {Metric.HP_MODE: mode}

    async def _set(self, state: int) -> None:
        c1, c2 = self._clients()
        cc = self.config.connection
        k1, k2 = SG_STATES[state]
        ttl = None if state == 2 else self.watchdog_s
        # Contacts that are switched ON get a device-side timer back to OFF (= back towards normal).
        await c1.switch_set(int(cc.get("contact1_channel", 0)), k1, ttl if k1 else None)
        await c2.switch_set(int(cc.get("contact2_channel", 1)), k2, ttl if k2 else None)
        self._refreshed = time.monotonic()

    async def apply(self, command: Command) -> None:
        if command.action != CommandAction.HP_MODE:
            raise UnsupportedCommandError("SG Ready kent alleen bedrijfsverzoeken (geen setpoint of vermogen)")
        mode = str(command.value)
        state = MODE_STATE.get(mode)
        if state is None:
            raise UnsupportedCommandError(f"onbekende modus {mode!r}")
        if state not in self.allowed:
            raise UnsupportedCommandError(f"SG Ready-stand {SG_LABELS[state]} is niet vrijgegeven voor deze warmtepomp")
        if self.state is not None and state != self.state and self._since is not None \
                and time.monotonic() - self._since < self.min_state_s and state != 2:
            raise UnsupportedCommandError(f"minimale tijd per stand ({self.min_state_s} s) nog niet verstreken")
        try:
            await self._set(state)
            if await self._contacts() != SG_STATES[state]:
                raise DriverError("relais meldt andere contactstanden dan opgedragen")
        except Exception:
            try:
                await self._set(2)            # safe default on any failure
            finally:
                self.state = 2
            raise
        if state != self.state:
            self._since = time.monotonic()
        self.state = state

    async def release_control(self) -> None:
        await self._set(2)
        self.state = 2

    def diagnostics(self) -> dict[str, Any]:
        return dict(self.diag)
