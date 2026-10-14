"""Modbus TCP / RTU transport on pymodbus (a maintained implementation; no own protocol code).

* One lock per connection: one request at a time (RTU bus contention, many devices allow one TCP client).
* Bounded retries: pymodbus retries a request at most ``retries`` times; the transport never loops.
* Rate limit: at least ``min_interval_s`` between requests (protects small devices and gateways).
* Writes are never retried after a timeout (the device may have executed them): the caller reads back.
* Exceptions are mapped to clear Dutch messages.

Addresses are protocol addresses (0-based). Many manuals list 40001 (holding) / 30001 (input)
references; profiles store protocol addresses.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ems.devices.base import DeviceUnavailableError, DriverError

EXCEPTIONS = {1: "functie niet ondersteund door het apparaat", 2: "registeradres bestaat niet",
              3: "ongeldige waarde", 4: "storing in het apparaat", 5: "bevestigd, nog bezig", 6: "apparaat bezet",
              10: "gateway: pad niet beschikbaar", 11: "gateway: apparaat antwoordt niet"}


class ModbusError(DriverError):
    """Device answered with a Modbus exception or an invalid response."""


class ModbusTransport:
    """``kind`` = "tcp" (host, port) or "rtu" (serial port, baudrate, parity, stopbits)."""

    def __init__(self, kind: str = "tcp", *, host: str = "", port: int = 502, serial_port: str = "",
                 baudrate: int = 9600, parity: str = "N", stopbits: int = 1, bytesize: int = 8,
                 timeout: float = 3.0, retries: int = 1, min_interval_s: float = 0.0) -> None:
        if kind not in ("tcp", "rtu"):
            raise DriverError("Modbus-verbinding moet tcp of rtu zijn")
        self.kind, self.host, self.port = kind, host, int(port)
        self.serial_port, self.baudrate, self.parity = serial_port, int(baudrate), parity
        self.stopbits, self.bytesize = int(stopbits), int(bytesize)
        self.timeout, self.retries, self.min_interval_s = float(timeout), int(retries), float(min_interval_s)
        self._client = None
        self._lock = asyncio.Lock()
        self._last = 0.0
        self.stats: dict[str, Any] = {"requests": 0, "errors": 0, "reconnects": 0, "last_error": None, "last_ok": None}

    @classmethod
    def from_connection(cls, c: dict[str, Any], default_port: int = 502) -> ModbusTransport:
        kind = str(c.get("transport") or ("rtu" if c.get("serial_port") else "tcp"))
        return cls(kind, host=str(c.get("host", "")), port=int(c.get("port", default_port)),
                   serial_port=str(c.get("serial_port", "")), baudrate=int(c.get("baudrate", 9600)),
                   parity=str(c.get("parity", "N")), stopbits=int(c.get("stopbits", 1)),
                   timeout=float(c.get("timeout_s", 3)), retries=int(c.get("retries", 1)),
                   min_interval_s=float(c.get("min_interval_s", 0.0)))

    @property
    def target(self) -> str:
        return f"{self.host}:{self.port}" if self.kind == "tcp" else f"{self.serial_port} @ {self.baudrate} baud"

    def _make(self):
        from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
        if self.kind == "tcp":
            if not self.host:
                raise DriverError("geen IP-adres/hostnaam ingesteld")
            return AsyncModbusTcpClient(self.host, port=self.port, timeout=self.timeout, retries=self.retries,
                                        reconnect_delay=0, reconnect_delay_max=0)
        if not self.serial_port:
            raise DriverError("geen seriële poort ingesteld (bijv. COM3 of /dev/ttyUSB0)")
        return AsyncModbusSerialClient(self.serial_port, baudrate=self.baudrate, parity=self.parity,
                                       stopbits=self.stopbits, bytesize=self.bytesize, timeout=self.timeout,
                                       retries=self.retries, reconnect_delay=0, reconnect_delay_max=0)

    async def connect(self) -> None:
        if self._client is not None and self._client.connected:
            return
        await self.close()
        self._client = self._make()
        try:
            ok = await asyncio.wait_for(self._client.connect(), self.timeout + 1)
        except (OSError, TimeoutError) as exc:
            ok = False
            self.stats["last_error"] = str(exc)
        if not ok:
            self._client = None
            self.stats["errors"] += 1
            raise DeviceUnavailableError(f"Modbus {self.target} niet bereikbaar")
        self.stats["reconnects"] += 1

    async def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None

    async def _call(self, fn: str, *args, write: bool = False, **kw):
        async with self._lock:
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            await self.connect()
            from pymodbus.exceptions import ModbusException
            self.stats["requests"] += 1
            try:
                rr = await asyncio.wait_for(getattr(self._client, fn)(*args, **kw),
                                            self.timeout * (self.retries + 1) + 1)
            except (ModbusException, OSError, TimeoutError) as exc:
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{type(exc).__name__}: {exc}"
                await self.close()
                what = "schrijfopdracht: resultaat onbekend (wordt teruggelezen)" if write else "geen antwoord"
                raise DeviceUnavailableError(f"Modbus {self.target}: {what}") from exc
            finally:
                self._last = time.monotonic()
            if rr.isError():
                code = getattr(rr, "exception_code", 0)
                self.stats["errors"] += 1
                self.stats["last_error"] = f"uitzondering {code}"
                raise ModbusError(f"Modbus-uitzondering {code}: {EXCEPTIONS.get(code, 'onbekend')}")
            self.stats["last_ok"] = time.time()
            return rr

    async def read(self, table: str, address: int, count: int, unit: int) -> list[int] | list[bool]:
        if not 0 <= address <= 0xFFFF or not 1 <= count <= 125:
            raise ModbusError(f"ongeldig adres/aantal: {address}/{count}")
        fn = {"holding": "read_holding_registers", "input": "read_input_registers",
              "coil": "read_coils", "discrete": "read_discrete_inputs"}.get(table)
        if fn is None:
            raise ModbusError(f"onbekende registertabel {table!r}")
        rr = await self._call(fn, address, count=count, device_id=unit)
        if table in ("coil", "discrete"):
            return list(rr.bits[:count])
        if len(rr.registers) != count:
            raise ModbusError("onvolledig Modbus-antwoord")
        return list(rr.registers)

    async def write_registers(self, address: int, values: list[int], unit: int) -> None:
        if len(values) == 1:
            await self._call("write_register", address, values[0], device_id=unit, write=True)
        else:
            await self._call("write_registers", address, values, device_id=unit, write=True)

    async def write_coil(self, address: int, value: bool, unit: int) -> None:
        await self._call("write_coil", address, bool(value), device_id=unit, write=True)
