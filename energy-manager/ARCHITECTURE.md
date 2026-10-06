# Architectuur — Energy Manager

Dit document beschrijft het technisch ontwerp, de gemaakte keuzes en waarom.
Status per fase staat in [DEVELOPMENT.md](DEVELOPMENT.md).

## 1. Uitgangspunten

1. **De Raspberry Pi is autonoom.** Alle regeling, opslag en optimalisatie draait op de Pi.
   De Windows-app en webinterface zijn alleen bedieningsschermen.
2. **Veiligheid boven economie.** Het EMS *verschuift* alleen setpoints. Elk apparaat houdt zijn
   eigen veilige regeling (thermostaat, vorstbeveiliging, zelfconsumptiemodus, laadpaal-standaard).
   Valt het EMS weg, dan valt de installatie terug op die eigen regeling — nooit op "uit".
3. **Generiek, niet merkgebonden.** De kern spreekt één vocabulaire (`Metric`, `Capability`,
   `Command`). Drivers vertalen dat naar een concreet protocol.
4. **Geen verzonnen protocollen.** Een driver bevat alleen registers/endpoints/topics uit officiële
   documentatie. Ontbreekt die: mock- of generieke driver.
5. **Alles uitlegbaar.** Elke actie reist als `Decision` (commando + redenen + data) en komt in
   het beslissingslogboek.
6. **Alles simuleerbaar.** Eén `Clock`-abstractie maakt real-time, versneld simuleren, backtesten
   en deterministische tests mogelijk met precies dezelfde code.

## 2. Technische keuzes

| Onderdeel | Keuze | Waarom | Overwogen alternatieven |
|---|---|---|---|
| Taal / runtime | **Python 3.11+ met asyncio** | Rijk ecosysteem (Modbus, MQTT, optimalisatie, data), goed op ARM64; asyncio = veel gelijktijdige apparaat-I/O zonder threads | Go/Rust (sneller, maar kleiner energie-/optimalisatie-ecosysteem) |
| Datamodel & config | **Pydantic v2** | Validatie, JSON-schema met UI-metadata (labels, SIMPLE/ADVANCED/EXPERT) → instellingenschermen worden gegenereerd | dataclasses + handmatige validatie |
| API | **FastAPI + uvicorn** (fase 2) | Async, automatische OpenAPI-documentatie, WebSockets, Pydantic-native | Flask (sync), Django (zwaar) |
| Database | **PostgreSQL 16 + TimescaleDB** (Docker); **SQLite**-profiel voor kleine/dev-installaties | Hypertables, compressie en *continuous aggregates* voor automatisch downsamplen (5 s → 1 min → 15 min); één database voor config én tijdreeksen; SQL voor rapportages/backtests | InfluxDB (tweede database + eigen querytaal), alleen SQLite (geen compressie/aggregaten) |
| Opslagmedium | **SSD via USB3** aanbevolen | SD-kaarten slijten door continue schrijfacties | — |
| Optimizer | **MILP met HiGHS** (via `scipy.optimize.milp` / `highspy`) met een eigen dunne modelleerlaag | Binaire keuzes zijn nodig (niet tegelijk laden/ontladen, min. looptijd warmtepomp, EV-minimumstroom, PV aan/uit); HiGHS is de snelste open-source MILP-solver, MIT-licentie, ARM64-wheels. 36 h × 15 min ≈ 144 stappen × ~15 variabelen → < 1 s op een Pi 4 | Pyomo (zwaar, extra solver nodig), OR-Tools (groot, CP-SAT minder geschikt voor continue vermogens), PuLP+CBC (trager, CBC-binary op ARM lastiger) |
| Communicatie app ↔ Pi | **REST (OpenAPI) + WebSocket** over HTTPS op het LAN | REST voor configuratie/opdrachten, WebSocket voor live-data (≈1 s) en meldingen | gRPC (slecht in browsers) |
| Interne/externe bus | **In-process EventBus**; optionele **MQTT-brug (Mosquitto)** | Kern werkt zonder broker; MQTT voor Home Assistant en MQTT-apparaten | Broker verplicht maken (extra faalpunt) |
| Windows-app | **Tauri 2** (Rust-shell + WebView2) | ~10 MB installer i.p.v. ~150 MB (Electron); zelfde webfrontend als de Pi-webinterface; Windows Credential Manager via plugin; ondertekende auto-updates | Electron (zwaar), WinUI/.NET (tweede UI-codebase) |
| Webinterface | **React + TypeScript + Vite**, PWA, geserveerd door de Pi | Eén frontend-codebase voor Windows (Tauri), telefoon en tablet; light/dark/auto via CSS-variabelen + `prefers-color-scheme`; grafieken met uPlot/ECharts | Vue/Svelte (prima, minder ecosysteem voor dashboards) |
| Deployment | **Docker Compose** op Raspberry Pi OS Lite 64-bit | Reproduceerbaar, eenvoudige updates/rollback, volumes houden data en config bij updates | Bare-metal pip-installatie (lastiger updaten) |

## 3. Lagen en verantwoordelijkheden

```
            Windows-app (Tauri)        Telefoon / tablet (PWA)
                     \                    /
                      HTTPS: REST + WebSocket          ← fase 2/3
                               |
   ┌─────────────────────────── Raspberry Pi ────────────────────────────┐
   │ api/            FastAPI, auth, rate limiting, OpenAPI               │
   │ services/       history, export, back-up, notificaties, updates     │
   │ ─────────────────────────────────────────────────────────────────── │
   │ core/engine     regelcyclus, fail-safe, watchdog, journal, events   │  ← fase 1
   │ control/        controllers, overrides, CommandGate                 │  ← fase 1
   │ optimizer/      rolling-horizon MILP → plan → OptimizerController   │  ← fase 6+
   │ forecasting/    PV, verbruik, temperatuur, warmtevraag              │  ← fase 9
   │ tariffs/        contract → get_import_price / get_export_price      │  ← fase 5
   │ automations/    ALS/EN/OF/DAN/ANDERS-regels                         │  ← fase 10
   │ ─────────────────────────────────────────────────────────────────── │
   │ devices/        driver-interface, registry, DeviceManager           │  ← fase 1
   │ integrations/   mock/ (fase 1), p1_dsmr/, generic_modbus/, ...      │
   │ simulator/      fysiek model van een woning                         │  ← fase 1
   │ database/       TimescaleDB/SQLite repositories                     │  ← fase 2
   └─────────────────────────────────────────────────────────────────────┘
```

Afhankelijkheden lopen alleen naar beneden: `control` kent `core` en `devices`, maar `devices`
kent geen controllers. Integraties kennen alleen `core.models` en `devices.base`
(mockdrivers daarnaast de simulator).

## 4. De regelcyclus (fase 1, gebouwd)

Elke `control.interval_s` (standaard 10 s):

1. **Poll** — `DeviceManager` leest alle drivers gelijktijdig, met timeout per apparaat.
   Fouten → status `STALE`/`OFFLINE`, automatisch herverbinden met back-off.
2. **Valideer** — onmogelijke waarden (SOC 140 %, NaN, 1e12 W) worden geweigerd;
   een meter waarvan de *vingerafdruk* (vermogen + fasespanningen + meterstanden) minutenlang
   identiek is, geldt als bevroren.
3. **Snapshot** — alle apparaten worden samengevoegd tot één `SiteSnapshot`
   (meerdere PV-velden/batterijen/EV's = één systeem; SOC capaciteitsgewogen).
   De `grid_reference`-meter is de waarheid aan de aansluiting; huisverbruik wordt afgeleid.
4. **Gezondheid** — ontbreekt een betrouwbare netmeting langer dan `grid_stale_after_s`,
   of crasht de controller → **fail-safe**: alle apparaten `release_control()`, journaalregel,
   event. Herstel pas na `failsafe_recover_ticks` gezonde cycli.
5. **Beslissen** — de actieve `Controller` levert `Decision`s (commando + redenen).
6. **Overrides** — handmatige bediening vervangt automatische beslissingen per commandogroep,
   verloopt automatisch (30 min … onbeperkt) en geeft het apparaat daarna vrij → terug naar AUTO.
7. **CommandGate** — de enige weg naar hardware:
   * `DRY_RUN` → niets uitvoeren, journaal "EMS zou …";
   * `SIMULATION_MODE` → alleen drivers met `manifest.simulated` worden beschreven;
   * dedupliceert (deadband per actie) en ververst periodiek (keep-alive voor Modbus-remote-control).
8. **Journaal + events** — iedere verstuurde/proef-beslissing met `run_id`, oude/nieuwe waarde,
   redenen, bron (controller/override/engine) en (vanaf fase 5/6) prijs en verwachte winst.

### Fail-safe-lagen

| Storing | Opvang |
|---|---|
| Apparaat traag/offline | timeout per apparaat; regelcyclus loopt door |
| Netmeting weg / onrealistisch / bevroren | fail-safe na `grid_stale_after_s` |
| Bug in strategie | exception → fail-safe |
| Regelcyclus hangt | `Watchdog` (heartbeat) → vrijgeven; systemd `WatchdogSec` herstart de service (fase 2) |
| Raspberry Pi crasht | hardware-watchdog herstart de Pi; apparaten draaien op eigen regeling; drivers gebruiken waar mogelijk **time-outs in het apparaat zelf** (remote-control vervalt automatisch) |
| Internet/prijsdata weg | cache + laatst bekende planning; daarna regelgebaseerde zelfconsumptie (fase 5) |
| Database weg | regelcyclus is onafhankelijk van de database; schrijven wordt gebufferd (fase 2) |

## 5. Tekenconventies en eenheden

| Grootheid | Positief | Negatief |
|---|---|---|
| Netvermogen, fasestroom | afname (import) | teruglevering (export) |
| Batterijvermogen | laden | ontladen |
| PV, warmtepomp, EV, belastingen | productie / verbruik | — |

Eenheden: W, kWh, °C, %, A, V, EUR. Tijden intern altijd UTC (timezone-aware);
weergave in de tijdzone van de locatie.

## 6. Pluginarchitectuur

* Een driver = subklasse van `DeviceDriver` met een `DriverManifest`
  (id, categorieën, capabilities, verbindingstypen, `simulated`, `verified`, documentatiebron,
  velden voor de apparaatwizard).
* Registratie met `@register_driver`; `DriverRegistry.discover()` importeert alles onder
  `ems.integrations` én pakketten van derden via entry-point-groep `ems.drivers`.
* De kern vraagt alleen *capabilities* op ("kan batterijmodus sturen"), nooit merken.
* `self_test()` levert het rapport voor de apparaatwizard (✓/✗ per functie).

Zie [DEVICE_INTEGRATION_GUIDE.md](DEVICE_INTEGRATION_GUIDE.md).

## 7. Optimizer-ontwerp (fase 6, gepland)

* Rolling horizon: elke `optimizer.interval_minutes` (5) een plan voor `horizon_hours` (36)
  in stappen van `time_step_minutes` (15, gelijk aan de day-ahead-resolutie).
* Doelfunctie: Σ (importprijs·import − exportprijs·export) + slijtage·doorvoer
  + comfortstraf + eindwaarde van opgeslagen energie (voorkomt "leeg eindigen").
* Beperkingen: energiebalans per stap, SOC-dynamiek met laad-/ontlaadrendement, min/max/reserve-SOC,
  vermogenslimieten, netlimieten, fase-limieten (lineair benaderd), thermisch RC-model woning,
  EV-vertrektijd/doel-SOC, max. cycli/dag.
* Binair: laden XOR ontladen, warmtepomp aan/uit met min. loop-/stilstandtijd, EV ≥ minimumstroom.
* Uitvoering: `OptimizerController` voert stap 1 uit; een snelle lokale regellus (zero-export,
  fasebewaking) corrigeert afwijkingen binnen de stap. Solver-time-out/geen oplossing →
  regelgebaseerde fallback.
* Uitlegbaarheid: per beslissing de schaduwprijzen en het verschil met de "niets doen"-baseline
  → "verwacht netto voordeel € 1,42".
* Dezelfde optimizer + simulator vormen de backtester (fase 11).

## 8. Data & historie (fase 2)

* Live: ruwe meetwaarden elke 5–10 s → hypertable `measurements` (14 dagen bewaren).
* Continuous aggregates: 1 min (1 jaar), 15 min en uur (onbeperkt).
* Tabellen voor beslissingen, plannen, prognoses, prijzen, configuratieversies.
* Alles gesleuteld op `site_id` → multi-site zonder schemawijziging.

## 9. Beveiliging (fase 2)

* Alleen LAN; geen poorten naar internet. Toegang op afstand via WireGuard/Tailscale.
* HTTPS met lokaal gegenereerde CA; de Windows-app pint de CA-vingerafdruk bij koppelen
  (koppelcode/QR op de Pi).
* Lokale gebruikers, Argon2id-wachtwoordhashes, korte JWT-access-tokens + roterende refresh-tokens.
* Rollen: `viewer`, `operator` (overrides), `admin` (instellingen), `installer` (apparaten/drivers).
* API-tokens met scope voor integraties (alleen gehasht opgeslagen).
* Apparaatwachtwoorden/API-sleutels versleuteld (Fernet) met een sleutel buiten de database
  (`0600`-bestand of systemd-credentials); in config alleen `${VARIABELE}`-verwijzingen.
* Bearer-tokens in de `Authorization`-header (geen cookies) → geen CSRF-vector; strikte CSP tegen XSS;
  rate limiting op login en schrijvende endpoints.

## 10. Deployment & updates (fase 2)

* Docker Compose: `ems-core`, `timescaledb`, optioneel `mosquitto`, reverse proxy met TLS.
* Volumes voor `/var/lib/ems` (database, config, sleutels, back-ups).
* Updates: versie-images, vóór de update automatische back-up, database-migraties (Alembic),
  health-check na start, automatische rollback bij falen. Configuratie en historie blijven behouden.
* Back-up/restore: één archief (config + database-dump + sleutels, versleuteld) → verhuizen naar een nieuwe Pi.

## 11. Multi-site

Elke configuratie heeft een `site.id`; snapshots, journaal en (straks) database-records dragen die id.
Een tweede locatie = een tweede `EMSConfig` + engine-instantie (`SiteRuntime`), onder dezelfde API.
