"""Profile-driven Modbus driver (TCP or RTU): ``connection.profile_id`` selects a validated model profile
from the device library. Without a valid profile the driver offers no capabilities at all.

Reads are grouped into blocks of contiguous registers (max 100) per table. Writes:
* only controls whose documentation status allows writing (``ControlSpec.writable``),
* value checked against the profile range / allowed values and the device's own limits,
* encoded and written once — never retried (a timed-out write may have been executed),
* read back and compared (``readback_verification``); a mismatch raises and is reported,
* optional device-side expiry (watchdog register) so a lost EMS cannot leave a command active.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ems.core.models import Capability, Command, CommandAction, DeviceCategory, Metric
from ems.devices.base import DeviceDriver, DeviceUnavailableError, DriverError, DriverManifest, UnsupportedCommandError
from ems.devices.registry import register_driver
from ems.integrations.generic.mapping import BOOL_METRICS
from ems.integrations.modbus.codec import CodecError, decode, encode, register_count, scaled, unscaled
from ems.integrations.modbus.transport import ModbusError, ModbusTransport

MAX_BLOCK = 100
ALL_CATEGORIES = tuple(DeviceCategory)


class LibraryDriverMixin:
    """Instance-level facts for commissioning (driver_facts) derived from the profile."""

    profile = None

    def write_documented(self) -> bool:
        return bool(self.profile and any(c.writable for c in self.profile.writable_controls))

    def hardware_verified(self) -> bool:
        return bool(self.profile and self.profile.test_status == "HARDWARE_TESTED")

    def simulated_target(self) -> bool:
        """True only for the built-in test environment (simulator profile on this computer)."""
        return bool(self.profile and self.profile.simulator_only and self._is_local_lab())

    def _is_local_lab(self) -> bool:
        host = str(self.config.connection.get("host", ""))
        return host in ("127.0.0.1", "localhost") and getattr(self.context.simulator, "lab", None) is not None

    def documentation_ref(self) -> str | None:
        if not self.profile:
            return None
        return "; ".join(s.title for s in self.profile.documentation_sources)


def load_profile(config):
    from ems.devices.library.catalog import ProfileError, catalog
    pid = config.connection.get("profile_id")
    if not pid:
        return None, "geen apparaatprofiel gekozen"
    try:
        return catalog().get(str(pid)), None
    except ProfileError as exc:
        return None, str(exc)


def point_value(p, regs: list[int]) -> Any:
    raw = decode(regs, p.type, byte_order=p.byte_order, word_order=p.word_order)
    if p.enum is not None and raw is not None:
        return p.enum.get(str(raw), str(raw))
    value = scaled(raw, sf=p.sf, scale=p.scale, offset=p.offset)
    if isinstance(value, (int, float)) and p.invert:
        value = -value
    return value


def blocks(points: list, max_block: int = MAX_BLOCK) -> list[tuple[str, int, int, list]]:
    """Group points into (table, start, count, points) blocks of contiguous-ish registers."""
    out: list[tuple[str, int, int, list]] = []
    for table in ("holding", "input", "coil", "discrete"):
        pts = sorted((p for p in points if p.table == table and p.address is not None), key=lambda p: p.address)
        cur: list = []
        start = end = 0
        for p in pts:
            n = 1 if table in ("coil", "discrete") else register_count(p.type, p.size)
            if cur and p.address + n - start <= max_block and p.address <= end + 8:
                cur.append(p)
                end = max(end, p.address + n)
            else:
                if cur:
                    out.append((table, start, end - start, cur))
                cur, start, end = [p], p.address, p.address + n
        if cur:
            out.append((table, start, end - start, cur))
    return out


@register_driver
class ProfileModbusDriver(LibraryDriverMixin, DeviceDriver):
    manifest = DriverManifest(
        driver_id="library.modbus",
        display_name="Modbus (merk/model uit de apparatenbibliotheek)",
        vendor="Apparatenbibliotheek",
        categories=ALL_CATEGORIES,
        capabilities=frozenset(),          # per instance, from the profile
        connection_types=("modbus_tcp", "modbus_rtu"),
        documentation="per profiel (zie apparatenbibliotheek)",
        write_capable=True,                # only for documented controls of the chosen profile
        grid_meter_kind="modbus",
        notes="Registers, typen en schrijfbare functies komen uit een gevalideerd modelprofiel.",
        connection_schema={
            "profile_id": {"type": "string", "label_nl": "Apparaatprofiel", "readonly": True},
            "transport": {"type": "string", "enum": ["tcp", "rtu"], "default": "tcp",
                          "label_nl": "Hoe is het apparaat aangesloten?",
                          "enum_labels": {"tcp": "Netwerk (LAN/wifi, Modbus TCP)", "rtu": "RS485-kabel (Modbus RTU)"}},
            "host": {"type": "string", "label_nl": "IP-adres / hostnaam", "show_if": ["transport", "tcp"]},
            "port": {"type": "integer", "default": 502, "label_nl": "Poort", "level": "advanced",
                     "show_if": ["transport", "tcp"]},
            "serial_port": {"type": "string", "label_nl": "Seriële poort (COM3, /dev/ttyUSB0)",
                            "show_if": ["transport", "rtu"]},
            "baudrate": {"type": "integer", "default": 9600, "label_nl": "Baudrate", "level": "expert",
                         "show_if": ["transport", "rtu"]},
            "parity": {"type": "string", "enum": ["N", "E", "O"], "default": "N", "label_nl": "Pariteit",
                       "level": "expert", "show_if": ["transport", "rtu"]},
            "stopbits": {"type": "integer", "default": 1, "label_nl": "Stopbits", "level": "expert",
                         "show_if": ["transport", "rtu"]},
            "unit_id": {"type": "integer", "default": 1, "label_nl": "Unit-ID / slave-adres", "level": "advanced"},
            "timeout_s": {"type": "integer", "default": 3, "label_nl": "Time-out (s)", "level": "expert"},
            "retries": {"type": "integer", "default": 1, "label_nl": "Herhalingen bij lezen", "level": "expert"},
        },
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        self.profile, self.profile_error = load_profile(config)
        c = dict(self.profile.connection_parameters if self.profile else {}) | dict(config.connection)
        self.unit = int(c.get("unit_id", 1))
        self.transport = ModbusTransport.from_connection(c, int(c.get("port", 502)))
        self.diag: dict[str, Any] = {"reads": 0, "errors": 0, "points": {}, "writes": 0, "last_write": None}
        self._last_write: dict[str, float] = {}

    def modes(self) -> list[str]:
        """Heat-pump modes this profile documents (empty: no mode control)."""
        spec = self.profile.control("hp_mode") if self.profile else None
        return list(spec.values) if spec is not None and spec.writable and spec.values else []

    # ------------------------------------------------------------ capability
    def capabilities(self) -> frozenset[Capability]:
        if self.profile is None:
            return frozenset()
        caps = set(self.profile.read_capabilities)
        caps |= {c.capability for c in self.profile.writable_controls if c.writable}
        return frozenset(caps)

    async def connect(self) -> None:
        if self.profile is None:
            raise DriverError(self.profile_error or "geen profiel")
        await self.transport.connect()

    async def disconnect(self) -> None:
        await self.transport.close()

    async def _read_points(self, points: list) -> dict:
        out: dict = {}
        errors = []
        for table, start, count, pts in blocks(points):
            try:
                regs = await self.transport.read(table, start, count, self.unit)
            except ModbusError as exc:
                errors.append(f"{table} {start}+{count}: {exc}")
                continue
            for p in pts:
                if table in ("coil", "discrete"):
                    out[id(p)] = bool(regs[p.address - start])
                    continue
                n = register_count(p.type, p.size)
                try:
                    out[id(p)] = point_value(p, regs[p.address - start:p.address - start + n])
                except CodecError as exc:
                    errors.append(f"{p.metric or p.name}: {exc}")
        if errors:
            self.diag["errors"] += len(errors)
            self.diag["last_error"] = errors[-1]
        return out

    async def read(self) -> dict[Metric, Any]:
        if self.profile is None:
            raise DriverError(self.profile_error or "geen profiel")
        raw = await self._read_points(self.profile.points)
        values: dict[Metric, Any] = {}
        diag: dict[str, Any] = {}
        for p in self.profile.points:
            v = raw.get(id(p))
            if v is None:
                continue
            if p.metric is not None:
                if p.metric in BOOL_METRICS:
                    v = bool(v)
                if p.combine == "sum" and isinstance(values.get(p.metric), (int, float)) and isinstance(v, (int, float)):
                    values[p.metric] += v
                else:
                    values[p.metric] = v
            else:
                diag[p.name] = v
        if not values and not diag:
            raise DeviceUnavailableError(self.diag.get("last_error", "geen enkele waarde gelezen"))
        phases = [values.get(Metric(f"grid_power_l{n}_w")) for n in (1, 2, 3)]
        if Metric.GRID_POWER_W not in values and all(isinstance(x, (int, float)) for x in phases):
            values[Metric.GRID_POWER_W] = sum(phases)
        if Metric.GRID_POWER_W not in values and Metric.GRID_IMPORT_POWER_W in values \
                and Metric.GRID_EXPORT_POWER_W in values:
            values[Metric.GRID_POWER_W] = values[Metric.GRID_IMPORT_POWER_W] - values[Metric.GRID_EXPORT_POWER_W]
        self.diag["points"] = diag
        self.diag["reads"] += 1
        self.diag["last_read"] = time.time()
        return values

    async def identify(self) -> dict[str, Any]:
        """Identity strings (manufacturer/model/serial/firmware) from the profile's diagnostic points."""
        if self.profile is None:
            return {}
        pts = [p for p in self.profile.points if p.name in ("manufacturer", "model", "serial_number", "firmware_version")]
        raw = await self._read_points(pts)
        return {p.name: raw.get(id(p)) for p in pts if raw.get(id(p)) not in (None, "")}

    # -------------------------------------------------------------- writing
    def _encode(self, spec, command: Command) -> tuple[list[int] | bool, float | str]:
        if spec.fixed_value is not None and command.value is None:
            raw0 = spec.fixed_value
            return (bool(raw0) if spec.table == "coil" else encode(raw0, spec.type)), str(raw0)
        if spec.values is not None:
            key = str(command.value)
            if key not in spec.values:
                raise UnsupportedCommandError(f"waarde {command.value!r} niet toegestaan; kies uit {', '.join(spec.values)}")
            raw: float = spec.values[key]
            shown: float | str = key
        else:
            try:
                v = float(command.value)
            except (TypeError, ValueError):
                raise UnsupportedCommandError(f"ongeldige waarde {command.value!r}") from None
            if (spec.min is not None and v < spec.min) or (spec.max is not None and v > spec.max):
                raise UnsupportedCommandError(f"waarde {v:g} buiten het toegestane bereik {spec.min:g}–{spec.max:g}")
            raw, shown = unscaled(v, sf=spec.sf, scale=spec.scale, offset=spec.offset), v
        if spec.table == "coil":
            return bool(raw), shown
        return encode(raw, spec.type, byte_order=spec.byte_order, word_order=spec.word_order), shown

    async def apply(self, command: Command) -> None:
        if self.profile is None:
            raise UnsupportedCommandError(self.profile_error or "geen profiel")
        spec = self.profile.control(command.action)
        if spec is None or spec.address is None:
            raise UnsupportedCommandError(f"{command.action.value} wordt door dit model niet ondersteund")
        if not spec.writable:
            raise UnsupportedCommandError(f"{command.action.value}: schrijven niet geverifieerd voor dit model")
        min_gap = float(self.profile.safety_constraints.get("min_command_interval_s", 0))
        last = self._last_write.get(command.action.value)
        if min_gap and last and time.monotonic() - last < min_gap and command.action != CommandAction.BATTERY_AUTO:
            raise UnsupportedCommandError(f"te snel na de vorige opdracht (minimaal {min_gap:g} s)")
        payload, shown = self._encode(spec, command)
        timeout = float(self.profile.command_timeouts.get("write_s", 5))
        if spec.expires_s and spec.expiry_register is not None:
            await asyncio.wait_for(self.transport.write_registers(spec.expiry_register, [int(spec.expires_s)], self.unit),
                                   timeout)
        if spec.table == "coil":
            await asyncio.wait_for(self.transport.write_coil(spec.address, bool(payload), self.unit), timeout)
        else:
            await asyncio.wait_for(self.transport.write_registers(spec.address, payload, self.unit), timeout)
        self._last_write[command.action.value] = time.monotonic()
        self.diag["writes"] += 1
        self.diag["last_write"] = {"action": command.action.value, "value": shown, "ts": time.time()}
        if self.profile.readback_verification:
            await self._verify(spec, payload, shown)

    async def _verify(self, spec, payload, shown) -> None:
        from ems.devices.library.schema import Point
        rb = spec.readback or Point(name="readback", table="coil" if spec.table == "coil" else "holding",
                                    address=spec.address, type=spec.type, byte_order=spec.byte_order,
                                    word_order=spec.word_order)
        n = 1 if rb.table in ("coil", "discrete") else register_count(rb.type, rb.size)
        regs = await asyncio.wait_for(self.transport.read(rb.table, rb.address, n, self.unit),
                                      float(self.profile.command_timeouts.get("readback_s", 5)))
        if rb.table in ("coil", "discrete"):
            ok = bool(regs[0]) == bool(payload)
        elif spec.readback is None:
            ok = regs[:n] == list(payload)
        else:
            got = point_value(rb, regs)
            want = shown if isinstance(shown, str) else float(shown)
            ok = got == want if isinstance(want, str) else abs(float(got) - want) <= max(spec.readback_tolerance, 1e-6)
        self.diag["last_write"]["verified"] = ok
        if not ok:
            raise DriverError(f"teruglezen na schrijven komt niet overeen ({spec.action.value})")

    async def release_control(self) -> None:
        if self.profile is None:
            return
        for w in self.profile.release_writes:
            regs = encode(w["value"], w.get("type", "uint16"))
            await self.transport.write_registers(int(w["address"]), regs, self.unit)

    def diagnostics(self) -> dict[str, Any]:
        return {**self.diag, "transport": self.transport.stats, "target": self.transport.target,
                "profile": None if self.profile is None else self.profile.profile_id}
