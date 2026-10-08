"""EMSRuntime: everything that runs 24/7 on the Raspberry Pi, wired together.

    devices -> EMS engine (control loop) -> CommandGate -> devices
                 |  snapshot                     ^
                 v                               | plan slot
    history / notifications / automations    OptimizingController <- OptimizerService
                 |                                                   ^  ^  ^
                 v                                           prices tariff forecast
    WebSocket fan-out (live, decisions, plan, notifications)

The API layer only talks to this object. In Demo Mode a simulated site is advanced
in (scaled) real time; in production only real drivers are used and missing data
stays missing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from ems import __version__
from ems.automations import AutomationEngine
from ems.control.base import NativeController
from ems.control.optimizing import OptimizingController
from ems.core.clock import SimulatedClock, SystemClock
from ems.core.config import EMSConfig, StrategyProfile, config_from_dict, dump_config, load_config, save_config
from ems.core.engine import EMSEngine
from ems.core.events import EventBus
from ems.core.journal import DecisionJournal, JournalEntry
from ems.core.models import Command, CommandAction, DeviceCategory, DeviceStatus, Metric
from ems.core.watchdog import sd_notify
from ems.database import Database
from ems.devices.base import DriverContext
from ems.devices.manager import DeviceManager
from ems.devices.registry import DriverRegistry
from ems.devices.registry import registry as default_registry
from ems.forecasting.service import ForecastService
from ems.forecasting.weather import DemoWeatherProvider, OpenMeteoProvider
from ems.gridmeter.meter import GridMeterStatus
from ems.nodes.power import windows_sleep_settings
from ems.nodes.service import NodeService
from ems.optimizer.explain import aggregate
from ems.optimizer.service import OptimizerService
from ems.prices.providers import DemoProvider, EnergyZeroProvider, EntsoeProvider, StaticProvider
from ems.prices.service import PriceService
from ems.security.auth import SessionRegistry, TokenIssuer, hash_password
from ems.security.secrets import SecretStore
from ems.server.demo import DEMO_CONFIG, PRODUCTION_DEFAULT
from ems.server.history import HistoryRecorder
from ems.server.logbuffer import RingBufferHandler
from ems.server.notifications import NotificationService
from ems.services.backup import create_backup, rotate_backups
from ems.simulator.builder import build_site
from ems.tariffs import TariffEngine

log = logging.getLogger(__name__)


class DBJournal(DecisionJournal):
    """Decision journal that also persists to the database."""

    def __init__(self, db: Database) -> None:
        super().__init__(None)
        self.db = db

    def record(self, entry: JournalEntry) -> None:
        super().record(entry)
        try:
            self.db.insert_decision({
                "ts": entry.timestamp.timestamp(), "site_id": entry.site_id, "run_id": entry.run_id,
                "device": entry.device, "action": entry.action, "outcome": entry.outcome,
                "old_value": entry.old_value, "new_value": entry.new_value, "summary": entry.summary,
                "reasons": entry.reasons, "source": entry.source, "price": entry.price,
                "expected_profit": entry.expected_profit, "data": _jsonable(entry.data)})
        except Exception:
            log.exception("could not store decision")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class EMSRuntime:
    def __init__(self, data_dir: str | Path, *, mode: str | None = None, db_url: str | None = None,
                 registry: DriverRegistry = default_registry, env: dict[str, str] | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.mode_override = mode
        self.db_url = db_url or os.environ.get("EMS_DATABASE_URL") or f"sqlite:///{self.data_dir / 'ems.db'}"
        self.registry = registry
        self.env = dict(os.environ) if env is None else env
        self.config_path = self.data_dir / "ems.yaml"
        self.bus = EventBus()
        self.log_buffer = RingBufferHandler()
        self.started_at = time.time()
        self.tasks: list[asyncio.Task] = []
        self.ws_queues: set[asyncio.Queue] = set()
        self._reload_lock = asyncio.Lock()
        self._last_tick_monotonic = time.monotonic()
        self._prev_status: dict[str, str] = {}
        self._last_automation = 0.0
        self._last_daily: str | None = None
        self.running = False
        self.watchdog_tripped = False
        self.site = None

    # ----------------------------------------------------------------- start
    async def start(self, loops: bool = True) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        logging.getLogger().addHandler(self.log_buffer)
        if not self.config_path.exists():
            default = DEMO_CONFIG if (self.mode_override or self.env.get("EMS_MODE")) == "demo" else PRODUCTION_DEFAULT
            self.config_path.write_text(yaml.safe_dump(default, sort_keys=False, allow_unicode=True), encoding="utf-8")
        self.config = self._load_config()
        self.db = Database(self.db_url)
        await asyncio.to_thread(self.db.migrate)
        self.secrets = SecretStore(self.data_dir)
        jwt_secret = self.secrets.get("jwt_secret")
        if not jwt_secret:
            jwt_secret = secrets.token_urlsafe(48)
            self.secrets.set("jwt_secret", jwt_secret)
        self.tokens = TokenIssuer(jwt_secret)
        self.sessions = SessionRegistry()
        self.notifications = NotificationService(self.db, self.bus, self.config.notifications.webhook_url)
        self.automations = AutomationEngine(self.db, self._run_automation_action)
        self.nodes = NodeService(self)
        self.nodes.load_links()
        if self.config.runtime.mode == "demo" and await asyncio.to_thread(self.db.count_users) == 0:
            await asyncio.to_thread(self.db.create_user, "demo", hash_password("demo"), "installer")
        self.bus.subscribe("decision", self._on_decision)
        self.bus.subscribe("failsafe", self._on_failsafe)
        self.bus.subscribe("notification", lambda _t, p: self._broadcast({"type": "notification", "data": p}))
        await self._build()
        if loops:
            self._start_loops()
        self.running = True
        sd_notify("READY=1")

    def _load_config(self) -> EMSConfig:
        cfg = load_config(self.config_path, env=self.env)
        if self.mode_override:
            cfg.runtime.mode = self.mode_override
        if cfg.runtime.mode == "demo":
            cfg.runtime.simulation_mode = True
        return cfg

    @property
    def demo(self) -> bool:
        return self.config.runtime.mode == "demo"

    async def _build(self) -> None:
        cfg = self.config
        now = datetime.now(UTC)
        if self.demo:
            old_site = self.site
            start = old_site.now if old_site is not None else now
            self.clock = SimulatedClock(start)
            self.site = build_site(cfg, start)
            if old_site is not None:
                self.site.carry_state_from(old_site)
            self.site.advance(0)
        else:
            self.clock = SystemClock()
            self.site = None
        self.nodes.reconfigure()
        ctx = DriverContext(self.clock, simulator=self.site, secrets=self.secrets,  # type: ignore[arg-type]
                            nodes=self.nodes.links)
        self.devices = DeviceManager(cfg, ctx, self.registry, strict=False)
        provider, provider_error = None, None
        try:
            match cfg.prices.provider:
                case "entsoe":
                    token = cfg.prices.entsoe_token
                    if token.startswith("secret:"):
                        token = self.secrets.get(token.removeprefix("secret:")) or ""
                    provider = EntsoeProvider(token, cfg.prices.bidding_zone)
                case "energyzero":
                    provider = EnergyZeroProvider(cfg.prices.energyzero_interval)
                case "manual":
                    provider = StaticProvider()
                case "demo":
                    if self.site is None:
                        provider_error = "demo-prijzen zijn alleen beschikbaar in Demo Mode"
                    else:
                        provider = DemoProvider(self.site.env)
        except ValueError as exc:
            provider_error = str(exc)
        self.prices = PriceService(self.db, cfg.prices.bidding_zone, provider, cfg.site.timezone)
        self.prices.last_error = provider_error
        self.tariff = TariffEngine(cfg.tariff, self.prices.spot, cfg.site.timezone)
        weather = None
        if cfg.forecast.weather_provider == "open_meteo":
            weather = OpenMeteoProvider()
        elif cfg.forecast.weather_provider == "demo" and self.site is not None:
            weather = DemoWeatherProvider(self.site.env)
        self.forecast = ForecastService(cfg, self.db, weather)
        caps = {i: d.driver.device_capabilities() for i, d in self.devices.devices.items() if d.driver is not None}
        self.optimizer = OptimizerService(cfg, self.prices, self.tariff, self.forecast, caps)
        # A pure device-gateway node never decides itself; its controller sends commands via the node API.
        self.controller = OptimizingController(self.optimizer) if self.nodes.identity.is_controller \
            else NativeController()
        self.journal = DBJournal(self.db)
        self.engine = EMSEngine(cfg, self.devices, self.controller, self.clock, journal=self.journal, bus=self.bus)
        self.engine.gate.owner_guard = self.nodes.owner_guard
        self.recorder = HistoryRecorder(self.db, cfg.site.id, self.tariff, min_interval_s=max(10.0, cfg.control.interval_s))
        await self.engine.start()
        await self.engine.observe()
        await self.prices.refresh(self.clock.now())
        await self.forecast.refresh_weather(self.clock.now())
        self._prev_status = {}
        self.optimizer.request("start")
        if self.engine.grid_selection.status == GridMeterStatus.NO_PRIMARY_GRID_METER:
            await self.notifications.notify("warning", "no_primary_grid_meter", self.engine.grid_selection.reason,
                                            cooldown_s=24 * 3600)

    # ----------------------------------------------------------------- loops
    def _start_loops(self) -> None:
        loops = [self._control_loop(), self._price_loop(), self._forecast_loop(), self._maintenance_loop(),
                 self._watchdog_loop(), self.nodes.loop()]
        if self.nodes.identity.is_controller:
            loops.append(self._optimizer_loop())
        for coro in loops:
            self.tasks.append(asyncio.create_task(coro))

    async def _stop_loops(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.tasks.clear()

    async def stop(self) -> None:
        self.running = False
        await self._stop_loops()
        try:
            await self.engine.stop()
        except Exception:
            log.exception("engine stop failed")
        try:
            await self.nodes.stop()
        except Exception:
            log.exception("node service stop failed")
        logging.getLogger().removeHandler(self.log_buffer)
        self.db.close()

    @property
    def speed(self) -> float:
        return self.config.runtime.demo_speed if self.demo else 1.0

    async def tick_once(self) -> None:
        """One control cycle (also used by tests)."""
        interval = self.config.control.interval_s
        await self.engine.tick()
        if self.site is not None:
            self.site.advance(interval)
            self.clock.advance(interval)  # type: ignore[union-attr]
        self._last_tick_monotonic = time.monotonic()
        snap = self.engine.last_snapshot
        if snap is None:
            return
        try:
            await self.recorder.record(snap)
        except Exception:
            log.exception("history record failed")
        await self._check_notifications(snap)
        self.optimizer.check_triggers(snap)
        if time.monotonic() - self._last_automation >= 30 / self.speed:
            self._last_automation = time.monotonic()
            try:
                await self.automations.evaluate(self.automation_metrics(), self.clock.now())
            except Exception:
                log.exception("automations failed")
        await self._broadcast({"type": "live", "data": self.live()})

    async def _control_loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self.tick_once()
            except Exception:
                log.exception("control cycle failed")
            interval = self.config.control.interval_s / self.speed
            await asyncio.sleep(max(0.05, interval - (time.monotonic() - started)))

    async def _optimizer_loop(self) -> None:
        reason = "start"
        while True:
            snap = self.engine.last_snapshot
            if snap is not None:
                try:
                    plan = await self.optimizer.run(snap, self.clock.now(), reason)
                    await asyncio.to_thread(self.db.insert_plan, {
                        "created_ts": time.time(), "site_id": self.config.site.id, "status": plan.status,
                        "trigger": reason, "expected_cost": plan.expected_cost, "baseline_cost": plan.baseline_cost,
                        "payload": {"summary": plan.inputs_summary, "message": plan.message,
                                    "slots": plan.slots[:16]}})
                    await self._broadcast({"type": "plan", "data": self.plan_view(hours=36)})
                except Exception:
                    log.exception("optimizer run failed")
                reason = await self.optimizer.wait_trigger(self.config.optimizer.interval_minutes * 60 / self.speed)
            else:
                await asyncio.sleep(1)

    async def _price_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.prices.refresh_minutes * 60 / self.speed)
            before = self.prices.last_known()
            n = await self.prices.refresh(self.clock.now())
            if n and self.prices.last_known() != before:
                self.optimizer.request("nieuwe prijsdata")
                await self._price_notifications()

    async def _forecast_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.forecast.refresh_minutes * 60 / self.speed)
            old = self.forecast.latest
            await self.forecast.refresh_weather(self.clock.now())
            if old is not None:
                new = self.forecast.build(self.clock.now(), 24, old.outdoor_c[0] if old.outdoor_c else None)
                a, b = sum(old.pv_w[:96]), sum(new.pv_w[:96])
                if max(a, b) > 2000 and abs(a - b) / max(a, b) > 0.2:
                    self.optimizer.request("sterk gewijzigde PV-prognose")

    async def _maintenance_loop(self) -> None:
        while True:
            await asyncio.sleep(60 / self.speed)
            try:
                await asyncio.to_thread(self.recorder.aggregate, self.clock.now())
                today = self.clock.now().astimezone(ZoneInfo(self.config.site.timezone)).date().isoformat()
                if self._last_daily != today:
                    if self._last_daily is not None:
                        await self.forecast.update_pv_calibration(self.clock.now() - timedelta(days=1))
                        await asyncio.to_thread(self.db.apply_retention)
                        await asyncio.to_thread(self.auto_backup)
                    self._last_daily = today
            except Exception:
                log.exception("maintenance failed")

    async def _watchdog_loop(self) -> None:
        """Software watchdog on real (monotonic) time; systemd/Docker restarts the service."""
        while True:
            await asyncio.sleep(5)
            stalled = time.monotonic() - self._last_tick_monotonic
            limit = max(60.0, 6 * self.config.control.interval_s / self.speed)
            if stalled > limit:
                if not self.watchdog_tripped:
                    self.watchdog_tripped = True
                    log.critical("control loop stalled", extra={"seconds": round(stalled)})
                    await self.devices.release_all()
                    await self.notifications.notify("critical", "watchdog", "Regelcyclus vastgelopen: apparaten "
                                                    "teruggezet naar hun eigen regeling")
            else:
                self.watchdog_tripped = False
                sd_notify("WATCHDOG=1")

    def auto_backup(self) -> Path | None:
        if not self.db_url.startswith("sqlite:///"):
            return None
        directory = self.data_dir / "backups"
        directory.mkdir(exist_ok=True)
        path = directory / f"auto-{datetime.now(UTC):%Y%m%d-%H%M%S}.zip"
        path.write_bytes(create_backup(self.data_dir, self.db_url, include_keys=False))
        rotate_backups(directory)
        return path

    # ---------------------------------------------------------- notifications
    async def _check_notifications(self, snap) -> None:
        n = self.notifications
        for dev_id, st in snap.devices.items():
            prev = self._prev_status.get(dev_id)
            self._prev_status[dev_id] = st.status.value
            name = self._device_name(dev_id)
            if st.status == DeviceStatus.OFFLINE and prev not in (None, DeviceStatus.OFFLINE.value):
                await n.notify("warning", "device_offline", f"{name} is offline", {"device": dev_id}, key=dev_id,
                               cooldown_s=900)
            elif st.status == DeviceStatus.ONLINE and prev == DeviceStatus.OFFLINE.value:
                n.clear("device_offline", dev_id)
                await n.notify("info", "device_online", f"{name} is weer online", {"device": dev_id}, key=dev_id,
                               cooldown_s=60)
        sel = self.engine.grid_selection
        if sel.device_id and not snap.grid_valid:
            await n.notify("warning", "grid_meter_missing", "P1-/netmeterdata ontbreekt", {"device": sel.device_id},
                           cooldown_s=1800)
        if snap.phase_currents_a:
            pct = 100 * max(abs(a) for a in snap.phase_currents_a) / self.config.grid.ampere_per_phase
            if pct >= self.config.notifications.phase_load_warn_pct:
                await n.notify("warning", "phase_load", f"Hoge fasebelasting: {pct:.0f}% van de zekering",
                               {"pct": round(pct, 1)}, cooldown_s=900)

    async def _price_notifications(self) -> None:
        now = self.clock.now()
        upcoming = self.prices.series(now, now + timedelta(hours=36), estimate=False)
        neg = [p for p in upcoming if p.spot < 0]
        if neg:
            await self.notifications.notify("info", "negative_price", f"Negatieve stroomprijs verwacht vanaf "
                                            f"{neg[0].start.astimezone(ZoneInfo(self.config.site.timezone)):%H:%M}",
                                            key=neg[0].start.date().isoformat(), cooldown_s=12 * 3600)
        limit = self.config.notifications.extreme_price_eur_kwh
        high = [p for p in upcoming if (self.tariff.breakdown_with_spot(p.start, p.spot).import_price or 0) > limit]
        if high:
            await self.notifications.notify("info", "extreme_price", f"Extreme stroomprijs verwacht (> €{limit:.2f})"
                                            .replace(".", ","), key=high[0].start.date().isoformat(),
                                            cooldown_s=12 * 3600)

    async def _on_decision(self, _topic: str, gr) -> None:
        d = gr.decision
        await self._broadcast({"type": "decision", "data": {
            "ts": self.clock.now().isoformat(), "device": d.command.device_id, "action": d.command.action.value,
            "value": d.command.value, "outcome": gr.outcome.value, "summary": d.summary, "reasons": d.reasons,
            "expected_benefit": d.expected_benefit_eur}})

    async def _on_failsafe(self, _topic: str, payload: dict) -> None:
        await self.notifications.notify("critical", "failsafe", f"EMS fallback actief: {payload.get('reason')}",
                                        payload, cooldown_s=600)
        await self._broadcast({"type": "failsafe", "data": payload})

    # ------------------------------------------------------------- websocket
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.ws_queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.ws_queues.discard(q)

    async def _broadcast(self, message: dict) -> None:
        for q in list(self.ws_queues):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(message)

    # ------------------------------------------------------------------ views
    def _device_name(self, device_id: str) -> str:
        try:
            return self.config.device(device_id).name
        except KeyError:
            return device_id

    def now(self) -> datetime:
        return self.clock.now()

    def live(self) -> dict:
        snap = self.engine.last_snapshot
        now = self.clock.now()
        br = self.tariff.breakdown(now)
        sel = self.engine.grid_selection
        out: dict[str, Any] = {
            "timestamp": now.isoformat(), "mode": self.config.runtime.mode, "site": self.config.site.name,
            "failsafe": {"active": self.engine.failsafe_active, "reason": self.engine.failsafe_reason},
            "price": {"spot": br.spot, "import": br.import_price, "export": br.export_price},
            "grid_meter": {**sel.to_dict(), "available": False, "age_s": None},
            "flows": None, "devices": {}, "features": {}, "plan_slot": None,
        }
        if snap is None:
            return out
        age = snap.grid.age_s(now)
        out["grid_meter"].update({"available": snap.grid.available, "age_s": None if age is None else round(age, 2),
                                  "name": self._device_name(sel.device_id) if sel.device_id else None})
        out["features"] = snap.features
        out["flows"] = {
            "grid_w": snap.grid_power_w if snap.grid_valid else None,
            "pv_w": snap.pv_power_w, "battery_w": snap.battery_power_w, "soc_pct": snap.battery_soc_pct,
            "house_w": snap.house_load_w, "hp_w": snap.hp_power_w, "ev_w": snap.ev_power_w,
            "indoor_c": snap.indoor_temp_c, "outdoor_c": snap.outdoor_temp_c,
            "phase_currents_a": snap.phase_currents_a,
            "phase_power_w": snap.grid.get_phase_power(), "phase_voltage_v": snap.grid.get_phase_voltage(),
        }
        for dev_id, st in snap.devices.items():
            cfg = self.config.device(dev_id)
            out["devices"][dev_id] = {"name": cfg.name, "category": cfg.category.value, "status": st.status.value,
                                      "error": st.error, "control_level": cfg.control_level,
                                      "control_state": self.engine.control_state(dev_id).value,
                                      "values": {str(k): v for k, v in st.values.items()}}
        slot = self.optimizer.current_slot(now)
        if slot:
            out["plan_slot"] = slot
        out["quality"] = snap.quality
        out["balance"] = snap.balance
        out["ems_status"] = self.ems_status()
        out["now"] = self.now_explanation()
        return out

    # ------------------------------------------------------- status / health
    def ems_status(self) -> dict:
        """AUTOMATIC | SHADOW_MODE | MANUAL_OVERRIDE | DEGRADED | SAFE_MODE | ERROR, with the reason."""
        eng = self.engine
        if self.watchdog_tripped:
            return {"state": "ERROR", "label": "Storing", "reason": "regelcyclus vastgelopen"}
        if eng.failsafe_active:
            return {"state": "SAFE_MODE", "label": "Veilige modus", "reason": eng.failsafe_reason}
        manual = [o for o in eng.overrides.active() if o.source == "override"]
        if manual:
            return {"state": "MANUAL_OVERRIDE", "label": "Handmatige bediening",
                    "reason": ", ".join(o.command.describe_nl() for o in manual)}
        controllable = [d for d in self.config.devices if d.enabled and self.devices.devices.get(d.id)
                        and self.devices.devices[d.id].driver is not None
                        and any(c.value.startswith("control_") for c in self.devices.devices[d.id].driver.device_capabilities())]
        if controllable and all(d.control_level in ("shadow", "read_only", "connection_test") for d in controllable) \
                and any(d.control_level == "shadow" for d in controllable):
            return {"state": "SHADOW_MODE", "label": "Schaduwmodus", "reason": "het EMS rekent mee maar stuurt niets aan"}
        problems = []
        snap = eng.last_snapshot
        if eng.grid_selection.status == GridMeterStatus.NO_PRIMARY_GRID_METER:
            problems.append("geen primaire netmeter")
        elif snap is not None and not snap.grid_valid:
            problems.append("netmeting niet actueel")
        if not self.nodes.identity.is_controller:
            return {"state": "DEGRADED" if problems else "AUTOMATIC", "label": "Gateway",
                    "reason": "deze node levert apparaten aan een andere controller"}
        if self.optimizer.plan is None or not self.optimizer.plan.ok:
            problems.append("geen optimale planning (terugval op zelfconsumptie)")
        offline = [d for d, st in (snap.devices.items() if snap else []) if st.status.value == "offline"]
        if offline:
            problems.append(f"offline: {', '.join(offline)}")
        if problems:
            return {"state": "DEGRADED", "label": "Beperkt", "reason": "; ".join(problems)}
        return {"state": "AUTOMATIC", "label": "Automatisch", "reason": "optimizer regelt volgens planning"}

    def now_explanation(self) -> dict | None:
        """WAT doet het EMS nu, WAAROM, TOT WANNEER, VERWACHT VOORDEEL, WELKE GRENZEN."""
        decisions = self.engine.last_decisions
        if not decisions:
            return None
        order = {"battery": 0, "ev_charger": 1, "heat_pump": 2, "pv_inverter": 3}
        def rank(item):
            dev_id, _ = item
            try:
                return order.get(self.config.device(dev_id).category.value, 9)
            except KeyError:
                return 9
        dev_id, d = sorted(decisions.items(), key=rank)[0]
        until = None
        now = self.clock.now()
        slots = self.optimizer.upcoming(now, 36)
        if slots and d.get("action") in ("battery_charge", "battery_discharge", "battery_standby", "battery_auto"):
            sign = lambda w: 0 if abs(w or 0) < 200 else (1 if w > 0 else -1)  # noqa: E731
            cur = sign(slots[0].get("battery_w"))
            for row in slots[1:]:
                if sign(row.get("battery_w")) != cur:
                    until = row["start"]
                    break
        try:
            name = self.config.device(dev_id).name
        except KeyError:
            name = dev_id
        limits = [f"SOC {self.config.battery.min_soc:.0f}–{self.config.battery.max_soc:.0f}% "
                  f"(reserve {self.config.battery.reserve_soc:.0f}%)",
                  f"aansluiting {self.engine.grid_guard.limit_w / 1000:.1f} kW"]
        plan = self.optimizer.plan
        benefit = d.get("expected_benefit")
        if benefit is None and plan is not None and plan.ok and plan.baseline_cost is not None:
            benefit = round(plan.baseline_cost - plan.expected_cost, 2)
        return {"device": dev_id, "device_name": name, "what": d.get("summary"), "outcome": d.get("outcome"),
                "why": d.get("reasons") or [], "until": until, "expected_benefit_eur": benefit, "limits": limits,
                "since": d.get("ts")}

    def config_warnings(self) -> list[dict]:
        """Plausibility checks on top of the hard validation (which already blocks e.g. min SOC > max SOC)."""
        cfg, out = self.config, []
        conn = cfg.grid.connection_kw
        if cfg.grid.max_import_kw and cfg.grid.max_import_kw > conn * 1.001:
            out.append({"level": "warning", "field": "grid.max_import_kw",
                        "message": f"Maximale afname {cfg.grid.max_import_kw:.1f} kW is hoger dan de aansluiting "
                                   f"{cfg.grid.phases}×{cfg.grid.ampere_per_phase:.0f} A ({conn:.1f} kW)."})
        zero = cfg.strategy.export_mode.value == "zero" or cfg.strategy.profile.value == "zero_export"
        if zero and self.engine.grid_selection.device_id is None:
            out.append({"level": "error", "field": "strategy.export_mode",
                        "message": "Nul-teruglevering vraagt een primaire netmeter; deze functie is niet actief."})
        for d in cfg.devices:
            md = self.devices.devices.get(d.id)
            if d.category.value == "battery" and not d.params.get("capacity_kwh") and \
                    not (d.params.get("sim") or {}).get("capacity_kwh"):
                out.append({"level": "warning", "field": f"devices.{d.id}.params.capacity_kwh",
                            "message": f"{d.name}: capaciteit (kWh) ontbreekt — de optimizer kan de batterij niet plannen."})
            if md and md.driver is None and d.enabled:
                out.append({"level": "error", "field": f"devices.{d.id}.driver", "message": f"{d.name}: {md.error}"})
        if cfg.prices.provider == "none" and cfg.tariff.contract_type.value == "dynamic":
            out.append({"level": "warning", "field": "prices.provider",
                        "message": "Dynamisch contract zonder prijsbron: kies bijvoorbeeld EnergyZero."})
        return out

    def installation(self) -> dict:
        snap = self.engine.last_snapshot
        cfg = self.config

        def state_of(cats: tuple[str, ...]) -> tuple[str, list[str]]:
            devs = [d for d in cfg.devices if d.category.value in cats and d.enabled]
            if not devs:
                return "NOT_CONFIGURED", []
            states = [snap.devices[d.id].status.value if snap and d.id in snap.devices else "unknown" for d in devs]
            st = "ONLINE" if all(s == "online" for s in states) else ("ERROR" if all(s == "offline" for s in states)
                                                                      else "CONFIGURED")
            return st, [d.name for d in devs]

        sel = self.engine.grid_selection
        t = cfg.tariff
        items = [
            {"key": "site", "label": "Locatie", "state": "CONFIGURED", "detail": f"{cfg.site.name} ({cfg.site.timezone})"},
            {"key": "grid", "label": "Netaansluiting", "state": "CONFIGURED",
             "detail": f"{cfg.grid.phases}×{cfg.grid.ampere_per_phase:.0f} A ({cfg.grid.connection_kw:.1f} kW)"},
            {"key": "grid_meter", "label": "Primaire netmeter",
             "state": "NOT_CONFIGURED" if sel.device_id is None else ("ONLINE" if snap and snap.grid_valid else "ERROR"),
             "detail": sel.reason if sel.device_id is None else self._device_name(sel.device_id)},
        ]
        for key, label, cats in (("pv", "Zonnepanelen", ("pv_inverter", "hybrid_inverter")),
                                 ("battery", "Batterij", ("battery",)), ("heat_pump", "Warmtepomp",
                                                                         ("heat_pump", "heat_pump_boiler")),
                                 ("ev", "Laadpaal / EV", ("ev_charger",))):
            st, names = state_of(cats)
            items.append({"key": key, "label": label, "state": st, "detail": ", ".join(names) or "niet ingesteld"})
        contract = "CONFIGURED" if (t.contract_type.value != "dynamic" or cfg.prices.provider != "none") else "NOT_CONFIGURED"
        items.append({"key": "contract", "label": "Energiecontract", "state": contract,
                      "detail": f"{t.contract_name} — {t.contract_type.value}, prijsbron {cfg.prices.provider}"})
        items.append({"key": "nodes", "label": "Nodes", "state": "ONLINE" if all(
            link.online for link in self.nodes.links.values()) else "ERROR",
            "detail": f"{self.nodes.identity.name} ({self.nodes.identity.platform})"
                      + (f" + {len(self.nodes.links)} gekoppeld" if self.nodes.links else "")})
        from ems.api.routes_core import PROFILE_LABELS  # labels live with the API texts
        items.append({"key": "strategy", "label": "EMS-strategie", "state": "CONFIGURED",
                      "detail": PROFILE_LABELS.get(cfg.strategy.profile.value, cfg.strategy.profile.value)})
        return {"items": items, "status": self.ems_status(), "warnings": self.config_warnings()}

    def health(self) -> dict:
        snap = self.engine.last_snapshot
        now = self.clock.now()
        checks: list[dict] = []

        def add(key, label, state, detail=""):
            checks.append({"key": key, "label": label, "state": state, "detail": detail})

        sel = self.engine.grid_selection
        if sel.device_id is None:
            add("grid_meter", "Netmeter", "warn", sel.reason)
        else:
            add("grid_meter", "Netmeter", "ok" if snap and snap.grid_valid else "error",
                "" if snap and snap.grid_valid else "geen actuele netmeting")
        pst = self.prices.status(now)
        if pst["provider"] is None:
            add("prices", "Prijzen", "warn", "geen prijsbron ingesteld")
        else:
            today = pst.get("coverage", {}).get("today")
            add("prices", "Prijzen", "error" if pst["last_error"] and not today else
                ("ok" if today and today["complete"] else "warn"),
                pst["last_error"] or ("" if today and today["complete"] else "niet alle kwartierprijzen van vandaag bekend"))
        if snap is not None:
            for key, label in (("battery", "Batterij"), ("pv", "Zonnepanelen"), ("heat_pump", "Warmtepomp"),
                               ("ev", "Laadpaal")):
                q = snap.quality.get(key, "UNKNOWN")
                if q != "UNKNOWN":
                    add(key, label, "ok" if q == "GOOD" else "warn" if q in ("STALE", "ESTIMATED") else "error",
                        "" if q == "GOOD" else f"gegevens: {q}")
            if snap.balance and not snap.balance.get("ok", True):
                add("balance", "Energiebalans", "warn",
                    f"meetwaarden tellen niet op (rest {snap.balance['residual_w']:.0f} W)")
        if self.nodes.identity.is_controller:
            st = self.optimizer.status()
            add("optimizer", "Optimizer", "ok" if st["status"] == "optimal" else "warn",
                "" if st["status"] == "optimal" else (st.get("message") or "nog geen planning"))
        fc = self.forecast.status()
        if fc["weather_provider"] is None:
            add("forecast", "Prognose", "warn", "geen weerbron: standaardprofielen")
        else:
            add("forecast", "Prognose", "warn" if fc["last_error"] else "ok", fc["last_error"] or "")
        add("time", "Tijd", "ok" if now.year >= 2025 else "error", "" if now.year >= 2025 else "systeemklok onjuist")
        if self.nodes.identity.platform == "WINDOWS" and self.nodes.identity.is_controller:
            sleep = windows_sleep_settings()
            if sleep and sleep["ac_sleep_after_s"] > 0:
                add("sleep", "Slaapstand", "warn", f"Deze pc gaat na {sleep['ac_sleep_after_s'] // 60} min in slaap; "
                    "Energy Manager kan dan niet regelen. Zet slaapstand (op netstroom) uit in Windows-instellingen.")
            elif sleep:
                add("sleep", "Slaapstand", "ok", "uitgeschakeld op netstroom")
        for link in self.nodes.links.values():
            add(f"node:{link.node_id}", f"Node {link.name}", "ok" if link.online and link.lease_ok else
                ("warn" if link.online else "error"), link.lease_error or link.error or "")
        for dev_id, md in self.devices.devices.items():
            cfg = md.config
            if cfg.enabled and md.driver is not None and cfg.control_level in ("shadow", "read_only") and any(
                    c.value.startswith("control_") for c in md.driver.device_capabilities()):
                add(f"control:{dev_id}", f"Regeling {cfg.name}", "warn",
                    "schaduwmodus" if cfg.control_level == "shadow" else "alleen lezen")
        score = round(100 * sum({"ok": 1, "warn": 0.5, "error": 0}[c["state"]] for c in checks) / max(1, len(checks)))
        return {"score": score, "checks": checks, "status": self.ems_status()}

    def plan_view(self, hours: float | None = None, resolution: int | None = None) -> dict:
        """Plan for the UI. ``hours`` defaults to the configured horizon; ``resolution`` (minutes)
        aggregates on the server with correct energy sums and time-weighted averages."""
        plan = self.optimizer.plan
        now = self.clock.now()
        horizon = float(hours or self.config.optimizer.horizon_hours)
        slots = self.optimizer.upcoming(now, horizon)
        step = int(round(slots[0].get("duration_min") or 15)) if slots else 15   # actual optimizer interval
        if resolution and resolution > step:
            slots = aggregate(slots, resolution, self.config.site.timezone)
        known = self.prices.last_known()
        data = {"optimizer": self.optimizer.status(), "run_id": self.optimizer.status().get("run_id"),
                "slots": slots, "horizon_hours": horizon, "configured_horizon_hours": self.config.optimizer.horizon_hours,
                "step_minutes": step, "resolution_minutes": max(step, resolution or step),
                "planned_until": slots[-1]["end"] if slots else None,
                "prices_known_until": None if known is None else known.isoformat(),
                "expected_cost": None, "baseline_cost": None, "expected_benefit": None, "inputs": {}}
        if plan is not None:
            data.update({"expected_cost": plan.expected_cost, "baseline_cost": plan.baseline_cost,
                         "expected_benefit": plan.expected_benefit, "inputs": plan.inputs_summary,
                         "status": plan.status, "message": plan.message})
        return data

    def automation_metrics(self) -> dict[str, Any]:
        snap = self.engine.last_snapshot
        now = self.clock.now()
        tz = ZoneInfo(self.config.site.timezone)
        br = self.tariff.breakdown(now)
        local = now.astimezone(tz)
        m: dict[str, Any] = {"price.import": br.import_price, "price.export": br.export_price, "price.spot": br.spot,
                             "time": f"{local:%H:%M}", "weekday": local.weekday()}
        if snap is not None:
            m.update({"grid.power_w": snap.grid_power_w if snap.grid_valid else None,
                      "grid.import_w": snap.grid.get_import_power(), "grid.export_w": snap.grid.get_export_power(),
                      "pv.power_w": snap.pv_power_w, "battery.soc": snap.battery_soc_pct,
                      "battery.power_w": snap.battery_power_w, "house.load_w": snap.house_load_w,
                      "hp.power_w": snap.hp_power_w, "ev.power_w": snap.ev_power_w,
                      "temp.outdoor": snap.outdoor_temp_c, "temp.indoor": snap.indoor_temp_c})
            for dev_id, st in snap.devices.items():
                m[f"device.{dev_id}.status"] = st.status.value
                for k, v in st.values.items():
                    m[f"device.{dev_id}.{k}"] = v
        fc = self.forecast.latest
        if fc is not None:
            m["forecast.pv_next_24h_kwh"] = round(sum(fc.pv_w[:96]) / 4000, 2)
            end_of_day = local.replace(hour=23, minute=59).astimezone(UTC)
            m["forecast.pv_remaining_today_kwh"] = round(sum(p for s, p in zip(fc.slots, fc.pv_w, strict=False)
                                                             if s <= end_of_day) / 4000, 2)
        return m

    async def _run_automation_action(self, action: dict, meta: dict) -> str:
        t = action["type"]
        who = f"automatisering '{meta['name']}'"
        if t == "override":
            cmd = Command(action["device"], CommandAction(action["action"]), action.get("value"))
            self.config.device(cmd.device_id)  # raises KeyError for unknown devices
            chk = self.engine.check(cmd)       # same validation as a manual override (capability, range, state)
            if not chk.allowed:
                raise ValueError("geweigerd: " + "; ".join(chk.reasons))
            cmd = Command(cmd.device_id, cmd.action, chk.value)
            self.engine.overrides.set(cmd, float(action.get("duration_min", 60)), user=who, source="automation")
            self.optimizer.request("handmatige bediening (automatisering)")
            return f"override {cmd.describe_nl()}"
        if t == "clear_override":
            self.engine.overrides.clear(action["device"])
            return "override opgeheven"
        if t == "notify":
            await self.notifications.notify(action.get("level", "info"), "automation", action["message"],
                                            {"automation": meta["automation_id"]}, key=str(meta["automation_id"]),
                                            cooldown_s=60)
            return "melding verstuurd"
        if t == "set_profile":
            if action.get("profile") not in {p.value for p in StrategyProfile}:
                raise ValueError("onbekend profiel")
            data = self.config.model_dump(mode="json")
            data["strategy"]["profile"] = action["profile"]
            await self.reload(data, who, "profiel gewijzigd door automatisering")
            return f"profiel {action['profile']}"
        if t == "replan":
            self.optimizer.request(who)
            return "herplanning aangevraagd"
        raise ValueError(f"onbekende actie {t}")

    # --------------------------------------------------------- configuration
    async def reload(self, data: dict | EMSConfig, username: str | None, comment: str = "") -> EMSConfig:
        """Validate + persist a new configuration and rebuild the runtime with it."""
        async with self._reload_lock:
            if isinstance(data, EMSConfig):
                new = data
            else:
                new = config_from_dict(data, env=self.env)
                new._env_refs = {**self.config._env_refs, **new._env_refs}
            if self.mode_override:
                new.runtime.mode = self.mode_override
            await asyncio.to_thread(save_config, new, self.config_path)
            await asyncio.to_thread(self.db.add_config_version, dump_config(new), username, comment)
            loops = bool(self.tasks)
            await self._stop_loops()
            await self.engine.stop()
            overrides = self.engine.overrides
            self.config = self._load_config()
            self.notifications.webhook_url = self.config.notifications.webhook_url
            await self._build()
            for ov in overrides.active():          # manual overrides survive a settings change
                self.engine.overrides._items[ov.command.group_key] = ov
            if loops:
                self._start_loops()
            log.info("configuration reloaded", extra={"user": username, "comment": comment})
            return self.config

    async def restart(self, while_stopped=None):
        """Re-read config, database and secrets from disk (after a restore).

        ``while_stopped`` (a blocking callable) runs after the engine is stopped and the
        database is closed — required to replace the database file (Windows locks open
        files). Its result is returned; on an exception the old state is reopened and the
        exception re-raised."""
        async with self._reload_lock:
            loops = bool(self.tasks)
            await self._stop_loops()
            try:
                await self.engine.stop()
            except Exception:
                log.exception("engine stop failed")
            self.db.close()
            result, error = None, None
            if while_stopped is not None:
                try:
                    result = await asyncio.to_thread(while_stopped)
                except Exception as exc:
                    error = exc
            self.config = self._load_config()
            self.db = Database(self.db_url)
            await asyncio.to_thread(self.db.migrate)
            self.secrets = SecretStore(self.data_dir)
            self.tokens = TokenIssuer(self.secrets.get("jwt_secret") or self.tokens.secret)
            self.notifications = NotificationService(self.db, self.bus, self.config.notifications.webhook_url)
            self.automations = AutomationEngine(self.db, self._run_automation_action)
            await self.nodes.stop()
            self.nodes = NodeService(self)
            self.nodes.load_links()
            await self._build()
            if loops:
                self._start_loops()
            if error is not None:
                raise error
            return result

    def system_status(self) -> dict:
        snap = self.engine.last_snapshot
        return {
            "version": __version__, "mode": self.config.runtime.mode, "site": self.config.site.name,
            "uptime_s": round(time.time() - self.started_at), "running": self.running,
            "gate_mode": self.engine.gate.mode.value, "controller": self.engine.controller.name,
            "failsafe": {"active": self.engine.failsafe_active, "reason": self.engine.failsafe_reason,
                         "events": self.engine.failsafe_events},
            "watchdog_tripped": self.watchdog_tripped, "database": {"ok": self.db.ping(),
                                                                    "schema_version": self.db.current_version(),
                                                                    "backend": self.db_url.split(":")[0]},
            "grid_meter": self.engine.grid_selection.to_dict(),
            "features": {} if snap is None else snap.features,
            "prices": self.prices.status(self.clock.now()), "forecast": self.forecast.status(),
            "optimizer": self.optimizer.status(),
            "devices": {i: {"name": self._device_name(i), **self.devices.health(i, self.clock.now())}
                        for i in self.devices.devices},
            "last_tick": None if snap is None else snap.timestamp.isoformat(),
            "speed": self.speed,
        }

    def device_metric(self, device_id: str, metric: Metric):
        snap = self.engine.last_snapshot
        st = None if snap is None else snap.devices.get(device_id)
        return None if st is None else st.get(metric)

    def device_categories(self) -> list[str]:
        return [c.value for c in DeviceCategory]
