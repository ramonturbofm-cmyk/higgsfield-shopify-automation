# DEVELOPMENT — voortgang, teststatus en besluiten

Laatst bijgewerkt: versie 0.2.0 — alle fases zonder hardwareafhankelijkheid zijn uitgevoerd.

## Legenda

| Teken | Betekenis |
|---|---|
| `[x]` | klaar en getest |
| `[~]` | werkt, met bekende beperking (zie toelichting) |
| `[ ]` | nog niet gebouwd |
| `[!]` | geblokkeerd door een externe factor (meestal: hardware/documentatie van de gebruiker nodig) |

Teststatus per onderdeel:

* **UNIT TESTED** — geautomatiseerde tests (`pytest`, 145 tests) tegen echte code, met nagebootste
  apparaten waar nodig (TLS-fake van een HomeWizard P1, fake Modbus TCP-server, lokale HTTP-server,
  echte Mosquitto-broker, DSMR-telegrammen uit de standaard).
* **SIMULATOR TESTED** — draait end-to-end tegen de fysieke woningsimulator (Demo Mode / `ems-sim`).
* **HARDWARE TESTED** — getest met een echt apparaat. **Tot nu toe is niets hardware-getest**: er was
  geen hardware beschikbaar in de ontwikkelomgeving.

## Status per onderdeel

### Kern en server (Raspberry Pi)

| Onderdeel | Status | Test |
|---|---|---|
| EMS-kern: regelcyclus, snapshot, validatie, bevroren-data, fail-safe, watchdog, journaal | `[x]` | UNIT, SIMULATOR |
| `ems serve`: één proces met engine, optimizer, historie, API, WebSocket | `[x]` | UNIT, SIMULATOR |
| Database (SQLite WAL standaard, PostgreSQL optioneel), migraties, 15-min-aggregaten | `[x]` | UNIT (SQLite); PostgreSQL alleen via compose-profiel, niet in CI |
| REST API `/api/v1` (80 endpoints) + OpenAPI (`/api/docs`) | `[x]` | UNIT |
| WebSocket `/api/v1/ws` (live, plan, meldingen, beslissingen) | `[x]` | UNIT |
| Auth: gebruikers, rollen viewer/operator/admin/installer, JWT, API-tokens, login-rate-limit | `[x]` | UNIT |
| Versleutelde secrets (Fernet, `secret.key` 0600); `${VAR}` uit `.env`; geen geheimen in YAML | `[x]` | UNIT |
| Health (`/healthz`, `/api/v1/system/status`), systemd `Type=notify` + `WatchdogSec` | `[x]` | UNIT; systemd-unit niet op een echte Pi gedraaid |
| Logging: JSON naar stdout/journald (Docker-logs met max-size-rotatie), ringbuffer in de UI | `[x]` | UNIT |
| Back-up/restore (zip: config, database, secrets), automatische dagelijkse back-up | `[x]` | UNIT (Linux + Windows CI) |
| Meldingen: in-app, WebSocket, webhook | `[~]` | UNIT — geen e-mail/push (webhook naar bijv. ntfy/Home Assistant kan) |
| Multi-site | `[~]` | Voorbereid (`site_id` overal); één site per proces |
| HTTPS | `[~]` | `--tls-cert/--tls-key` aanwezig; standaard HTTP op het LAN (nooit naar internet openzetten) |

### Meting, netmeter en apparaten

| Onderdeel | Status | Test |
|---|---|---|
| GridMeter-abstractie + selectie primaire netmeter (expliciet → HomeWizard → DSMR → Modbus aanbieden → geen) | `[x]` | UNIT, SIMULATOR |
| Functiebeperking zonder primaire netmeter ("Geen primaire netmeter ingesteld…") | `[x]` | UNIT |
| HomeWizard P1 (API v1 + v2): mDNS, koppelen met knop, token in secret store, HTTPS met HomeWizard-CA, WebSocket + polling-fallback, stale-detectie, diagnostiek | `[~]` | UNIT (TLS-fake volgens officiële docs) — **niet HARDWARE TESTED** |
| DSMR P1 direct (USB-kabel of ser2net/TCP), CRC16, DSMR 2.2–5 | `[~]` | UNIT (telegrammen uit de standaard) — **niet HARDWARE TESTED** |
| Generiek Modbus TCP (alleen lezen, functie 3/4, int16…int64/float32, woordvolgorde, schaal) | `[~]` | UNIT (fake server volgens de Modbus-spec) — **niet HARDWARE TESTED** |
| Generiek HTTP/JSON (alleen lezen, JSON-paden, optionele auth-header) | `[~]` | UNIT — **niet HARDWARE TESTED** |
| Generiek MQTT (alleen abonneren, JSON-pad of kale waarde, verouderingsgrens) | `[~]` | UNIT + echte Mosquitto-broker — **niet HARDWARE TESTED**; op een Windows-server niet getest |
| Mockdrivers (meter, PV, batterij, warmtepomp, laadpaal) — alleen in Demo Mode | `[x]` | UNIT, SIMULATOR |
| Schrijvende drivers voor echte omvormers/batterijen/warmtepompen/laadpalen | `[!]` | Geblokkeerd: merk, model, firmware, interface en officiële documentatie nodig |
| Apparaatwizard met verbindingstest (✓/✗ per functie) | `[x]` | UNIT, browser |

### Optimalisatie en regeling

| Onderdeel | Status | Test |
|---|---|---|
| Tariefengine (dynamisch/vast/variabel, salderen, opslagen, energiebelasting, btw) | `[x]` | UNIT |
| Prijsbronnen: ENTSO-E (A44), handmatig, demo; cache in DB; schatting als prijzen ontbreken | `[x]` | UNIT (ENTSO-E-parser met voorbeeld-XML); ENTSO-E live vereist eigen token |
| Weer/PV-prognose (Open-Meteo), kalibratie op historie; verbruiksprofiel uit historie | `[x]` | UNIT, SIMULATOR |
| Rolling-horizon MILP-optimizer (HiGHS), 36 h × 15 min, < 0,5 s | `[x]` | UNIT, SIMULATOR, E2E-scenario's 12:00 en 19:00 |
| Batterijprofielen Battery Saver / Balanced / Profit / Aggressive, slijtagekosten, max. cycli | `[x]` | UNIT |
| PV: zero-export (gesloten lus), slim/onbeperkt terugleveren, curtailment bij negatieve prijs | `[x]` | UNIT, SIMULATOR |
| Warmtepomp: thermische buffer (RC-model), comfortgrenzen (min. looptijd bewaakt het apparaat zelf) | `[x]` | UNIT, SIMULATOR |
| EV: vertrektijd/doel, min. stroom, fasebewaking | `[x]` | UNIT, SIMULATOR |
| Netlimieten/fasebewaking, piekbegrenzing | `[x]` | UNIT, SIMULATOR |
| Automatiseringen (ALS/EN/OF/DAN, tijdvensters, overrides) + editor | `[x]` | UNIT, browser |
| Inbedrijfstelling: CONNECTION TEST → READ ONLY → SHADOW ("EMS zou…") → LIMITED → FULL | `[x]` | UNIT, SIMULATOR |
| Handmatige overrides met verlooptijd | `[x]` | UNIT |
| Backtesting + Auto-Tune (IGNORE/TEST/APPLY, nooit automatisch) | `[x]` | UNIT, SIMULATOR |
| Financieel overzicht met/zonder EMS | `[x]` | UNIT, SIMULATOR |

### Bediening en installatie

| Onderdeel | Status | Test |
|---|---|---|
| Webinterface (21 pagina's, light/dark/auto, mobiel, live via WebSocket) | `[x]` | Browser (Playwright): alle pagina's zonder consolefouten, licht/donker/mobiel |
| Demo Mode "Demo Home" (8 kWp, 15 kWh, warmtepomp, EV, 3×25 A, dynamische prijs) | `[x]` | UNIT, SIMULATOR |
| Productiemodus zonder nepdata ("Niet beschikbaar"/"Geen data") | `[x]` | UNIT |
| Raspberry Pi: Docker-image (amd64 + arm64), docker-compose, `install.sh` (install/demo/update met rollback/backup/watchdog) | `[x]` | Docker build + healthcheck in sandbox en CI (arm64 via QEMU); **niet op een fysieke Pi gedraaid** |
| Windows alles-in-één: app start de ingebouwde EMS-server op de achtergrond (eigen installatie of Demo), automatisch starten bij aanmelden, netjes stoppen (token, alleen 127.0.0.1) | `[~]` | UNIT (start/stop/al-actief), startscherm getest in Chromium met echte server; achtergrondserver starten/opvragen/stoppen getest op Windows in CI; app zelf niet op een fysieke Windows-pc getest |
| Windows-app (Tauri 2): Raspberry Pi zoeken/handmatig, automatisch opnieuw verbinden, "Andere server" | `[~]` | Gecompileerd, verbindingsscherm getest in Chromium tegen een echte server; Windows-build via CI |
| Windows-installer `EnergyManagerSetup-<versie>.exe` (standaard alles op deze computer, of alleen de app) | `[~]` | Gebouwd door GitHub Actions; niet ondertekend (SmartScreen-melding) |

## Openstaande punten / wat de gebruiker nog moet aanleveren

1. **Hardware voor echte drivers** `[!]` — per apparaat: merk, exact model, firmwareversie, aansluiting
   (LAN/RS485/USB/cloud) en de officiële protocoldocumentatie. Tot die tijd: lezen via HomeWizard/DSMR of
   de generieke drivers met een mapping uit de eigen handleiding; **sturen van echte apparaten is
   bewust niet geïmplementeerd** (geen verzonnen registers/commando's).
2. **ENTSO-E-token** voor echte day-ahead-prijzen (gratis aan te vragen bij ENTSO-E); zonder token werkt
   handmatige prijsinvoer.
3. **Hardwaretest** van HomeWizard P1 en DSMR P1 op de doelinstallatie; daarna `verified=True` zetten en
   de teststatus hier bijwerken.
4. Code signing van de Windows-installer (certificaat nodig).

## Bekende beperkingen

* Zonder schrijvende driver kan het EMS niets aansturen; de inbedrijfstelling blijft dan op READ ONLY /
  SHADOW staan en toont wat het EMS zou doen.
* Een snelle (< 1 s) lokale regellus vereist een push-meter (HomeWizard WebSocket/DSMR elke seconde);
  de regelcyclus is standaard 10 s.
* MQTT-driver gebruikt `aiomqtt`; op een Windows-host met de standaard Proactor-eventloop is MQTT niet
  getest. De Raspberry Pi is het productieplatform.
* Simulator kent nog geen ontdooicycli, tapwater of batterijveroudering in de fysica (wel slijtagekosten
  in de optimizer).
* De HomeWizard-API is volgens de licentie van HomeWizard bedoeld voor persoonlijk, niet-commercieel
  gebruik.

## Architectuurbesluiten (ADR-log)

1. **Python/asyncio-kern, Pydantic-config** — zie ARCHITECTURE §2.
2. **Apparaten houden hun eigen regeling; EMS verschuift setpoints.** `release_control()` is verplicht.
3. **Eén pad naar hardware (`CommandGate`)** met simulatie/dry-run, deduplicatie en inbedrijfstellingsniveau.
4. **Bevroren-data via vingerafdruk** i.p.v. alleen netvermogen (voorkwam 90 valse fail-safes/dag).
5. **Keep-alive-verversingen zijn geen beslissingen** (geen journaalruis).
6. **Simulator binnen het `ems`-pakket** zodat Demo Mode, backtester en tests hem hergebruiken.
7. **Deterministische omgevingssignalen** (seeded) → reproduceerbare tests en prognoses.
8. **Optimizer: MILP met HiGHS via `scipy.optimize.milp`** met een eigen dunne modelleerlaag; binaire
   variabelen alleen waar nodig (laden XOR ontladen alleen bij `need_yb`, netrichting alleen bij
   `need_yg`). Netladen wordt lineair begrensd (batterij laden uit het net ≤ laadvermogen − PV-overschot)
   i.p.v. met extra binaries: 22 s → 0,03 s per plan.
9. **Tie-breaker "eerder handelen"**: bij gelijke kosten laadt/ontlaadt de planning liever nu dan later,
   zodat het plan niet onnodig verschuift tussen runs.
10. **Database: SQLite (WAL) standaard, PostgreSQL optioneel** i.p.v. TimescaleDB: één bestand, geen
    extra container op de Pi, ruim voldoende voor 15-min-aggregaten van één woning.
11. **Webinterface in vanilla ES-modules zonder buildstap** i.p.v. React/TS: geen Node-toolchain op de
    Pi, strikte CSP (`script-src 'self'`), klein en direct te debuggen. De Windows-app laadt dezelfde UI
    van de server.
12. **Windows-app = Tauri-shell met verbindingsscherm** dat de web-UI van de server laadt (geen tweede
    UI-codebase). CORS staat alleen de app-origin toe, incl. Private Network Access-preflight.
13. **GridMeter-abstractie**: de optimizer/controllers kennen geen merken, alleen `snap.grid`.
14. **Generieke drivers bevatten geen apparaatkennis**: registers/paden/topics komen uit de mapping die
    de installateur uit de eigen documentatie overneemt; ze zijn altijd alleen-lezen.
15. **Wachtwoorden die in de wizard worden ingevuld** gaan naar de versleutelde secret store; de YAML
    bevat alleen `secret:device.<id>.<veld>` (of `${VAR}`).
16. **Back-up terugzetten** gebeurt terwijl de engine stilstaat en de database gesloten is (Windows
    vergrendelt open bestanden).
17. **Windows alles-in-één = aparte achtergrondserver** (`EnergyManagerService.exe`, zonder venster) die de app
    start, i.p.v. de server ín het app-proces: het EMS blijft regelen als het app-venster dicht is, kan bij
    aanmelden starten en wordt netjes gestopt via een lokaal token (`control.txt`), zodat apparaten eerst
    worden vrijgegeven.

## Testen

```bash
cd energy-manager
pip install -e ".[dev]"
ruff check backend tests
pytest -q                 # 145 tests, ~1,5 min
ems selftest              # snelle zelftest van een geïnstalleerde build
```

CI (`.github/workflows/energy-manager-ci.yml`): lint, tests (met Mosquitto), selftest, Docker
amd64+arm64 en container-healthcheck. Windows (`energy-manager-windows.yml`): tests op Windows,
PyInstaller-server + selftest, Tauri-app, Inno Setup-installer.

Releaseprocedure: [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
