"""Direct DSMR P1 reading: parser, CRC, sign handling, TCP transport, GridMeter selection."""

import asyncio

import pytest

from conftest import make_config
from ems.core.clock import SystemClock
from ems.core.config import DeviceConfig
from ems.core.models import Metric
from ems.devices.base import DeviceUnavailableError, DriverContext
from ems.devices.registry import registry
from ems.gridmeter import GridMeterStatus, select_primary_grid_meter
from ems.integrations.dsmr.driver import DSMRP1Driver
from ems.integrations.dsmr.parser import TelegramBuffer, TelegramError, crc16, parse_telegram, to_metrics

BODY = (
    "/ISK5\\2M550T-1012\r\n\r\n"
    "1-3:0.2.8(50)\r\n"
    "0-0:1.0.0(260706143010S)\r\n"
    "0-0:96.1.1(4530303434303037313331363530363136)\r\n"
    "1-0:1.8.1(001234.567*kWh)\r\n"
    "1-0:1.8.2(002345.678*kWh)\r\n"
    "1-0:2.8.1(000123.456*kWh)\r\n"
    "1-0:2.8.2(000234.567*kWh)\r\n"
    "0-0:96.14.0(0002)\r\n"
    "1-0:1.7.0(00.000*kW)\r\n"
    "1-0:2.7.0(02.150*kW)\r\n"
    "1-0:32.7.0(231.0*V)\r\n1-0:52.7.0(230.5*V)\r\n1-0:72.7.0(229.9*V)\r\n"
    "1-0:31.7.0(010*A)\r\n1-0:51.7.0(001*A)\r\n1-0:71.7.0(001*A)\r\n"
    "1-0:21.7.0(00.000*kW)\r\n1-0:41.7.0(00.250*kW)\r\n1-0:61.7.0(00.120*kW)\r\n"
    "1-0:22.7.0(02.520*kW)\r\n1-0:42.7.0(00.000*kW)\r\n1-0:62.7.0(00.000*kW)\r\n"
    "!"
)


def telegram(body=BODY):
    return body + f"{crc16(body.encode()):04X}\r\n"


def test_crc16_known_value():
    assert crc16(b"123456789") == 0xBB3D  # CRC-16/ARC check value


def test_parse_and_map_with_signed_phases():
    tg = parse_telegram(telegram())
    assert tg.crc_checked and tg.values["version"] == "50"
    assert tg.timestamp.isoformat() == "2026-07-06T14:30:10+02:00"
    m = to_metrics(tg)
    assert m[Metric.GRID_POWER_W] == -2150.0
    assert m[Metric.GRID_POWER_L1_W] == -2520.0 and m[Metric.GRID_POWER_L2_W] == 250.0
    assert m[Metric.GRID_CURRENT_L1_A] == -10.0 and m[Metric.GRID_CURRENT_L2_A] == 1.0
    assert m[Metric.GRID_IMPORT_ENERGY_KWH] == pytest.approx(3580.245)
    assert m[Metric.GRID_EXPORT_ENERGY_KWH] == pytest.approx(358.023)


def test_crc_mismatch_rejected():
    bad = telegram().replace("02.150*kW", "09.150*kW")
    with pytest.raises(TelegramError, match="CRC"):
        parse_telegram(bad)


def test_legacy_without_crc_only_when_allowed():
    legacy = BODY + "\r\n"
    assert parse_telegram(legacy).crc_checked is False
    with pytest.raises(TelegramError):
        parse_telegram(legacy, require_crc=True)


def test_buffer_reassembles_split_stream():
    buf = TelegramBuffer()
    t = telegram()
    out = buf.feed("garbage" + t[:100]) + buf.feed(t[100:] + t[:20])
    assert len(out) == 1 and out[0] == t


async def test_tcp_transport_driver():
    t = telegram()

    async def serve(reader, writer):
        try:
            for _ in range(50):
                writer.write(t.encode())
                await writer.drain()
                await asyncio.sleep(0.1)
        except (ConnectionError, asyncio.CancelledError):
            pass

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg = DeviceConfig(id="p1", name="P1", category="smart_meter", driver="dsmr.p1",
                       connection={"transport": "tcp", "host": "127.0.0.1", "tcp_port": port})
    drv = DSMRP1Driver(cfg, DriverContext(SystemClock()))
    await drv.connect()
    values = await drv.read()
    assert values[Metric.GRID_POWER_W] == -2150.0
    assert drv.diagnostics()["telegrams"] >= 1
    await drv.disconnect()
    server.close()


async def test_unreachable_tcp_bridge():
    cfg = DeviceConfig(id="p1", name="P1", category="smart_meter", driver="dsmr.p1",
                       connection={"transport": "tcp", "host": "127.0.0.1", "tcp_port": 1})
    with pytest.raises(DeviceUnavailableError):
        await DSMRP1Driver(cfg, DriverContext(SystemClock())).connect()


def test_selection_prefers_homewizard_then_dsmr_and_only_offers_generic():
    hw = {"id": "hw", "name": "HomeWizard", "category": "smart_meter", "driver": "homewizard.p1"}
    ds = {"id": "ds", "name": "DSMR", "category": "smart_meter", "driver": "dsmr.p1"}
    assert select_primary_grid_meter(make_config([ds, hw]), registry).device_id == "hw"
    assert select_primary_grid_meter(make_config([ds]), registry).device_id == "ds"
    explicit = select_primary_grid_meter(make_config([hw, {**ds, "role": "primary_grid_meter"}]), registry)
    assert explicit.device_id == "ds" and explicit.status == GridMeterStatus.PRIMARY_EXPLICIT
    none = select_primary_grid_meter(make_config([]), registry)
    assert none.status == GridMeterStatus.NO_PRIMARY_GRID_METER
    assert "Sommige EMS-functies zijn beperkt" in none.reason
