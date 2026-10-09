"""Market data (prices) 0.5: provider-dependent settings, EnergyZero official endpoint, reserve source,
SSRF protection, statuses OFFICIAL_DAY_AHEAD / FORECAST / ESTIMATED / STALE / MISSING, publication delay,
DST days, contract markups, negative export value, offline source and the cache after a restart."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from conftest import make_config
from ems.core.config import ForecastConfig, PriceConfig, TariffConfig
from ems.database import Database
from ems.optimizer.service import OptimizerService
from ems.prices.providers import EnergyZeroProvider, PricePoint, PriceProvider, get_with_retry
from ems.prices.service import PriceService, PriceStatus, local_day_slots
from ems.prices.settings import EndpointError, build_provider, validate_endpoint, visible_fields
from ems.tariffs.engine import TariffEngine

TZ = ZoneInfo("Europe/Amsterdam")


def day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(UTC)


def quarters(d: date, price=lambda i: 0.10 + i / 1000, skip=()) -> list[PricePoint]:
    return [PricePoint(t, price(i), 15) for i, t in enumerate(local_day_slots(d, TZ)) if i not in skip]


def ez_payload(points: list[PricePoint]) -> dict:
    items = [{"start": p.start.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end": (p.start + timedelta(minutes=p.resolution_min)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "price": {"value": p.price_eur_kwh}} for p in points]
    return {"base": items, "base_with_vat": items, "all_in": items, "all_in_with_vat": items}


class Fixed(PriceProvider):
    def __init__(self, name, points=(), fail=None):
        self.name, self.points, self.fail, self.calls = name, list(points), fail, 0

    async def fetch(self, start, end):
        self.calls += 1
        if self.fail:
            raise self.fail
        return [p for p in self.points if start <= p.start < end]


# ---------------------------------------------------------------- settings depend on the provider
def test_energyzero_shows_no_entsoe_token():
    v = visible_fields({"provider": "energyzero", "fallback_enabled": False})
    assert "entsoe_token" not in v and "bidding_zone" not in v and "custom_url" not in v
    assert "energyzero_url" in v and "fallback_enabled" in v and "fallback_provider" not in v


def test_entsoe_shows_token_and_fallback_adds_its_fields():
    v = visible_fields({"provider": "entsoe"})
    assert "entsoe_token" in v and "energyzero_url" not in v
    v = visible_fields({"provider": "energyzero", "fallback_enabled": True, "fallback_provider": "entsoe"})
    assert "entsoe_token" in v and "energyzero_url" in v and "fallback_provider" in v
    # fallback switched off again: the token disappears
    assert "entsoe_token" not in visible_fields({"provider": "energyzero", "fallback_enabled": False,
                                                 "fallback_provider": "entsoe"})


def test_config_rules_for_fallback_and_custom_endpoint():
    with pytest.raises(ValueError, match="reserveprijsbron"):
        PriceConfig(provider="energyzero", fallback_enabled=True, fallback_provider="energyzero")
    with pytest.raises(ValueError, match="Aangepaste API-adressen"):
        PriceConfig(provider="energyzero", energyzero_url="https://example.com/v1/prices")
    PriceConfig(provider="energyzero", energyzero_url="https://example.com/v1/prices", allow_custom_endpoints=True)
    assert PriceConfig(energyzero_interval="hour").market_interval == "hour"      # 0.4 config migrates


def test_energyzero_provider_needs_no_token():
    prov = build_provider("energyzero", PriceConfig(provider="energyzero"), lambda _: None)
    assert isinstance(prov, EnergyZeroProvider) and prov.url == "https://public.api.energyzero.nl/v1/prices"
    with pytest.raises(ValueError, match="ENTSO-E"):
        build_provider("entsoe", PriceConfig(provider="entsoe"), lambda _: None)
    tok = build_provider("entsoe", PriceConfig(provider="entsoe", entsoe_token="secret:prices.entsoe_token"),
                         {"prices.entsoe_token": "t0k"}.get)
    assert tok.token == "t0k"


# ----------------------------------------------------------------------------- SSRF protection
def _resolver(ip):
    return lambda host, port, proto=0: [(2, 1, 6, "", (ip, port))]


@pytest.mark.parametrize(("url", "ip", "msg"), [
    ("https://prices.example.com/x", "93.184.216.34", None),
    ("https://prices.example.com/x", "10.0.0.5", "intern"),
    ("https://prices.example.com/x", "127.0.0.1", "intern"),
    ("https://prices.example.com/x", "169.254.169.254", "intern"),      # cloud metadata service
    ("https://prices.example.com/x", "100.64.1.1", "intern"),           # CGNAT
    ("https://prices.example.com/x", "fd00::1", "intern"),              # IPv6 ULA
    ("https://192.168.1.10/x", None, "intern"),
    ("http://prices.example.com/x", None, "https"),
    ("https://user:pw@prices.example.com/x", None, "wachtwoord"),
    ("https://router.local/x", None, "interne netwerknamen"),
    ("https://localhost/x", None, "interne netwerknamen"),
])
def test_custom_endpoint_validation(url, ip, msg):
    res = _resolver(ip or "93.184.216.34")
    if msg is None:
        assert validate_endpoint(url, allow_custom=True, resolver=res) == url
    else:
        with pytest.raises(EndpointError, match=msg):
            validate_endpoint(url, allow_custom=True, resolver=res)


def test_custom_endpoint_requires_deliberate_activation_official_always_ok():
    with pytest.raises(EndpointError, match="uitgeschakeld"):
        validate_endpoint("https://prices.example.com/x", allow_custom=False, resolver=_resolver("93.184.216.34"))
    assert validate_endpoint("https://public.api.energyzero.nl/v1/prices/", allow_custom=False).endswith("/v1/prices")


async def test_no_redirects_followed_and_auth_header_stays_on_url():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((str(req.url), req.headers.get("authorization")))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError, match="doorverwijzing"):
        await get_with_retry(client, "https://prices.example.com/x", headers={"Authorization": "Bearer s"})
    assert len(seen) == 1 and "169.254" not in seen[0][0]
    await client.aclose()


# --------------------------------------------------------------------------- EnergyZero official API
async def test_energyzero_official_parameters_quarters_retry_and_unpublished_day():
    d = date(2026, 10, 8)
    calls, sleeps = [], []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url)
        q = req.url.params
        assert str(req.url).startswith("https://public.api.energyzero.nl/v1/prices?")
        assert q["energy_type"] == "ENERGY_TYPE_ELECTRICITY" and q["interval"] == "INTERVAL_QUARTER"
        if q["date"] == "08-10-2026":
            if sum(1 for u in calls if u.params["date"] == "08-10-2026") == 1:
                return httpx.Response(503)                      # transient: retried
            return httpx.Response(200, json=ez_payload(quarters(d)))
        return httpx.Response(404, json={"error": "no data"})   # tomorrow not published yet

    async def no_sleep(s):
        sleeps.append(s)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    prov = EnergyZeroProvider("quarter", client=client, sleep=no_sleep)
    pts = await prov.fetch(day_start(d), day_start(d) + timedelta(days=2))
    assert len(pts) == 96 and all(p.resolution_min == 15 for p in pts) and sleeps == [1.0]
    assert pts[0].start == day_start(d) and prov.last_meta["endpoint"].endswith("/v1/prices")
    await client.aclose()


async def test_energyzero_dst_days_92_and_100_quarters():
    for d, n in ((date(2026, 3, 29), 92), (date(2026, 10, 25), 100)):
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req, d=d: httpx.Response(200, json=ez_payload(quarters(d))) if req.url.params["date"] == d.strftime("%d-%m-%Y")
            else httpx.Response(404)))
        svc = PriceService(None, "NL", EnergyZeroProvider("quarter", client=client), "Europe/Amsterdam")
        await svc.refresh(day_start(d) + timedelta(hours=8))
        cov = svc.coverage(d)
        assert cov["intervals"] == n and cov["complete"] and cov["hours"] == n / 4
        await client.aclose()


async def test_api_offline_keeps_cache_and_reports_error():
    def handler(req):
        raise httpx.ConnectError("offline")

    async def no_sleep(_):
        return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = PriceService(None, "NL", EnergyZeroProvider("quarter", client=client, sleep=no_sleep))
    d = date(2026, 6, 1)
    svc.add_points(quarters(d), "energyzero")
    assert await svc.refresh(day_start(d) + timedelta(hours=10)) == 0
    assert "geen verbinding" in svc.last_error and svc.spot(day_start(d)) is not None
    await client.aclose()


# ------------------------------------------------------------------------------- reserve source
async def test_fallback_used_only_when_needed_and_fills_only_gaps():
    d = date(2026, 6, 1)
    now = day_start(d) + timedelta(hours=10)
    primary = Fixed("energyzero", quarters(d) + quarters(d + timedelta(days=1)))
    reserve = Fixed("entsoe", quarters(d, price=lambda i: 9.0))
    svc = PriceService(None, "NL", primary, fallback=reserve)
    await svc.refresh(now)
    assert reserve.calls == 0                                  # primary complete: reserve not asked

    primary2 = Fixed("energyzero", quarters(d, skip=(40, 41)))
    reserve2 = Fixed("entsoe", quarters(d, price=lambda i: 0.5))
    svc2 = PriceService(None, "NL", primary2, fallback=reserve2)
    await svc2.refresh(now)
    assert reserve2.calls == 1
    assert svc2.spot(local_day_slots(d, TZ)[40]) == 0.5        # gap filled by the reserve source
    assert svc2.spot(local_day_slots(d, TZ)[39]) == pytest.approx(0.139)   # primary values kept
    assert svc2.point(local_day_slots(d, TZ)[40]).source == "entsoe"

    svc3 = PriceService(None, "NL", Fixed("energyzero", fail=ConnectionError("down")), fallback=Fixed("entsoe", quarters(d)))
    assert await svc3.refresh(now) == 96 and "down" in svc3.last_error
    assert svc3.coverage(d)["complete"]

    off = PriceService(None, "NL", Fixed("energyzero", fail=ConnectionError("down")), fallback=None)
    assert await off.refresh(now) == 0 and not off.coverage(d)["complete"]


# ------------------------------------------------------------------------------- statuses
def test_missing_quarter_is_estimated_large_gap_is_missing():
    d = date(2026, 6, 1)
    svc = PriceService(None, "NL", None)
    svc.add_points(quarters(d, skip=(10,) + tuple(range(40, 60))), "energyzero")
    slots = local_day_slots(d, TZ)
    p = svc.point(slots[10])
    assert p.status == PriceStatus.ESTIMATED and p.spot == pytest.approx((0.109 + 0.111) / 2) and p.confidence < 1
    assert svc.point(slots[50]).status == PriceStatus.MISSING          # 5 h gap: not invented
    cov = svc.coverage(d)
    assert cov["missing_count"] == 21 and not cov["complete"]


def test_forecast_never_official_has_band_and_respects_horizon():
    d = date(2026, 6, 10)
    svc = PriceService(None, "NL", None, forecast=ForecastConfig(price_forecast_horizon_hours=24))
    for k in range(8, 0, -1):
        svc.add_points(quarters(d - timedelta(days=k), price=lambda i, k=k: 0.10 + 0.01 * (k % 3)), "energyzero")
    end = svc.last_known()
    f = svc.point(end + timedelta(hours=3))
    assert f.status == PriceStatus.FORECAST and not f.official and f.estimated
    assert f.low < f.spot < f.high and 0 < f.confidence < 1
    assert svc.point(end + timedelta(hours=30)).status == PriceStatus.MISSING
    later = svc.point(end + timedelta(hours=20))
    assert later.confidence < f.confidence                            # less certain further ahead
    svc.fcfg = ForecastConfig(price_forecast_enabled=False)
    assert svc.point(end + timedelta(hours=3)).status == PriceStatus.MISSING
    # The published-only series never contains a forecast.
    assert all(p.status == PriceStatus.OFFICIAL_DAY_AHEAD
               for p in svc.series(end - timedelta(hours=2), end + timedelta(hours=4), estimate=False))


def test_expired_and_stale_prices():
    d = date(2026, 6, 1)
    now = day_start(d) + timedelta(hours=10)
    svc = PriceService(None, "NL", Fixed("energyzero"), now_fn=lambda: now)
    svc.add_points(quarters(d), "energyzero")
    assert svc.current(day_start(d) + timedelta(days=1, hours=1)) is None    # expired: no "now" price
    svc.last_error, svc.last_fetch = "offline", now - timedelta(hours=30)
    assert svc.stale(now)
    assert svc.point(now + timedelta(hours=2)).status == PriceStatus.STALE
    assert svc.point(now - timedelta(hours=2)).status == PriceStatus.OFFICIAL_DAY_AHEAD   # the past stays as it was
    svc.last_fetch = now - timedelta(hours=2)
    assert not svc.stale(now)


def test_publication_states_and_retry_schedule():
    d = date(2026, 6, 1)
    svc = PriceService(None, "NL", Fixed("energyzero"), config=PriceConfig(provider="energyzero"))
    svc.add_points(quarters(d), "energyzero")
    at = lambda hh, mm=0: datetime(2026, 6, 1, hh, mm, tzinfo=TZ).astimezone(UTC)   # noqa: E731
    assert svc.publication(at(10))["state"] == "not_yet_expected"
    assert 60 <= svc.next_refresh_s(at(12, 50)) <= 11 * 60            # check right after the auction
    assert svc.publication(at(13, 30))["state"] == "waiting" and svc.next_refresh_s(at(13, 30)) == 15 * 60
    pub = svc.publication(at(16))
    assert pub["state"] == "delayed" and pub["known"] == 0 and pub["intervals"] == 96
    svc.add_points(quarters(d + timedelta(days=1), skip=(95,)), "energyzero")
    assert svc.publication(at(16))["state"] == "delayed"               # one quarter missing: not "available"
    svc.add_points(quarters(d + timedelta(days=1)), "energyzero")
    assert svc.publication(at(16))["state"] == "available" and svc.next_refresh_s(at(16)) == 60 * 60


async def test_cache_and_sync_state_survive_restart(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/p.db")
    db.migrate()
    d = date(2026, 6, 1)
    now = day_start(d) + timedelta(hours=10)
    svc = PriceService(db, "NL", Fixed("energyzero", quarters(d)))
    assert await svc.refresh(now) == 96
    svc2 = PriceService(db, "NL", Fixed("energyzero", fail=ConnectionError("down")))
    assert svc2.last_fetch == now and svc2.sync["energyzero"]["slots"] == 96     # persisted sync info
    await svc2.refresh(now + timedelta(hours=1))
    assert svc2.coverage(d)["complete"] and svc2.point(day_start(d)).source == "energyzero"
    assert svc2.status(now)["last_success"] == now.isoformat()


# ------------------------------------------------------------------------- contract / optimizer
def test_supplier_markups_and_negative_export_value():
    d = date(2026, 6, 1)
    svc = PriceService(None, "NL", None)
    svc.add_points(quarters(d, price=lambda i: -0.05), "energyzero")
    tariff = TariffEngine(TariffConfig(contract_type="dynamic", import_markup_eur_kwh=0.02, energy_tax_mode="manual",
                                       energy_tax_eur_kwh=0.10, vat_pct=21, export_markup_eur_kwh=-0.02,
                                       export_fee_eur_kwh=0.01, netting=False), svc.spot)
    b = tariff.breakdown(day_start(d) + timedelta(hours=12))
    assert b.import_price == pytest.approx((-0.05 + 0.02 + 0.10) * 1.21, abs=1e-4)
    assert b.export_price == pytest.approx(-0.05 - 0.02 - 0.01, abs=1e-4)   # feeding in costs money


def _optimizer(prices, **forecast):
    cfg = make_config([], tariff={"import_markup_eur_kwh": 0, "energy_tax_eur_kwh": 0, "vat_pct": 0},
                      forecast=forecast)
    tariff = TariffEngine(cfg.tariff, prices.spot, cfg.site.timezone)
    return OptimizerService(cfg, prices, tariff, None)


def test_optimizer_uses_only_allowed_price_statuses():
    d = date(2026, 6, 10)
    svc = PriceService(None, "NL", None)
    for k in range(7, 0, -1):
        svc.add_points(quarters(d - timedelta(days=k)), "energyzero")
    svc.add_points(quarters(d, skip=(20,)), "energyzero")
    slots = local_day_slots(d, TZ)
    opt = _optimizer(svc)
    assert opt.price_usable(svc.point(slots[5])) == (True, "")
    ok, why = opt.price_usable(svc.point(slots[20]))                 # ESTIMATED gap, default stop_plan
    assert not ok and "ontbrekend" in why
    assert _optimizer(svc, missing_price_fallback="use_forecast").price_usable(svc.point(slots[20]))[0]
    fc = svc.point(svc.last_known() + timedelta(hours=1))
    assert fc.status == PriceStatus.FORECAST
    assert not _optimizer(svc, optimizer_uses_price_forecast=False).price_usable(fc)[0]
    assert not _optimizer(svc, price_forecast_confidence_threshold=0.99).price_usable(fc)[0]
    assert opt.price_usable(svc.point(svc.last_known() + timedelta(hours=40)))[1] == "geen prijs bekend"


def test_no_grid_charging_on_forecast_prices():
    from ems.optimizer.model import BatteryModel, OptimizerInput, solve
    t0 = datetime(2026, 6, 1, tzinfo=UTC)
    slots = [t0 + timedelta(minutes=15 * i) for i in range(8)]
    imp = [0.05] * 4 + [0.60] * 4                          # cheap now, expensive later: grid charging pays
    bat = BatteryModel(10, 2, 1, 9, 4000, 4000, 0.95, 0.95, 0.0, 0.0, 5, True, False)
    base = dict(slots=slots, dt_h=0.25, import_price=imp, export_price=[0.0] * 8, pv_w=[0.0] * 8, load_w=[1000.0] * 8,
                max_import_w=10000, max_export_w=10000, battery=bat)
    free = solve(OptimizerInput(**base))
    assert sum(r["battery_grid_charge_w"] for r in free.slots[:4]) > 1000
    blocked = solve(OptimizerInput(**base, grid_charge_allowed=[False] * 8, price_estimated=[True] * 8))
    assert all(r["battery_grid_charge_w"] == 0 for r in blocked.slots)
