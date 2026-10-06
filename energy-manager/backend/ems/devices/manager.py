"""DeviceManager: owns all driver instances.

Responsibilities: connect / reconnect with back-off, concurrent polling
with timeouts, sensor validation, frozen-data detection, command dispatch
and releasing control (fail-safe). A failing device never stops the loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ems.core.config import DeviceConfig, EMSConfig
from ems.core.models import Command, DeviceState, DeviceStatus, Metric
from ems.core.validation import FrozenValueDetector, validate_values
from ems.devices.base import (
    DeviceDriver,
    DeviceUnavailableError,
    DriverContext,
    UnsupportedCommandError,
)
from ems.devices.registry import DriverRegistry
from ems.devices.registry import registry as default_registry

log = logging.getLogger(__name__)

FAILURES_BEFORE_RECONNECT = 3


@dataclass
class ManagedDevice:
    config: DeviceConfig
    driver: DeviceDriver | None
    connected: bool = False
    last_ok: datetime | None = None
    last_values: dict[Metric, Any] = field(default_factory=dict)
    next_retry: datetime | None = None
    failures: int = 0
    error: str | None = None


class DeviceManager:
    def __init__(self, config: EMSConfig, context: DriverContext,
                 registry: DriverRegistry = default_registry, strict: bool = True) -> None:
        self.config = config
        self.context = context
        self.registry = registry
        self.clock = context.clock
        self.timeout = config.control.device_timeout_s
        self.stale_after = timedelta(seconds=config.control.grid_stale_after_s)
        self.backoff = timedelta(seconds=config.control.reconnect_backoff_s)
        self.frozen = FrozenValueDetector(config.control.frozen_after_s)
        self.devices: dict[str, ManagedDevice] = {}
        self.strict = strict
        for dev in config.devices:
            driver, error = None, None
            if dev.enabled:
                try:
                    driver = registry.get(dev.driver)(dev, context)
                except KeyError as exc:
                    if strict:
                        raise
                    error = f"driver niet beschikbaar: {exc}"
                    log.error("unknown driver", extra={"device": dev.id, "driver": dev.driver})
            self.devices[dev.id] = ManagedDevice(dev, driver, error=error)

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        await asyncio.gather(*(self._connect(d) for d in self.devices.values() if d.driver))

    async def stop(self) -> None:
        await self.release_all()
        for d in self.devices.values():
            if d.driver and d.connected:
                try:
                    await asyncio.wait_for(d.driver.disconnect(), self.timeout)
                except Exception:
                    log.warning("disconnect failed", extra={"device": d.config.id})
                d.connected = False

    async def _connect(self, d: ManagedDevice) -> None:
        assert d.driver is not None
        try:
            await asyncio.wait_for(d.driver.connect(), self.timeout)
            d.connected, d.failures, d.error, d.next_retry = True, 0, None, None
            self.frozen.reset(d.config.id)
            log.info("device connected", extra={"device": d.config.id})
        except Exception as exc:
            d.connected = False
            d.error = _describe(exc)
            d.next_retry = self.clock.now() + self.backoff
            log.warning("device connect failed", extra={"device": d.config.id, "error": d.error})

    # -- polling ---------------------------------------------------------
    async def poll(self) -> dict[str, DeviceState]:
        items = list(self.devices.values())
        states = await asyncio.gather(*(self._poll_one(d) for d in items))
        return {d.config.id: s for d, s in zip(items, states, strict=True)}

    async def _poll_one(self, d: ManagedDevice) -> DeviceState:
        cfg = d.config
        now = self.clock.now()
        if d.driver is None:
            if d.error:
                return DeviceState(cfg.id, cfg.category, DeviceStatus.OFFLINE, error=d.error)
            return DeviceState(cfg.id, cfg.category, DeviceStatus.DISABLED)
        if not d.connected:
            if d.next_retry is None or now >= d.next_retry:
                await self._connect(d)
            if not d.connected:
                return DeviceState(cfg.id, cfg.category, DeviceStatus.OFFLINE,
                                   last_update=d.last_ok, error=d.error)
        try:
            raw = await asyncio.wait_for(d.driver.read(), self.timeout)
        except Exception as exc:
            d.failures += 1
            d.error = _describe(exc)
            if d.failures >= FAILURES_BEFORE_RECONNECT:
                d.connected = False
                d.next_retry = now + self.backoff
            recent = d.last_ok is not None and now - d.last_ok < self.stale_after
            status = DeviceStatus.STALE if recent else DeviceStatus.OFFLINE
            return DeviceState(cfg.id, cfg.category, status, dict(d.last_values),
                               last_update=d.last_ok, error=d.error)

        values, rejected = validate_values(raw)
        if rejected:
            log.warning("implausible values rejected",
                        extra={"device": cfg.id, "rejected": {str(k): v for k, v in rejected.items()}})
        if self.frozen.is_frozen(cfg.id, values, now):
            d.error = "meetwaarde bevroren"
            return DeviceState(cfg.id, cfg.category, DeviceStatus.STALE, values,
                               last_update=d.last_ok, error=d.error, rejected=rejected)
        d.failures, d.error, d.last_ok, d.last_values = 0, None, now, values
        return DeviceState(cfg.id, cfg.category, DeviceStatus.ONLINE, values,
                           last_update=now, rejected=rejected)

    # -- control ---------------------------------------------------------
    def driver(self, device_id: str) -> DeviceDriver:
        drv = self.devices[device_id].driver
        if drv is None:
            raise DeviceUnavailableError(f"{device_id} is uitgeschakeld")
        return drv

    def health(self, device_id: str, now: datetime) -> dict:
        d = self.devices[device_id]
        age = None if d.last_ok is None else (now - d.last_ok).total_seconds()
        return {"connected": d.connected, "failures": d.failures, "error": d.error,
                "last_update": None if d.last_ok is None else d.last_ok.isoformat(),
                "age_s": None if age is None else round(age, 2),
                "next_retry": None if d.next_retry is None else d.next_retry.isoformat()}

    def is_simulated(self, device_id: str) -> bool:
        d = self.devices.get(device_id)
        return bool(d and d.driver and d.driver.manifest.simulated)

    async def apply(self, command: Command) -> None:
        d = self.devices.get(command.device_id)
        if d is None or d.driver is None:
            raise DeviceUnavailableError(f"onbekend of uitgeschakeld apparaat {command.device_id}")
        if not d.driver.supports(command):
            raise UnsupportedCommandError(f"{command.device_id} ondersteunt {command.action} niet")
        if not d.connected:
            raise DeviceUnavailableError(f"{command.device_id} is niet verbonden")
        await asyncio.wait_for(d.driver.apply(command), self.timeout)

    async def release_all(self) -> dict[str, str | None]:
        """Ask every connected device to return to native behaviour."""
        async def one(d: ManagedDevice) -> tuple[str, str | None]:
            try:
                await asyncio.wait_for(d.driver.release_control(), self.timeout)  # type: ignore[union-attr]
                return d.config.id, None
            except Exception as exc:
                return d.config.id, _describe(exc)

        targets = [d for d in self.devices.values() if d.driver and d.connected]
        return dict(await asyncio.gather(*(one(d) for d in targets)))


def _describe(exc: BaseException) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
