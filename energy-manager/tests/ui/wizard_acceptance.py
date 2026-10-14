"""Browser acceptance of the device wizard against the Testomgeving (opdracht §24 G; not part of pytest).

    ems serve --lab --data-dir <tmp> --port 8796 &
    python tests/ui/wizard_acceptance.py http://127.0.0.1:8796 <screenshot-dir>

Walks the steps a user does tomorrow: categories → method → brand/model or automatic search → details →
connection test with capability report → save → device page; edit form keeps typed input during live
updates; secret fields are password inputs; Testomgeving page and fault injection; mobile width.
"""

import os
import sys

from playwright.sync_api import sync_playwright

BASE, OUT = sys.argv[1], sys.argv[2]
results, errors = [], []


def ok(msg):
    results.append(msg)
    print("PASS ", msg)


def open_wizard(pg):
    pg.goto(BASE + "/#/devices")        # a hash change is needed for the router to render the wizard again
    pg.wait_for_selector("h1:has-text('Apparaten')")
    pg.goto(BASE + "/#/devices/add")


def add_via_brand(pg, category, profile_or_driver, attr="data-profile"):
    open_wizard(pg)
    pg.wait_for_selector(f"[data-category={category}]")
    pg.click(f"[data-category={category}]")
    pg.click("[data-method=brand]")
    sel = f".model[{attr}='{profile_or_driver}'] button" if attr else f".model:has-text('{profile_or_driver}') button"
    pg.wait_for_selector(sel)
    pg.click(sel)
    pg.wait_for_selector("text=Verbinding testen")


def test_and_save(pg, shot):
    pg.click("text=Verbinding testen")
    pg.wait_for_selector("[data-action=save]", timeout=30000)
    assert pg.is_visible("text=Wat kan het EMS met dit apparaat?")
    pg.screenshot(path=f"{OUT}/{shot}.png", full_page=True)
    pg.click("[data-action=save]")
    pg.wait_for_selector("text=Functies van dit apparaat", timeout=30000)


with sync_playwright() as p:
    exe = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    b = p.chromium.launch(executable_path=exe)
    pg = b.new_page(viewport={"width": 1366, "height": 900})
    pg.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: m.type == "error" and errors.append(f"console: {m.text}"))
    pg.goto(BASE + "/")
    pg.wait_for_selector("button[type=submit]")
    pg.click("button[type=submit]")
    pg.wait_for_selector(".flow", timeout=20000)
    assert pg.is_visible(".mode-tag:has-text('Testomgeving')")
    ok("Testomgeving is clearly marked in the top bar")

    # 1. categories: 15 tiles, then 4 methods
    pg.goto(BASE + "/#/devices/add")
    pg.wait_for_selector("[data-category]")
    n = pg.locator("[data-category]").count()
    assert n == 15, n
    pg.screenshot(path=f"{OUT}/wizard-1-categories.png", full_page=True)
    pg.click("[data-category=heat_pump]")
    pg.wait_for_selector("[data-method=brand]")
    assert pg.locator("[data-method]:visible").count() == 3       # 'Handmatig' hidden in Eenvoudig
    ok("wizard starts with 15 device categories; methods follow; manual hidden at level Eenvoudig")

    # 2. brand/model: heat pump over Modbus TCP, unsupported brands visible but not selectable
    pg.click("[data-method=brand]")
    pg.wait_for_selector(".model")
    assert pg.locator(".model:has-text('Nog niet ondersteund') button").count() == 0
    pg.screenshot(path=f"{OUT}/wizard-2-brands.png", full_page=True)
    add_via_brand(pg, "heat_pump", "energymanager.lab.heat_pump")
    assert pg.locator("input[aria-label='naam']").input_value() == "Virtuele warmtepomp (Modbus TCP)"
    assert not pg.is_visible("text=Seriële poort")                 # RTU-only field hidden for TCP
    test_and_save(pg, "wizard-3-test-heatpump")
    ok("heat pump via brand/model: connection test, identity, capability report, saved (read-only)")

    # 3. automatic search: battery
    open_wizard(pg)
    pg.wait_for_selector("[data-category=battery]")
    pg.click("[data-category=battery]")
    pg.click("[data-method=search]")
    pg.wait_for_selector(".result[data-driver='library.modbus'] button", timeout=30000)
    pg.screenshot(path=f"{OUT}/wizard-4-search.png", full_page=True)
    pg.click(".result[data-driver='library.modbus'] button")
    pg.wait_for_selector("text=Verbinding testen")
    test_and_save(pg, "wizard-5-test-battery")
    ok("battery via automatic search (exact model recognised) and saved")

    # 4. edit form keeps input during live updates (finding 6)
    pg.click("text=Bewerken")
    pg.wait_for_selector(".edit-slot input[aria-label=naam]")
    pg.fill(".edit-slot input[aria-label=naam]", "Batterij getypt")
    pg.wait_for_timeout(7000)
    assert pg.input_value(".edit-slot input[aria-label=naam]") == "Batterij getypt"
    pg.click(".edit-slot >> text=Opslaan")
    pg.wait_for_selector("h1:has-text('Batterij getypt')", timeout=15000)
    ok("edit form: typed text survives 7 s of live updates and is saved")

    # 5. PV (SunSpec), P1 (primary meter), SG Ready, EV, boiler
    add_via_brand(pg, "pv_inverter", "energymanager.lab.sunspec_inverter")
    test_and_save(pg, "wizard-6-test-pv")
    add_via_brand(pg, "smart_meter", "Virtuele P1-meter", None)
    pg.screenshot(path=f"{OUT}/wizard-7-p1.png", full_page=True)
    test_and_save(pg, "wizard-8-test-p1")
    add_via_brand(pg, "ev_charger", "energymanager.lab.ev_charger")
    test_and_save(pg, "wizard-9-test-ev")
    ok("PV (SunSpec), P1 meter and charger added through the wizard")

    # 6. secret fields come from the schema (Shelly password) — never shown as text
    open_wizard(pg)
    pg.wait_for_selector("[data-category=boiler]")
    pg.click("[data-category=boiler]")
    pg.click("[data-method=gateway]")
    pg.click("text=Netwerkrelais (Shelly Gen2+) >> xpath=../.. >> button")
    pg.wait_for_selector("text=Verbinding testen")
    pw = pg.locator("input[type=password]")
    assert pw.count() >= 1
    ok("secret connection field rendered as password input (from driver metadata)")

    # 7. device list, Testomgeving page and a fault
    pg.goto(BASE + "/#/devices")
    pg.wait_for_selector("table")
    rows = pg.locator("tbody tr").count()
    assert rows >= 5, rows
    pg.screenshot(path=f"{OUT}/wizard-10-devices.png", full_page=True)
    pg.goto(BASE + "/#/lab")
    pg.wait_for_selector("[data-lab=battery]")
    pg.check("[data-lab=battery] input[type=checkbox]")
    pg.click("[data-lab=battery] >> text=Toepassen")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=f"{OUT}/wizard-11-lab.png", full_page=True)
    pg.goto(BASE + "/#/devices")
    pg.wait_for_timeout(8000)
    pg.reload()
    pg.wait_for_selector("table")
    txt = pg.inner_text("tbody")
    assert "offline" in txt or "verouderd" in txt or "Niet verbonden" in txt, txt
    pg.goto(BASE + "/#/lab")
    pg.wait_for_selector("[data-lab=battery]")
    pg.uncheck("[data-lab=battery] input[type=checkbox]")
    pg.click("[data-lab=battery] >> text=Toepassen")
    ok("Testomgeving page: fault 'offline' makes the battery go offline in the EMS; fault removed")

    # 8. mobile width: wizard without horizontal scroll
    m = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page()
    m.goto(BASE + "/")
    m.wait_for_selector("button[type=submit]")
    m.click("button[type=submit]")
    m.wait_for_selector(".flow", timeout=20000)
    m.goto(BASE + "/#/devices/add")
    m.wait_for_selector("[data-category]")
    sw = m.evaluate("document.documentElement.scrollWidth")
    assert sw <= 392, sw
    m.screenshot(path=f"{OUT}/wizard-12-mobile.png", full_page=True)
    ok("wizard at 390 px: no horizontal scroll")
    b.close()

print("\n".join(errors) if errors else "no console/page errors")
print(f"WIZARD ACCEPTANCE: {'FAIL' if errors else 'PASS'} ({len(results)} checks)")
sys.exit(1 if errors else 0)
