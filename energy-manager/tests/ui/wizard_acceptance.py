"""Acceptance of the device wizard — in a browser, or in the *installed* Windows app (not part of pytest).

    # browser (Chromium) against a local server
    ems serve --lab --data-dir <tmp> --port 8796 &
    python tests/ui/wizard_acceptance.py http://127.0.0.1:8796 <screenshot-dir>

    # the installed Windows app: its WebView2 started with --remote-debugging-port=9222
    python tests/ui/wizard_acceptance.py http://127.0.0.1:8080 <screenshot-dir> --cdp http://127.0.0.1:9222 [--demo]

Checks what a user sees: frontend and server are the same build (no old cached JavaScript), the new wizard
(Soort → Methode → Gegevens → Testen), the four methods for **every** category, real brands/models with functions
and honest alternatives. In the Testomgeving (default) also: brand/model with a Modbus profile, automatic search,
SunSpec, P1, Home Assistant via entity lists (no JSON), edit form during live updates, faults, mobile width.
``--demo``: only the checks that need no virtual devices (used right after an upgrade of an existing install).
"""

import os
import sys

from playwright.sync_api import sync_playwright

for _stream in (sys.stdout, sys.stderr):      # Windows console code page cannot print "→"
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE, OUT = sys.argv[1].rstrip("/"), sys.argv[2]
CDP = sys.argv[sys.argv.index("--cdp") + 1] if "--cdp" in sys.argv else None
DEMO = "--demo" in sys.argv
PRIME_OLD = "--prime-old" in sys.argv      # open the OLD (pre-0.6.1) wizard once, so its files land in the app cache
os.makedirs(OUT, exist_ok=True)
results, errors = [], []
CATEGORIES = ["smart_meter", "energy_meter", "pv_inverter", "hybrid_inverter", "battery", "heat_pump", "heat_pump_boiler",
              "boiler", "ev_charger", "ev", "airco", "ventilation", "smart_plug", "smart_relay", "flexible_load"]
METHODS = ["search", "brand", "gateway", "manual"]


def ok(msg):
    results.append(msg)
    print("PASS ", msg, flush=True)


def shot(pg, name):
    pg.screenshot(path=f"{OUT}/{name}.png", full_page=True)


def open_wizard(pg):
    pg.goto(BASE + "/#/devices")        # a hash change is needed for the router to render the wizard again
    pg.wait_for_selector("h1:has-text('Apparaten')", timeout=30000)
    pg.goto(BASE + "/#/devices/add")
    pg.wait_for_selector("[data-category]", timeout=30000)


def choose(pg, category, method):
    open_wizard(pg)
    pg.click(f"[data-category={category}]")
    pg.wait_for_selector("[data-method]")
    pg.click(f"[data-method={method}]")


def add_via_brand(pg, category, selector):
    choose(pg, category, "brand")
    pg.wait_for_selector(selector, timeout=20000)
    pg.click(selector)
    pg.wait_for_selector("[data-action=test]")


def test_and_save(pg, name):
    pg.click("[data-action=test]")
    pg.wait_for_selector("[data-action=save]", timeout=45000)
    assert pg.is_visible("text=Wat kan het EMS met dit apparaat?")
    shot(pg, name)
    pg.click("[data-action=save]")
    pg.wait_for_selector("text=Functies van dit apparaat", timeout=45000)


def login(pg):
    pg.goto(BASE + "/")
    pg.wait_for_timeout(3000)
    if pg.locator("input[autocomplete=username]").count():
        pg.click("button[type=submit]")
    pg.wait_for_selector(".flow, #version-line", timeout=30000)


def prime_old(pg):
    login(pg)
    pg.goto(BASE + "/#/devices")
    pg.wait_for_timeout(2500)
    pg.goto(BASE + "/#/devices/add")
    pg.wait_for_selector(".steps", timeout=30000)
    pg.wait_for_timeout(2000)
    steps = pg.inner_text(".steps").replace("\n", " ")
    shot(pg, "old-version-wizard")
    assert "Merk en model" in steps, steps
    ok(f"old version opened in the app (its wizard: {steps}); its web files are now in the app's cache")


def run(pg, browser):
    if PRIME_OLD:
        return prime_old(pg)
    login(pg)
    # --- build identity: the page and the server are the same version ------------------------------------------
    page_asset = pg.locator("meta[name=ems-asset]").get_attribute("content")
    page_version = pg.locator("meta[name=ems-version]").get_attribute("content")
    server = pg.evaluate("fetch('/healthz', {cache: 'no-store'}).then(r => r.json())")
    assert page_asset and page_asset == server["asset_hash"], (page_asset, server)
    assert page_version == server["version"], (page_version, server)
    line = pg.inner_text("#version-line")
    assert server["version"] in line and "≠" not in line, line
    assert pg.locator("[data-version-mismatch]").count() == 0
    devices_js = pg.evaluate("fetch(document.querySelector('script[type=module]').src.replace('app.js', 'views/devices.js'))"
                             ".then(r => r.text())")
    assert "addwizard.js" in devices_js
    ok(f"frontend = server: version {server['version']}, build {server['build']}, assets {page_asset} "
       "(versioned URL, not from an old cache)")
    if not DEMO:
        assert pg.is_visible(".mode-tag:has-text('Testomgeving')")
        ok("Testomgeving is clearly marked in the top bar")

    # --- the new wizard, four methods for every category ----------------------------------------------------------
    open_wizard(pg)
    steps = pg.inner_text(".steps").replace("\n", " ")
    assert "Methode" in steps and "Merk en model" not in steps, steps
    assert pg.locator("[data-category]").count() == 15
    shot(pg, "wizard-1-categories")
    for cat in CATEGORIES:
        pg.click(f"[data-category={cat}]")
        pg.wait_for_selector("[data-method]")
        visible = [m for m in METHODS if pg.is_visible(f"[data-method={m}]")]
        assert visible == METHODS, (cat, visible)
        if cat == "pv_inverter":
            shot(pg, "wizard-2-methods-pv")
        pg.click("text=← Terug")
        pg.wait_for_selector("[data-category]")
    ok("wizard: Soort → Methode → Gegevens → Testen; all 4 methods (automatisch zoeken, merk en model, gateway, "
       "handmatig) visible for all 15 categories")

    # --- brand/model lists: real brands, functions, alternatives -------------------------------------------------------
    choose(pg, "pv_inverter", "brand")
    pg.wait_for_selector("[data-brand=Fronius]")
    brands = pg.locator("[data-brand]").evaluate_all("els => els.map(e => e.dataset.brand)")
    assert {"Fronius", "SMA", "SolarEdge", "Kostal", "Growatt", "Enphase"} <= set(brands), brands
    assert not {"Home Assistant", "SunSpec-compatibel (overige merken)", "Apparatenbibliotheek"} & set(brands), brands
    fronius = pg.inner_text("[data-brand=Fronius]")
    assert "PV-vermogen" in fronius and "model 123" in fronius and "Verbinding:" in fronius, fronius
    assert pg.locator("[data-brand=Growatt] [data-route=sunspec]").count() == 1
    assert pg.locator(".not-listed [data-route=homeassistant]").count() == 1
    shot(pg, "wizard-3-brands-pv")
    for cat, must in (("heat_pump", "Vaillant"), ("ev_charger", "Alfen"), ("battery", "Tesla"), ("boiler", "Itho Daalderop")):
        choose(pg, cat, "brand")
        pg.wait_for_selector(f"[data-brand='{must}']")
        assert pg.locator("[data-brand='Home Assistant']").count() == 0
        assert pg.locator(f"[data-brand='{must}'] [data-route]").count() >= 1   # honest: not supported + alternative
    ok("brand/model lists show real brands with functions; unsupported models say so and offer an alternative route; "
       "Home Assistant/SunSpec are routes, not brands")

    # gateway step = routes; technical fields only where relevant
    choose(pg, "pv_inverter", "gateway")
    pg.wait_for_selector("[data-route-card=sunspec]")
    pg.click("[data-route-card=sunspec] button")
    pg.wait_for_selector("[data-action=test]")
    assert pg.inner_text("[data-picked=connection]").startswith("Apparaat met SunSpec")
    assert pg.is_visible("select[name=transport]") and not pg.is_visible("text=Seriële poort")
    assert not pg.is_visible("text=Baudrate") and not pg.is_visible("text=Teken netvermogen omdraaien")
    pg.select_option("select[name=transport]", "rtu")
    assert pg.is_visible("text=Seriële poort") and not pg.is_visible("text=IP-adres / hostnaam")
    shot(pg, "wizard-4-sunspec-rtu")
    ok("SunSpec: device and connection shown separately; Modbus TCP shows no RS485 fields, RS485 no IP fields; "
       "meter/expert settings hidden")
    if DEMO:
        return

    # --- Testomgeving: real connections --------------------------------------------------------------------------------
    add_via_brand(pg, "heat_pump", ".model[data-profile='energymanager.lab.heat_pump'] button")
    assert not pg.is_visible("text=Seriële poort")
    test_and_save(pg, "wizard-5-test-heatpump")
    ok("heat pump via brand/model (Modbus TCP profile): test, identity, capability report, saved")

    choose(pg, "battery", "search")
    pg.wait_for_selector(".result[data-driver='library.modbus'] button", timeout=45000)
    shot(pg, "wizard-6-search")
    pg.click(".result[data-driver='library.modbus'] button")
    pg.wait_for_selector("[data-action=test]")
    test_and_save(pg, "wizard-7-test-battery")
    ok("battery via automatic search (exact model recognised) and saved")

    pg.click("text=Bewerken")
    pg.wait_for_selector(".edit-slot input[aria-label=naam]")
    pg.fill(".edit-slot input[aria-label=naam]", "Batterij getypt")
    pg.wait_for_timeout(7000)
    assert pg.input_value(".edit-slot input[aria-label=naam]") == "Batterij getypt"
    pg.click(".edit-slot >> text=Opslaan")
    pg.wait_for_selector("h1:has-text('Batterij getypt')", timeout=15000)
    ok("edit form: typed text survives 7 s of live updates and is saved")

    add_via_brand(pg, "pv_inverter", ".model[data-profile='energymanager.lab.sunspec_inverter'] button")
    test_and_save(pg, "wizard-8-test-pv")
    add_via_brand(pg, "smart_meter", ".model:has-text('Virtuele P1-meter') button")
    test_and_save(pg, "wizard-9-test-p1")
    ok("SunSpec inverter and P1 meter added through the wizard")

    # Home Assistant: entity lists, no JSON
    choose(pg, "boiler", "gateway")
    pg.wait_for_selector("[data-route-card=homeassistant]")
    pg.click("[data-route-card=homeassistant] button")
    pg.wait_for_selector("[data-action=ha-connect]")
    assert pg.locator("input[type=password]").count() >= 1
    pg.click("[data-action=ha-connect]")
    pg.wait_for_selector("[data-ha-metric=load_power_w] select", timeout=20000)
    pg.select_option("[data-ha-metric=load_power_w] select", "sensor.testwoning_boiler_vermogen")
    pg.select_option("[data-ha-action=switch] select", "switch.testwoning_boiler")
    assert not pg.is_visible("textarea")                                  # JSON stays hidden for beginners
    shot(pg, "wizard-10-homeassistant")
    test_and_save(pg, "wizard-11-test-homeassistant")
    ok("Home Assistant: connect, choose entities from lists (no JSON), test and save")

    pg.goto(BASE + "/#/lab")
    pg.wait_for_selector("[data-lab=battery]")
    pg.check("[data-lab=battery] input[type=checkbox]")
    pg.click("[data-lab=battery] >> text=Toepassen")
    pg.wait_for_timeout(1500)
    pg.goto(BASE + "/#/devices")
    pg.wait_for_timeout(9000)
    pg.reload()
    pg.wait_for_selector("table")
    txt = pg.inner_text("tbody")
    assert "offline" in txt or "verouderd" in txt or "Niet verbonden" in txt, txt
    pg.goto(BASE + "/#/lab")
    pg.wait_for_selector("[data-lab=battery]")
    pg.uncheck("[data-lab=battery] input[type=checkbox]")
    pg.click("[data-lab=battery] >> text=Toepassen")
    ok("Testomgeving: fault 'offline' makes the battery go offline in the EMS; fault removed")

    if browser is not None:                                               # mobile width (browser run only)
        m = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page()
        login(m)
        m.goto(BASE + "/#/devices/add")
        m.wait_for_selector("[data-category]")
        assert m.evaluate("document.documentElement.scrollWidth") <= 392
        shot(m, "wizard-12-mobile")
        ok("wizard at 390 px: no horizontal scroll")


with sync_playwright() as p:
    if CDP:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        pg = next((x for x in ctx.pages if not x.url.startswith("devtools")), None) or ctx.new_page()
        own = None
    else:
        exe = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
        own = p.chromium.launch(executable_path=exe)
        pg = own.new_page(viewport={"width": 1366, "height": 900})
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: m.type == "error" and "favicon" not in m.text and errors.append(f"console: {m.text}"))
    try:
        run(pg, own)
    except Exception as exc:
        try:
            shot(pg, "failure")
        except Exception:
            pass
        errors.append(f"FAILED: {type(exc).__name__}: {exc}")
    if own:
        own.close()

print("\n".join(errors) if errors else "no console/page errors")
print(f"WIZARD ACCEPTANCE ({'installed app' if CDP else 'browser'}, {'demo' if DEMO else 'testomgeving'}): "
      f"{'FAIL' if errors else 'PASS'} ({len(results)} checks)")
sys.exit(1 if errors else 0)
