"""SafetyValidator, control lease (fencing), pairing codes and node identity."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from ems.control.safety import SafetyRejected, SafetyValidator
from ems.core.config import DeviceConfig
from ems.core.models import Capability, Command, CommandAction, Metric
from ems.devices.manager import ManagedDevice
from ems.nodes.identity import detect_platform, load_identity
from ems.nodes.lease import LeaseManager
from ems.nodes.pairing import PairingManager, hash_token

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


class FakeDriver:
    def __init__(self, caps):
        self.caps = caps

    def supports(self, cmd):
        from ems.core.models import ACTION_CAPABILITY
        return ACTION_CAPABILITY[cmd.action] in self.caps


def device(soc=50.0, age_s=1.0, connected=True, caps=None, params=None):
    cfg = DeviceConfig(id="bat", name="Batterij", category="battery", driver="x",
                       params=params or {"max_charge_w": 5000, "max_discharge_w": 4000})
    caps = caps or {Capability.CONTROL_BATTERY_MODE, Capability.CONTROL_BATTERY_POWER,
                    Capability.CONTROL_EV_CURRENT, Capability.CONTROL_PV_LIMIT}
    md = ManagedDevice(cfg, FakeDriver(caps), connected=connected)
    md.last_ok = NOW - timedelta(seconds=age_s)
    md.last_values = {Metric.BATTERY_SOC_PCT: soc} if soc is not None else {}
    return md


def test_safety_limits_and_clamping():
    v = SafetyValidator(min_soc=10, reserve_soc=20, max_soc=95)
    r = v.validate(Command("bat", CommandAction.BATTERY_CHARGE, 9000), device(), NOW)
    assert r.command.value == 5000 and r.adjusted
    with pytest.raises(SafetyRejected, match="vol"):
        v.validate(Command("bat", CommandAction.BATTERY_CHARGE, 1000), device(soc=96), NOW)
    with pytest.raises(SafetyRejected, match="reserve"):
        v.validate(Command("bat", CommandAction.BATTERY_DISCHARGE, 1000), device(soc=19), NOW)
    with pytest.raises(SafetyRejected, match="SOC"):
        v.validate(Command("bat", CommandAction.BATTERY_CHARGE, 1000), device(soc=None), NOW)


def test_safety_stale_offline_capability_and_release():
    v = SafetyValidator(stale_after_s=30)
    with pytest.raises(SafetyRejected, match="verouderd"):
        v.validate(Command("bat", CommandAction.BATTERY_CHARGE, 1000), device(age_s=60), NOW)
    with pytest.raises(SafetyRejected, match="niet verbonden"):
        v.validate(Command("bat", CommandAction.BATTERY_CHARGE, 1000), device(connected=False), NOW)
    with pytest.raises(SafetyRejected, match="ondersteunt"):
        v.validate(Command("bat", CommandAction.HP_MODE, "boost"), device(), NOW)
    # Handing control back is always allowed on a connected device, even with old data.
    assert v.validate(Command("bat", CommandAction.BATTERY_AUTO), device(age_s=600), NOW).command.action == \
        CommandAction.BATTERY_AUTO


def test_safety_rate_limit_and_ev_pv():
    v = SafetyValidator(min_interval_s=5)
    v.validate(Command("bat", CommandAction.BATTERY_CHARGE, 1000), device(), NOW)
    with pytest.raises(SafetyRejected, match="te snel"):
        v.validate(Command("bat", CommandAction.BATTERY_DISCHARGE, 1000), device(), NOW + timedelta(seconds=2))
    assert v.validate(Command("bat", CommandAction.BATTERY_DISCHARGE, 1000), device(), NOW + timedelta(seconds=6))
    ev = device(params={"min_current_a": 6, "max_current_a": 16})
    assert v.validate(Command("bat", CommandAction.EV_CURRENT, 32), ev, NOW).command.value == 16
    with pytest.raises(SafetyRejected, match="minimum"):
        v.validate(Command("bat", CommandAction.EV_CURRENT, 3), ev, NOW + timedelta(seconds=10))
    pv = device(params={"peak_power_kw": 5})
    with pytest.raises(SafetyRejected):
        v.validate(Command("bat", CommandAction.PV_LIMIT, -1), pv, NOW)


def test_lease_fencing():
    t = [1000.0]
    saved = []
    lease = LeaseManager(30, load_epoch=lambda: 7, save_epoch=saved.append, clock=lambda: t[0])
    ok, st = lease.acquire("A")
    assert ok and st.epoch == 8 and saved == [8]
    assert lease.acquire("B")[0] is False                 # no take-over while A's lease is valid
    assert lease.check("A", 8) is None and lease.check("B", 8) and lease.check("A", 7)
    t[0] += 10
    assert lease.acquire("A")[1].epoch == 8              # renewal keeps the epoch
    t[0] += 31
    assert lease.check("A", 8) == "regelrecht verlopen"
    assert lease.expired_holder() == "A" and lease.expired_holder() is None
    ok, st = lease.acquire("B")
    assert ok and st.epoch == 9 and lease.check("A", 8) is not None


def test_pairing_code_single_use_and_lockout():
    t = [0.0]
    p = PairingManager(clock=lambda: t[0])
    code, ttl = p.new_code()
    assert len(code) == 6 and code.isdigit() and ttl == 600
    assert p.verify(code) and not p.verify(code)          # single use
    code, _ = p.new_code()
    for _ in range(5):
        p.verify("000000" if code != "000000" else "111111")
    assert not p.verify(code)                             # locked after 5 wrong attempts
    code, _ = p.new_code()
    t[0] += 601
    assert not p.verify(code)                             # expired
    token, h = PairingManager.new_token()
    assert token.startswith("emsn_") and hash_token(token) == h and token not in h


def test_identity_is_stable(tmp_path):
    a = load_identity(tmp_path, "", "gateway")
    b = load_identity(tmp_path, "Meterkast", "all_in_one")
    assert a.node_id == b.node_id and json.loads((tmp_path / "node.json").read_text())["node_id"] == a.node_id
    assert a.roles == ["DEVICE_GATEWAY", "USER_INTERFACE"] and not a.is_controller
    assert b.is_controller and b.name == "Meterkast" and detect_platform() in ("LINUX", "RASPBERRY_PI", "WINDOWS", "OTHER")


def test_powercfg_parsing_is_locale_independent():
    from ems.nodes.power import parse_powercfg
    nl = """Power-schema-GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Gebalanceerd)
  Subgroep-GUID: 238c9fa8-0aad-41ed-83f4-97be242c8f20  (Slaapstand)
    Energie-instelling-GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Slaapstand na)
      Minimaal mogelijke instelling: 0x00000000
      Maximaal mogelijke instelling: 0xffffffff
      Toename mogelijke instellingen: 0x00000001
      Eenheden mogelijke instellingen: Seconden
    Index van huidige wisselstroominstelling: 0x00000708
    Index van huidige gelijkstroominstelling: 0x00000384"""
    assert parse_powercfg(nl) == {"ac_sleep_after_s": 1800, "dc_sleep_after_s": 900}
    assert parse_powercfg("nothing") is None
