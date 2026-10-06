"""HomeWizard P1 integration (official API v1/v2) — mapping, pairing, TLS, WebSocket, driver."""

import asyncio
import json

import httpx
import pytest

from conftest import make_config
from ems.core.clock import SystemClock
from ems.core.config import DeviceConfig
from ems.core.models import Metric
from ems.devices.base import DeviceUnavailableError, DriverContext
from ems.devices.registry import registry
from ems.gridmeter import GridMeterStatus, select_primary_grid_meter
from ems.integrations.homewizard import client as hwc
from ems.integrations.homewizard.driver import HomeWizardP1Driver
from ems.integrations.homewizard.protocol import CA_CERT, cert_hostname, map_data_v1, map_measurement_v2
from ems.security.secrets import MemorySecretStore
from fake_homewizard import MEASUREMENT, FakeP1, make_ca_and_cert


def test_bundled_ca_is_homewizard_appliance_ca():
    from cryptography import x509
    cert = x509.load_pem_x509_certificate(CA_CERT.read_bytes())
    assert "Appliance Access CA" in cert.subject.rfc4514_string()


def test_cert_hostname_uses_legacy_product_type():
    assert cert_hostname("5C2FAFAABBCC") == "appliance/p1dongle/5c2fafaabbcc"


def test_map_v2_three_phase_example_from_docs():
    m = map_measurement_v2(MEASUREMENT)
    assert m[Metric.GRID_POWER_W] == -543
    assert m[Metric.GRID_EXPORT_POWER_W] == 543 and m[Metric.GRID_IMPORT_POWER_W] == 0
    assert (m[Metric.GRID_POWER_L1_W], m[Metric.GRID_POWER_L2_W]) == (-676, 133)
    assert m[Metric.GRID_CURRENT_L1_A] == -4
    assert m[Metric.GRID_IMPORT_ENERGY_KWH] == 13779.338


def test_map_v2_single_phase_sums_tariffs_and_keeps_missing_missing():
    m = map_measurement_v2({"energy_import_t1_kwh": 10830.511, "energy_import_t2_kwh": 2948.827,
                            "energy_export_t1_kwh": 1285.951, "energy_export_t2_kwh": 2876.51,
                            "power_w": -678, "power_l1_w": -676})
    assert m[Metric.GRID_IMPORT_ENERGY_KWH] == pytest.approx(13779.338)
    assert m[Metric.GRID_EXPORT_ENERGY_KWH] == pytest.approx(4162.461)
    assert Metric.GRID_POWER_L2_W not in m and Metric.GRID_VOLTAGE_L1_V not in m


def test_map_v1_current_sign_from_phase_power():
    m = map_data_v1({"active_power_w": -500, "active_power_l1_w": -500, "active_current_l1_a": 2.2,
                     "active_voltage_l1_v": 231, "total_power_import_kwh": 1.0, "total_power_export_kwh": 2.0})
    assert m[Metric.GRID_CURRENT_L1_A] == -2.2 and m[Metric.GRID_VOLTAGE_L1_V] == 231


async def test_pairing_requires_button_press():
    pressed = {"v": False}

    def handler(request: httpx.Request):
        assert request.headers["X-Api-Version"] == "2"
        if request.url.path == "/api/user" and request.method == "POST":
            assert json.loads(request.content) == {"name": "local/energy-manager"}
            if not pressed["v"]:
                return httpx.Response(403, json={"error": "user:creation-not-enabled"})
            return httpx.Response(200, json={"name": "local/energy-manager", "token": "A" * 32})
        if request.headers.get("Authorization") != "Bearer " + "A" * 32:
            return httpx.Response(401, json={"error": "user:unauthorized"})
        return httpx.Response(200, json={"product_type": "HWE-P1", "serial": "5c2fafaabbcc"})

    c = hwc.HomeWizardV2Client("p1.local", "5c2fafaabbcc", transport=httpx.MockTransport(handler))
    with pytest.raises(hwc.Unauthorized):
        await c.device_info()
    with pytest.raises(hwc.ButtonPressRequired):
        await c.create_user()
    pressed["v"] = True
    assert await c.create_user() == "A" * 32
    assert (await c.device_info())["product_type"] == "HWE-P1"
    await c.close()


async def test_v1_disabled_api_is_explained():
    c = hwc.HomeWizardV1Client("p1", transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(hwc.HomeWizardError, match="Local API"):
        await c.data()
    await c.close()


@pytest.fixture
async def fake(tmp_path):
    ca, cert, key = make_ca_and_cert(tmp_path)
    dev = await FakeP1(cert, key).start()
    dev.tokens.add("B" * 32)
    dev.ca = ca
    yield dev
    await dev.stop()


async def test_tls_validates_device_hostname_against_ca(fake):
    good = hwc.HomeWizardV2Client("127.0.0.1", "5c2fafaabbcc", "B" * 32, cafile=fake.ca, port=fake.port)
    assert (await good.device_info())["serial"] == "5c2fafaabbcc"
    assert (await good.measurement())["power_w"] == -543
    await good.close()
    wrong = hwc.HomeWizardV2Client("127.0.0.1", "000000000000", "B" * 32, cafile=fake.ca, port=fake.port)
    with pytest.raises(hwc.HomeWizardError):
        await wrong.device_info()        # certificate is for another device
    await wrong.close()
    untrusted = hwc.HomeWizardV2Client("127.0.0.1", "5c2fafaabbcc", "B" * 32, port=fake.port)  # real HW CA
    with pytest.raises(hwc.HomeWizardError):
        await untrusted.device_info()
    await untrusted.close()


async def test_probe_identity_learns_serial_only_for_trusted_chain(fake, tmp_path):
    ident = await hwc.probe_identity("127.0.0.1", fake.port, cafile=fake.ca)
    assert ident["serial"] == "5c2fafaabbcc" and ident["hostname"] == "appliance/p1dongle/5c2fafaabbcc"
    with pytest.raises(hwc.HomeWizardError, match="niet uitgegeven door HomeWizard"):
        await hwc.probe_identity("127.0.0.1", fake.port)   # bundled real CA does not sign the fake


async def test_websocket_stream_follows_documented_handshake(fake):
    c = hwc.HomeWizardV2Client("127.0.0.1", "5c2fafaabbcc", "B" * 32, cafile=fake.ca, port=fake.port)
    agen = c.stream("measurement")
    first = await asyncio.wait_for(agen.__anext__(), 5)
    assert first["power_w"] == -543
    await agen.aclose()
    await c.close()


def _driver(fake, monkeypatch, token="B" * 32):
    monkeypatch.setattr("ems.integrations.homewizard.client.CA_CERT", fake.ca)
    monkeypatch.setattr("ems.integrations.homewizard.protocol.CA_CERT", fake.ca)
    orig = hwc.HomeWizardV2Client.__init__

    def init(self, host, serial, token=None, **kw):
        kw.setdefault("cafile", fake.ca)
        kw.setdefault("port", fake.port)
        orig(self, host, serial, token, **kw)

    monkeypatch.setattr(hwc.HomeWizardV2Client, "__init__", init)
    cfg = DeviceConfig(id="p1", name="P1", category="smart_meter", driver="homewizard.p1",
                       connection={"host": "127.0.0.1", "api": "v2", "serial": "5c2fafaabbcc",
                                   "token_ref": "homewizard:5c2fafaabbcc"})
    store = MemorySecretStore({"homewizard:5c2fafaabbcc": token} if token else {})
    return HomeWizardP1Driver(cfg, DriverContext(SystemClock(), secrets=store))


async def test_driver_streams_via_websocket_and_falls_back_to_polling(fake, monkeypatch):
    drv = _driver(fake, monkeypatch)
    await drv.connect()
    await asyncio.sleep(0.6)
    values = await drv.read()
    assert values[Metric.GRID_POWER_W] == -543 and drv.diagnostics()["transport"] == "websocket (v2)"
    assert drv.diagnostics()["firmware_version"] == "6.00"
    fake.measurement = {**MEASUREMENT, "power_w": 840}
    await asyncio.sleep(0.5)
    assert (await drv.read())[Metric.GRID_POWER_W] == 840
    drv._ws_at = 0  # websocket silent -> REST polling
    drv._ws_task.cancel()
    assert (await drv.read())[Metric.GRID_POWER_W] == 840
    assert "polling" in drv.diagnostics()["transport"]
    await drv.disconnect()


async def test_driver_without_token_explains_pairing(fake, monkeypatch):
    drv = _driver(fake, monkeypatch, token=None)
    with pytest.raises(DeviceUnavailableError, match="gekoppeld"):
        await drv.connect()


async def test_driver_self_test_report(fake, monkeypatch):
    drv = _driver(fake, monkeypatch)
    report = await drv.self_test()
    text = report.render()
    assert "✓ apparaat bereikbaar" in text and "lokale API v2" in text and "3 fase(n) gemeten" in text
    await drv.disconnect()


def test_homewizard_becomes_primary_grid_meter_automatically():
    cfg = make_config([{"id": "hw", "name": "HomeWizard P1", "category": "smart_meter", "driver": "homewizard.p1",
                        "connection": {"host": "1.2.3.4"}}])
    sel = select_primary_grid_meter(cfg, registry)
    assert sel.device_id == "hw" and sel.status == GridMeterStatus.PRIMARY_AUTO and sel.needs_confirmation
    assert "HomeWizard" in sel.reason


def test_context_accepts_extensionless_v1_ca_like_the_real_one():
    """The real HomeWizard CA is X.509 v1 (no extensions); strict mode must be off."""
    import ssl as _ssl

    from ems.integrations.homewizard.protocol import ssl_context
    ctx = ssl_context()
    assert not ctx.verify_flags & _ssl.VERIFY_X509_STRICT
    assert ctx.verify_mode == _ssl.CERT_REQUIRED and ctx.check_hostname
