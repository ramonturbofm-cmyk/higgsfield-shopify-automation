"""Generic read-only drivers: Modbus TCP (against a spec-conform fake server), HTTP/JSON, MQTT."""

import asyncio
import json
import shutil
import socket
import struct
import subprocess
import time

import pytest

from ems.core.clock import SystemClock
from ems.core.config import DeviceConfig
from ems.core.models import Capability, Metric
from ems.devices.base import DeviceUnavailableError, DriverContext, UnsupportedCommandError
from ems.integrations.generic.mapping import MappingError, json_path, parse_entries
from ems.integrations.generic.modbus import GenericModbusTCPDriver, ModbusError, ModbusTCPClient, decode
from ems.integrations.generic.mqtt import GenericMQTTDriver
from ems.integrations.generic.rest import GenericHTTPJSONDriver


def ctx(secrets=None):
    return DriverContext(SystemClock(), secrets=secrets)


def dev(driver, connection, category="smart_meter"):
    return DeviceConfig(id="g1", name="Generiek", category=category, driver=driver, connection=connection)


# ------------------------------------------------------------------ Modbus TCP
class FakeModbusServer:
    """Implements read holding (0x03) / input (0x04) registers per the Modbus TCP spec."""

    def __init__(self, holding=None, inputs=None, unit=1):
        self.tables = {3: holding or {}, 4: inputs or {}}
        self.unit = unit
        self.requests = 0

    async def handle(self, reader, writer):
        try:
            while True:
                tid, proto, length, unit = struct.unpack(">HHHB", await reader.readexactly(7))
                pdu = await reader.readexactly(length - 1)
                self.requests += 1
                fc, addr, count = struct.unpack(">BHH", pdu)
                table = self.tables.get(fc)
                if table is None:
                    resp = bytes([fc | 0x80, 1])
                elif any(a not in table for a in range(addr, addr + count)):
                    resp = bytes([fc | 0x80, 2])
                else:
                    regs = [table[a] for a in range(addr, addr + count)]
                    resp = bytes([fc, 2 * count]) + struct.pack(f">{count}H", *regs)
                writer.write(struct.pack(">HHHB", tid, 0, len(resp) + 1, unit) + resp)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]


def regs32(value, fmt=">i"):
    hi, lo = struct.unpack(">2H", struct.pack(fmt, value))
    return hi, lo


def test_decode_types_and_word_order():
    assert decode([0xFFFF], "int16") == -1
    assert decode([0xFFFF], "uint16") == 65535
    hi, lo = regs32(-2150)
    assert decode([hi, lo], "int32") == -2150
    assert decode([lo, hi], "int32", "little") == -2150
    fh, fl = regs32(230.5, ">f")
    assert decode([fh, fl], "float32") == pytest.approx(230.5)
    assert decode([0, 0, 0x0001, 0x0000], "uint64") == 65536


async def test_modbus_driver_reads_and_maps():
    hi, lo = regs32(-2150)
    fh, fl = regs32(231.2, ">f")
    srv = FakeModbusServer(holding={10: hi, 11: lo, 20: fh, 21: fl}, inputs={0: 52, 1: 1234})
    port = await srv.start()
    cfg = dev("generic.modbus_tcp", {"host": "127.0.0.1", "port": port, "unit_id": 1, "values": [
        {"metric": "grid_power_w", "address": 10, "type": "int32"},
        {"metric": "grid_voltage_l1_v", "address": 20, "type": "float32"},
        {"metric": "grid_current_l1_a", "function": "input", "address": 0, "type": "uint16", "scale": 0.1},
        {"metric": "grid_import_energy_kwh", "function": "input", "address": 1, "type": "uint16", "scale": 0.01},
    ]})
    drv = GenericModbusTCPDriver(cfg, ctx())
    await drv.connect()
    v = await drv.read()
    assert v[Metric.GRID_POWER_W] == -2150
    assert v[Metric.GRID_VOLTAGE_L1_V] == pytest.approx(231.2, abs=1e-4)
    assert v[Metric.GRID_CURRENT_L1_A] == pytest.approx(5.2)
    assert v[Metric.GRID_IMPORT_ENERGY_KWH] == pytest.approx(12.34)
    assert Capability.READ_GRID_POWER in drv.capabilities()
    with pytest.raises(UnsupportedCommandError):
        await drv.apply(None)  # type: ignore[arg-type]
    report = await drv.self_test()
    assert report.reachable and "read_grid_power" in report.available
    await drv.disconnect()
    srv.server.close()


async def test_modbus_exception_and_partial_values():
    srv = FakeModbusServer(holding={0: 100})
    port = await srv.start()
    client = ModbusTCPClient("127.0.0.1", port)
    with pytest.raises(ModbusError, match="illegal data address"):
        await client.read_registers(1, 3, 5, 1)
    assert await client.read_registers(1, 3, 0, 1) == [100]   # connection still usable
    with pytest.raises(ModbusError):
        await client.read_registers(1, 6, 0, 1)                 # writes are refused client-side
    await client.close()
    cfg = dev("generic.modbus_tcp", {"host": "127.0.0.1", "port": port, "values": [
        {"metric": "grid_import_power_w", "address": 0}, {"metric": "grid_export_power_w", "address": 9}]})
    drv = GenericModbusTCPDriver(cfg, ctx())
    v = await drv.read()
    assert v == {Metric.GRID_IMPORT_POWER_W: 100.0}            # missing register: value absent, not invented
    assert "illegal data address" in drv.diag["last_error"]
    await drv.disconnect()
    srv.server.close()


async def test_modbus_unreachable_and_bad_mapping():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    drv = GenericModbusTCPDriver(dev("generic.modbus_tcp", {"host": "127.0.0.1", "port": port,
                                                            "values": [{"metric": "grid_power_w", "address": 0}]}), ctx())
    with pytest.raises(DeviceUnavailableError):
        await drv.connect()
    bad = GenericModbusTCPDriver(dev("generic.modbus_tcp", {"host": "x", "values": [
        {"metric": "grid_power_w", "address": 0, "type": "int128"}]}), ctx())
    with pytest.raises(MappingError):
        bad.entries()
    assert bad.capabilities() == frozenset()


def test_mapping_validation_and_json_path():
    with pytest.raises(MappingError, match="geen waardetoewijzing"):
        parse_entries([], ("path",))
    with pytest.raises(MappingError, match="onbekende metric"):
        parse_entries([{"metric": "made_up", "path": "a"}], ("path",))
    with pytest.raises(MappingError, match="ontbreekt path"):
        parse_entries([{"metric": "grid_power_w"}], ("path",))
    assert parse_entries('[{"metric": "pv_power_w", "path": "a"}]', ("path",))[0]["metric"] == Metric.PV_POWER_W
    doc = {"a": {"b": [{"c": 5}]}}
    assert json_path(doc, "a.b.0.c") == 5 and json_path(doc, "a.x") is None and json_path(doc, "a.b.3") is None


# ------------------------------------------------------------------ HTTP/JSON
async def _http_server(payload, status=200, seen=None):
    async def handle(reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        if seen is not None:
            seen.append(head)
        body = json.dumps(payload).encode()
        writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                     f"Connection: close\r\n\r\n".encode() + body)
        await writer.drain()
        writer.close()
    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


class Store(dict):
    def get(self, k, d=None):
        return super().get(k, d)


async def test_http_json_driver():
    seen = []
    srv, port = await _http_server({"data": {"import_w": 900, "export_w": 0, "pv": {"w": 1.5}}}, seen=seen)
    cfg = dev("generic.http_json", {"url": f"http://127.0.0.1:{port}/status", "auth_token": "secret:device.g1.auth_token",
                                    "values": [{"metric": "grid_import_power_w", "path": "data.import_w"},
                                               {"metric": "grid_export_power_w", "path": "data.export_w"},
                                               {"metric": "pv_power_w", "path": "data.pv.w", "scale": 1000}]})
    drv = GenericHTTPJSONDriver(cfg, ctx(Store({"device.g1.auth_token": "Bearer abc"})))
    await drv.connect()
    v = await drv.read()
    assert v[Metric.GRID_POWER_W] == 900 and v[Metric.PV_POWER_W] == 1500
    assert "authorization: bearer abc" in seen[0].lower()
    assert Capability.READ_GRID_POWER in drv.capabilities()
    await drv.disconnect()
    srv.close()
    srv, port = await _http_server({"x": 1}, status=500)
    drv = GenericHTTPJSONDriver(dev("generic.http_json", {"url": f"http://127.0.0.1:{port}/",
                                                          "values": [{"metric": "grid_power_w", "path": "x"}]}), ctx())
    with pytest.raises(DeviceUnavailableError, match="HTTP 500"):
        await drv.read()
    await drv.disconnect()
    srv.close()


# ------------------------------------------------------------------ MQTT
def test_mqtt_message_mapping_and_staleness():
    drv = GenericMQTTDriver(dev("generic.mqtt", {"host": "broker", "stale_after_s": 60, "values": [
        {"metric": "grid_power_w", "topic": "meter/state", "path": "power"},
        {"metric": "battery_soc_pct", "topic": "bat/soc"},
        {"metric": "battery_power_w", "topic": "bat/p", "invert": True}]}, category="smart_meter"), ctx())
    drv.on_message("meter/state", b'{"power": -1200}')
    drv.on_message("bat/soc", b"57.5")
    drv.on_message("bat/p", b"not a number")
    v = asyncio.run(drv.read())
    assert v == {Metric.GRID_POWER_W: -1200.0, Metric.BATTERY_SOC_PCT: 57.5}
    drv._latest[Metric.GRID_POWER_W] = (1.0, time.monotonic() - 120)   # stale value is dropped
    assert Metric.GRID_POWER_W not in asyncio.run(drv.read())


@pytest.mark.skipif(shutil.which("mosquitto") is None or shutil.which("mosquitto_pub") is None,
                    reason="mosquitto broker not installed")
async def test_mqtt_against_real_broker(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    conf = tmp_path / "m.conf"
    conf.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\n")
    broker = subprocess.Popen(["mosquitto", "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        await asyncio.sleep(0.5)
        subprocess.run(["mosquitto_pub", "-p", str(port), "-t", "p1/power", "-r", "-m", '{"w": 640}'], check=True)
        drv = GenericMQTTDriver(dev("generic.mqtt", {"host": "127.0.0.1", "port": port, "values": [
            {"metric": "grid_power_w", "topic": "p1/power", "path": "w"}]}), ctx())
        report = await drv.self_test()
        assert report.reachable and report.sample["grid_power_w"] == 640
        await drv.disconnect()
    finally:
        broker.terminate()
        broker.wait(5)
