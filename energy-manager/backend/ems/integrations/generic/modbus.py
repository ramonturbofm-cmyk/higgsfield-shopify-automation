"""Minimal async Modbus TCP client + generic read-only driver.

Protocol per the Modbus Organization specifications "MODBUS Application Protocol
Specification V1.1b3" and "MODBUS Messaging on TCP/IP Implementation Guide V1.0b":
MBAP header (transaction id, protocol id 0, length, unit id) + PDU. Only the read
functions 0x03 (holding registers) and 0x04 (input registers) are implemented —
this driver never writes.

Register addresses are protocol addresses (0-based). Many manuals list references
such as 40001 (holding) / 30001 (input): protocol address = reference − 40001 / − 30001.
Always check the device manual — some vendors already document 0-based addresses.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
import time
from typing import Any

from ems.core.models import Capability, Command, Metric
from ems.devices.base import DeviceDriver, DeviceUnavailableError, DriverError, DriverManifest, UnsupportedCommandError
from ems.devices.registry import register_driver
from ems.integrations.generic.mapping import (
    READ_CATEGORIES,
    VALUES_SCHEMA,
    MappingError,
    capabilities_for,
    convert,
    finish,
    parse_entries,
)

FUNCTIONS = {"holding": 0x03, "input": 0x04}
TYPES = {  # name -> (registers, struct format of the big-endian byte string)
    "uint16": (1, ">H"), "int16": (1, ">h"),
    "uint32": (2, ">I"), "int32": (2, ">i"), "float32": (2, ">f"),
    "uint64": (4, ">Q"), "int64": (4, ">q"),
}
EXCEPTIONS = {1: "illegal function", 2: "illegal data address", 3: "illegal data value",
              4: "server device failure", 6: "server device busy", 10: "gateway path unavailable",
              11: "gateway target device failed to respond"}


class ModbusError(DriverError):
    pass


class ModbusTCPClient:
    def __init__(self, host: str, port: int = 502, timeout: float = 3.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tid = 0
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        await self.close()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout)
        except (OSError, TimeoutError) as exc:
            raise DeviceUnavailableError(f"Modbus TCP {self.host}:{self.port} niet bereikbaar: {exc}") from exc

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._reader = self._writer = None

    async def read_registers(self, unit: int, function: int, address: int, count: int) -> list[int]:
        if function not in (0x03, 0x04):
            raise ModbusError("alleen lezen (functie 3/4) wordt ondersteund")
        if not 1 <= count <= 125 or not 0 <= address <= 0xFFFF:
            raise ModbusError(f"ongeldig adres/aantal: {address}/{count}")
        async with self._lock:
            if not self.connected:
                await self.connect()
            self._tid = (self._tid + 1) & 0xFFFF
            pdu = struct.pack(">BHH", function, address, count)
            frame = struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, unit) + pdu
            try:
                self._writer.write(frame)
                await self._writer.drain()
                header = await asyncio.wait_for(self._reader.readexactly(7), self.timeout)
                tid, proto, length, r_unit = struct.unpack(">HHHB", header)
                if length < 2 or length > 260:
                    raise ModbusError(f"ongeldige MBAP-lengte {length}")
                body = await asyncio.wait_for(self._reader.readexactly(length - 1), self.timeout)
            except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
                await self.close()
                raise DeviceUnavailableError(f"Modbus TCP {self.host}:{self.port}: {exc or 'timeout'}") from exc
            if tid != self._tid or proto != 0 or r_unit != unit:
                await self.close()  # out of sync: start over on the next request
                raise ModbusError("antwoord hoort niet bij het verzoek (transactie/unit)")
            fc = body[0]
            if fc == function | 0x80:
                code = body[1] if len(body) > 1 else 0
                raise ModbusError(f"Modbus-uitzondering {code}: {EXCEPTIONS.get(code, 'onbekend')}")
            if fc != function or len(body) < 2 or body[1] != 2 * count or len(body) != 2 + 2 * count:
                raise ModbusError("onverwacht Modbus-antwoord")
            return list(struct.unpack(f">{count}H", body[2:]))


def decode(registers: list[int], dtype: str, word_order: str = "big") -> float:
    n, fmt = TYPES[dtype]
    words = registers[:n]
    if word_order == "little":
        words = list(reversed(words))
    return struct.unpack(fmt, struct.pack(f">{n}H", *words))[0]


@register_driver
class GenericModbusTCPDriver(DeviceDriver):
    manifest = DriverManifest(
        driver_id="generic.modbus_tcp",
        display_name="Generiek Modbus TCP (alleen lezen)",
        vendor="Generiek",
        categories=READ_CATEGORIES,
        capabilities=frozenset(c for c in Capability if c.value.startswith("read_")),
        connection_types=("modbus_tcp",),
        documentation="Modbus Application Protocol V1.1b3; registers uit de handleiding van het apparaat",
        grid_meter_kind="modbus",
        notes="Registeradressen, typen en schaal neemt u over uit de officiële documentatie van het apparaat. "
              "Er wordt nooit naar het apparaat geschreven.",
        connection_schema={
            "host": {"type": "string", "label_nl": "IP-adres / hostnaam"},
            "port": {"type": "integer", "default": 502, "label_nl": "Poort"},
            "unit_id": {"type": "integer", "default": 1, "label_nl": "Unit-ID / slave-adres"},
            "timeout_s": {"type": "integer", "default": 3, "label_nl": "Time-out (s)"},
            "values": {**VALUES_SCHEMA, "example": [
                {"metric": "grid_power_w", "function": "input", "address": 0, "type": "int32",
                 "word_order": "big", "scale": 1.0}]},
        },
    )

    def __init__(self, config, context) -> None:
        super().__init__(config, context)
        c = config.connection
        self.unit = int(c.get("unit_id", 1))
        self.client = ModbusTCPClient(str(c.get("host", "")), int(c.get("port", 502)), float(c.get("timeout_s", 3)))
        self._entries: list[dict] | None = None
        self.diag: dict[str, Any] = {"reads": 0, "errors": 0}

    def entries(self) -> list[dict]:
        if self._entries is None:
            entries = parse_entries(self.config.connection.get("values"), ("address",))
            for e in entries:
                e.setdefault("function", "holding")
                e.setdefault("type", "uint16")
                if e["function"] not in FUNCTIONS:
                    raise MappingError(f"{e['metric']}: function moet holding of input zijn")
                if e["type"] not in TYPES:
                    raise MappingError(f"{e['metric']}: type moet een van {', '.join(TYPES)} zijn")
                if e.get("word_order", "big") not in ("big", "little"):
                    raise MappingError(f"{e['metric']}: word_order moet big of little zijn")
            self._entries = entries
        return self._entries

    def capabilities(self) -> frozenset[Capability]:
        try:
            return capabilities_for(self.entries())
        except MappingError:
            return frozenset()

    async def connect(self) -> None:
        if not self.client.host:
            raise DriverError("geen host ingesteld")
        self.entries()
        await self.client.connect()

    async def disconnect(self) -> None:
        await self.client.close()

    async def read(self) -> dict[Metric, Any]:
        values: dict[Metric, Any] = {}
        for e in self.entries():
            n = TYPES[e["type"]][0]
            try:
                regs = await self.client.read_registers(self.unit, FUNCTIONS[e["function"]], int(e["address"]), n)
            except ModbusError as exc:
                self.diag["errors"] += 1
                self.diag["last_error"] = f"{e['metric']}: {exc}"
                continue
            value = convert(e, decode(regs, e["type"], e.get("word_order", "big")))
            if value is not None:
                values[e["metric"]] = value
        if not values:
            raise DeviceUnavailableError(self.diag.get("last_error", "geen enkele waarde gelezen"))
        self.diag["reads"] += 1
        self.diag["last_read"] = time.time()
        return finish(values)

    async def apply(self, command: Command) -> None:
        raise UnsupportedCommandError("generieke Modbus-driver is alleen-lezen")

    async def release_control(self) -> None:
        return None
