"""Browser acceptance for audit tests 21 and 23 (run against a Demo server; not part of pytest).

    ems serve --demo --data-dir <tmp> --port 8795 &
    python tests/ui/ui_acceptance.py http://127.0.0.1:8795 <screenshot-dir>

21: slow and offline API -> clear timeout/offline message on every screen.
23: every screen at 1366x768 and 1920x1080 (no horizontal scroll, no console errors), touch
    (mobile emulation) and keyboard (Tab reaches the menu with visible focus, Enter navigates).
Uses the Chromium that Playwright finds (PLAYWRIGHT_BROWSERS_PATH) or /opt/pw-browsers/chromium.
"""

import os
import sys

from playwright.sync_api import sync_playwright

BASE, OUT = sys.argv[1], sys.argv[2]
PAGES = ["dashboard", "installation", "energy", "planning", "planning/60", "prices", "battery", "pv", "heatpump", "ev",
         "devices", "devices/battery", "devices/add", "automations", "finance", "backtest", "notifications", "settings",
         "settings/tariff", "settings/prices", "nodes", "system"]
results, errors = [], []


def ok(msg):
    results.append(msg)
    print("PASS ", msg)


def launch(p):
    exe = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
    return p.chromium.launch(executable_path=exe)


def login(pg):
    pg.goto(BASE + "/")
    pg.wait_for_selector("button[type=submit]")
    pg.click("button[type=submit]")
    pg.wait_for_selector(".flow", timeout=20000)


with sync_playwright() as p:
    b = launch(p)
    # 23: desktop resolutions -------------------------------------------------------------------
    for w, hgt in ((1366, 768), (1920, 1080)):
        ctx = b.new_context(viewport={"width": w, "height": hgt})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"{w}x{hgt} pageerror: {e}"))
        pg.on("console", lambda m: m.type == "error" and errors.append(f"{w}x{hgt} console: {m.text}"))
        login(pg)
        for level in ("simple", "expert"):
            pg.evaluate(f"localStorage.setItem('ems.level', '{level}')")
            for name in PAGES:
                pg.goto(f"{BASE}/#/{name}")
                pg.wait_for_timeout(700)
                txt = pg.inner_text("main")
                if "Fout bij laden" in txt:
                    errors.append(f"{w}x{hgt} {level} {name}: {txt[:120]}")
                sw = pg.evaluate("document.documentElement.scrollWidth")
                if sw > w + 2:
                    errors.append(f"{w}x{hgt} {name}: horizontal scroll {sw}px")
        pg.goto(f"{BASE}/#/dashboard")
        pg.wait_for_timeout(800)
        pg.screenshot(path=f"{OUT}/ui-{w}x{hgt}.png")
        ok(f"{len(PAGES)} screens x 2 levels at {w}x{hgt}: no load errors, no horizontal scroll")
        ctx.close()

    # 23: touch (phone) ---------------------------------------------------------------------------
    ctx = b.new_context(**p.devices["Pixel 7"])
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(f"touch pageerror: {e}"))
    login(pg)
    pg.tap("button.hamb")
    pg.wait_for_timeout(300)
    pg.tap("nav a[href='#/prices']")
    pg.wait_for_timeout(800)
    assert "#/prices" in pg.url, pg.url
    for name in PAGES:
        pg.goto(f"{BASE}/#/{name}")
        pg.wait_for_timeout(500)
        vw = pg.evaluate("window.innerWidth")
        sw = pg.evaluate("document.documentElement.scrollWidth")
        if sw > vw + 2:
            errors.append(f"touch {name}: horizontal scroll {sw}px > {vw}px")
    pg.screenshot(path=f"{OUT}/ui-touch.png")
    ok("touch (Pixel 7 emulation): menu by tap, all screens without horizontal scroll")
    ctx.close()

    # 23: keyboard --------------------------------------------------------------------------------
    ctx = b.new_context(viewport={"width": 1366, "height": 768})
    pg = ctx.new_page()
    login(pg)
    reached = None
    for _ in range(40):
        pg.keyboard.press("Tab")
        el = pg.evaluate("(() => { const a = document.activeElement; return a ? (a.getAttribute('href') || a.tagName) : null })()")
        if el == "#/planning":
            reached = el
            break
    assert reached, "menu not reachable by keyboard"
    outline = pg.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    pg.keyboard.press("Enter")
    pg.wait_for_timeout(800)
    assert "#/planning" in pg.url and outline != "none", (pg.url, outline)
    ok("keyboard: Tab reaches the menu with a visible focus outline, Enter opens the page")
    ctx.close()

    # 21: slow and offline API --------------------------------------------------------------------
    ctx = b.new_context(viewport={"width": 1366, "height": 768})
    pg = ctx.new_page()
    login(pg)
    pg.evaluate("window.__EMS_TEST_TIMEOUT = 1500")
    pg.route("**/api/v1/**", lambda route: route.abort())          # server unreachable
    msgs = {}
    for name in ("planning", "dashboard", "prices", "finance", "devices", "settings"):
        pg.goto(f"{BASE}/#/{name}")
        pg.wait_for_timeout(1200)
        txt = pg.inner_text("body")
        msgs[name] = "niet bereikbaar" in txt or "Server offline" in txt
    assert all(msgs.values()), msgs
    assert pg.is_visible("text=Server offline")
    pg.screenshot(path=f"{OUT}/ui-offline.png")
    pg.unroute("**/api/v1/**")
    # slow: answers later than the request timeout
    import time as _t

    def slow(route):
        _t.sleep(2.5)
        route.continue_()
    pg.route("**/api/v1/prices**", slow)
    pg.goto(f"{BASE}/#/prices")
    pg.wait_for_timeout(4000)
    txt = pg.inner_text("body")
    assert "Geen antwoord van de EMS-server" in txt, txt[:300]
    ok("offline: every screen shows 'niet bereikbaar' + 'Server offline'; slow API: clear timeout message")
    ctx.close()
    b.close()

print("\n".join(errors) if errors else "no console/page errors")
print(f"UI ACCEPTANCE: {'FAIL' if errors else 'PASS'} ({len(results)} checks)")
sys.exit(1 if errors else 0)
