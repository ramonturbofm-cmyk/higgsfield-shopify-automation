# Testresultaten — Energy Manager 0.4.0

Alles hieronder is echt uitgevoerd. Niets is met echte hardware getest. CI-uitslagen zijn van commit
`998e56c (CI-runs 37862836117 en 37862836164, 2026-10-09)` op branch `claude/energy-manager-phase1`.

## Samenvatting

| Suite | Waar | Resultaat |
|---|---|---|
| pytest (Linux, Python 3.13) | ontwikkelomgeving | **306 passed, 0 failed, 0 skipped** |
| pytest (Linux, Python 3.12) | CI *tests and Raspberry Pi image* | **306 passed** in 108 s; `ems selftest` OK; EnergyZero live: 136 kwartier- en 34 uurprijzen |
| pytest (Windows, Python 3.12) | CI *Windows installer* | stap groen: geen failures; 1 test overgeslagen (MQTT-test vereist Mosquitto, niet op de Windows-runner) |
| ruff (backend, tests, tools) | lokaal + CI | geen meldingen |
| Linux-acceptatie Docker amd64 | lokaal (Docker 29) + CI | lokaal **6/6 PASS**; CI **6/6 PASS** |
| Raspberry Pi-acceptatie (Docker arm64 onder QEMU) | CI | **6/6 PASS** (`LINUX ACCEPTANCE (docker linux/arm64): PASS (6 checks)`) — emulatie, geen fysieke Pi |
| Linux-acceptatie systemd (native) | CI | **PASS** (job `linux-native` groen) |
| Windows-acceptatie (schone Windows-runner) | CI | **12/12 PASS** |
| Browser-acceptatie (`tests/ui/ui_acceptance.py`, Chromium) | lokaal tegen Demo-server | **5/5 PASS**, geen console- of paginafouten |
| Hardware | — | **NOT TESTED** (geen hardware beschikbaar) |

## pytest per onderwerp (audit)

| Bestand | Tests | Dekt |
|---|---|---|
| `test_audit_p0.py` | 11 | audit 1–6, 18, P0-04 bevestiging, P0-06 sessies/tokens |
| `test_safety_matrix.py` | 75 | P0-07/08: 9 commandotypes × 8 situaties, grenzen, SOC, rate limit, verouderde netmeting |
| `test_audit_tariffs.py` | 10 | audit 7, 8, 11, 12 |
| `test_audit_prices.py` | 7 | audit 9, 10 |
| `test_audit_planning.py` | 7 | audit 22, redencodes, WP-actie, horizon |
| `test_audit_meter.py` | 1 | audit 17 (gesimuleerd), primaire netmeter |
| `test_audit_ui_schema.py` | 21 | audit 19, 20 |
| `test_version.py` | 3 | audit 24 (versie, downgrade database/installer) |
| overige (`test_api`, `test_distributed`, `test_engine`, …) | 171 | regressie, nodes (audit 15, 16) |

Tijdens het werk gevonden en hersteld door deze tests: een echt apparaat zonder `control_level` in de YAML
kreeg standaard **volledige regeling** (nu alleen-lezen); de optimizer rapporteerde netladen terwijl de
batterij stilstond; tokens in localStorage/URL; automatiseringen met acties die het apparaat niet heeft.

## Browser-acceptatie (lokaal)

```
PASS  22 screens x 2 levels at 1366x768: no load errors, no horizontal scroll
PASS  22 screens x 2 levels at 1920x1080: no load errors, no horizontal scroll
PASS  touch (Pixel 7 emulation): menu by tap, all screens without horizontal scroll
PASS  keyboard: Tab reaches the menu with a visible focus outline, Enter opens the page
PASS  offline: every screen shows 'niet bereikbaar' + 'Server offline'; slow API: clear timeout message
UI ACCEPTANCE: PASS (5 checks)
```

## Linux-acceptatie (lokaal, Docker amd64)

```
PASS  server starts (docker); web interface served
PASS  first account, cookie session, CSRF enforced, settings saved
PASS  production mode: no fake data, 'Geen primaire netmeter ingesteld'
PASS  restart: account and settings kept
PASS  crash (process killed): Docker restarts the container
PASS  upgrade/new container on the same data: everything kept
LINUX ACCEPTANCE (docker): PASS (6 checks)
```

## Windows-acceptatie (CI)

Installer `EnergyManagerSetup-0.4.0.exe`, 69.832.773 bytes (66,6 MB), SHA-256
`096364a464e1b9e33f45959c721586f36911ef5c988bbe48782462a5a3f7e377` (na publicatie opnieuw gedownload en
nagerekend: gelijk). UNSIGNED TEST BUILD.

```
PASS  install: files, Start menu entries and autostart entry present
PASS  Windows app launches (WebView2) and stays open
PASS  background EMS (no window) runs; database created (sqlite, schema v2)
PASS  live dashboard data: grid 5274 W, EMS status AUTOMATIC
PASS  optimizer active: 144 planned slots, status optimal
PASS  WebSocket delivers live updates
PASS  settings saved
PASS  app closed, EMS keeps running in the background
PASS  restart via autostart (as after a Windows reboot): settings, mode and history kept (4 -> 5 samples)
PASS  upgrade over existing install: EMS stopped by installer, data and settings kept
PASS  version 0.4.0 everywhere; downgrade over a newer version refused (exit code 1), EMS untouched
PASS  uninstall: program, autostart entry and background EMS removed; user data kept
```

Eerdere poging (run 37862079894) faalde bij de upgrade-stap: de installer wachtte niet tot het EMS-proces
echt was afgesloten. Opgelost in `windows/installer.iss` (wachten op procesexit) en daarna groen.

## Niet getest

* Echte apparaten (HomeWizard P1, DSMR, Modbus/HTTP/MQTT-apparaten, omvormers, batterijen, warmtepompen,
  laadpalen) — BLOCKED BY HARDWARE.
* Fysieke Raspberry Pi (alleen arm64 onder QEMU).
* Nodes op meerdere fysieke machines; verliesgevend netwerk.
* Windows 10 specifiek (de CI-runner is Windows Server / Windows 11-kern, build 26100).
