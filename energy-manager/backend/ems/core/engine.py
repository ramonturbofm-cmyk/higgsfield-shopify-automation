"""EMSEngine: the 24/7 control loop.

One tick:
  poll devices -> validate -> SiteSnapshot -> health check
  -> (fail-safe | controller decisions) -> manual overrides
  -> CommandGate (dry-run / simulation / live) -> decision journal -> events

Safety beats economics: when the grid measurement is missing, or the
controller crashes, every device is released to its own native behaviour
until the system has been healthy for a number of consecutive ticks.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ems.control.base import ControlContext, Controller
from ems.control.gate import CommandGate, GateMode, GateResult, Outcome
from ems.control.overrides import OverrideManager
from ems.core.clock import Clock
from ems.core.config import EMSConfig
from ems.core.events import EventBus
from ems.core.journal import DecisionJournal, JournalEntry
from ems.core.snapshot import SiteSnapshot, build_snapshot
from ems.devices.manager import DeviceManager

log = logging.getLogger(__name__)


def gate_mode(config: EMSConfig) -> GateMode:
    if config.runtime.dry_run:
        return GateMode.DRY_RUN
    if config.runtime.simulation_mode:
        return GateMode.SIMULATION
    return GateMode.LIVE


@dataclass
class TickResult:
    run_id: str
    snapshot: SiteSnapshot
    results: list[GateResult] = field(default_factory=list)
    failsafe: bool = False
    issues: list[str] = field(default_factory=list)


class EMSEngine:
    def __init__(self, config: EMSConfig, devices: DeviceManager, controller: Controller, clock: Clock,
                 *, journal: DecisionJournal | None = None, bus: EventBus | None = None,
                 gate: CommandGate | None = None, overrides: OverrideManager | None = None) -> None:
        self.config = config
        self.devices = devices
        self.controller = controller
        self.clock = clock
        self.journal = journal or DecisionJournal()
        self.bus = bus or EventBus()
        self.gate = gate or CommandGate(devices, gate_mode(config), config.control.command_refresh_s)
        self.tz = ZoneInfo(config.site.timezone)
        self.overrides = overrides or OverrideManager(clock, self.tz)
        self.failsafe_active = False
        self.failsafe_reason: str | None = None
        self.failsafe_events = 0
        self.commands_sent = 0
        self.heartbeat: datetime | None = None
        self.last_snapshot: SiteSnapshot | None = None
        self._healthy_ticks = 0
        self._last_grid_ok: datetime | None = None
        self._counter = itertools.count(1)

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self.devices.start()
        self._last_grid_ok = self.clock.now()  # grace period after boot
        self.heartbeat = self.clock.now()
        log.info("EMS engine started", extra={"mode": self.gate.mode, "controller": self.controller.name})

    async def stop(self) -> None:
        await self.devices.stop()
        self._journal_simple("system", "shutdown", "released", "EMS gestopt: apparaten terug naar eigen regeling",
                             ["Normale afsluiting"])

    async def run_forever(self, stop: asyncio.Event) -> None:
        interval = self.config.control.interval_s
        while not stop.is_set():
            started = self.clock.now()
            try:
                await self.tick()
            except Exception:  # last line of defence — never let the loop die
                log.exception("tick crashed")
                await self._enter_failsafe("interne fout in de regelcyclus")
            elapsed = (self.clock.now() - started).total_seconds()
            await self.clock.sleep(max(0.0, interval - elapsed))

    # ----------------------------------------------------------------- tick
    async def tick(self) -> TickResult:
        now = self.clock.now()
        run_id = f"{now:%Y%m%dT%H%M%S}-{next(self._counter)}"
        states = await self.devices.poll()
        snap = build_snapshot(self.config, states, now)
        self.last_snapshot = snap
        result = TickResult(run_id, snap)
        await self.bus.publish("snapshot", snap)

        await self._expire_overrides(now, run_id)

        result.issues = self._health_issues(snap, now)
        if result.issues:
            self._healthy_ticks = 0
            await self._enter_failsafe("; ".join(result.issues), run_id)
        elif self.failsafe_active:
            self._healthy_ticks += 1
            if self._healthy_ticks >= self.config.control.failsafe_recover_ticks:
                self._exit_failsafe(run_id)
        if self.failsafe_active:
            result.failsafe = True
            self.heartbeat = now
            return result

        ctx = ControlContext(self.config, now, self._capabilities(), run_id)
        try:
            decisions = self.controller.decide(snap, ctx)
        except Exception:
            log.exception("controller failed", extra={"controller": self.controller.name})
            await self._enter_failsafe(f"regelstrategie '{self.controller.name}' faalde", run_id)
            result.failsafe = True
            self.heartbeat = now
            return result

        for decision in self.overrides.apply(decisions):
            gr = await self.gate.submit(decision, now)
            result.results.append(gr)
            if gr.outcome == Outcome.SENT:
                self.commands_sent += 1
            if gr.outcome not in (Outcome.SKIPPED, Outcome.REFRESHED):
                self._journal(gr, run_id, now)
                await self.bus.publish("decision", gr)
        self.heartbeat = now
        return result

    # --------------------------------------------------------------- health
    def _health_issues(self, snap: SiteSnapshot, now: datetime) -> list[str]:
        issues = []
        if snap.grid_device_id is not None:
            if snap.grid_valid:
                self._last_grid_ok = now
            elif self._last_grid_ok is None or now - self._last_grid_ok > timedelta(
                    seconds=self.config.control.grid_stale_after_s):
                state = snap.devices.get(snap.grid_device_id)
                why = f" ({state.status}: {state.error})" if state and state.error else ""
                issues.append(f"netmeting {snap.grid_device_id} ontbreekt of is onbetrouwbaar{why}")
        return issues

    def _capabilities(self):
        return {dev_id: d.driver.capabilities() for dev_id, d in self.devices.devices.items()
                if d.driver is not None}

    async def _enter_failsafe(self, reason: str, run_id: str = "") -> None:
        if self.failsafe_active:
            return
        self.failsafe_active, self.failsafe_reason = True, reason
        self.failsafe_events += 1
        self._healthy_ticks = 0
        errors = await self.devices.release_all()
        self.gate.reset()
        log.error("FAIL-SAFE active", extra={"reason": reason, "release_errors": errors})
        reasons = [reason, "Alle apparaten terug naar hun eigen (veilige) regeling"]
        reasons += [f"{dev}: vrijgeven mislukt ({err})" for dev, err in errors.items() if err]
        self._journal_simple("system", "failsafe", "released", "EMS fallback actief", reasons, run_id)
        await self.bus.publish("failsafe", {"active": True, "reason": reason})

    def _exit_failsafe(self, run_id: str) -> None:
        reason = self.failsafe_reason
        self.failsafe_active, self.failsafe_reason = False, None
        log.warning("fail-safe cleared", extra={"previous_reason": reason})
        self._journal_simple("system", "failsafe_cleared", "sent", "EMS hervat automatische regeling",
                             [f"Systeem {self.config.control.failsafe_recover_ticks} cycli gezond",
                              f"Vorige oorzaak: {reason}"], run_id)

    async def _expire_overrides(self, now: datetime, run_id: str) -> None:
        for ov in self.overrides.expire():
            dev = ov.command.device_id
            try:
                await self.devices.driver(dev).release_control()
            except Exception as exc:
                log.warning("release after override failed", extra={"device": dev, "error": str(exc)})
            self.gate.reset(dev)
            self._journal_simple(dev, "override_expired", "released",
                                 f"Handmatige bediening verlopen: {ov.command.describe_nl()}",
                                 ["Terug naar AUTO"], run_id)

    # -------------------------------------------------------------- journal
    def _journal(self, gr: GateResult, run_id: str, now: datetime) -> None:
        d = gr.decision
        reasons = list(d.reasons) + ([gr.error] if gr.error else [])
        self.journal.record(JournalEntry(
            timestamp=now, site_id=self.config.site.id, run_id=run_id,
            device=d.command.device_id, action=d.command.action.value, outcome=gr.outcome.value,
            old_value=gr.old_value, new_value=d.command.value, summary=d.summary, reasons=reasons,
            source=d.source, expected_profit=d.expected_benefit_eur, data=d.data,
        ))

    def _journal_simple(self, device: str, action: str, outcome: str, summary: str,
                        reasons: list[str], run_id: str = "") -> None:
        self.journal.record(JournalEntry(
            timestamp=self.clock.now(), site_id=self.config.site.id, run_id=run_id,
            device=device, action=action, outcome=outcome, old_value=None, new_value=None,
            summary=summary, reasons=reasons, source="engine",
        ))
