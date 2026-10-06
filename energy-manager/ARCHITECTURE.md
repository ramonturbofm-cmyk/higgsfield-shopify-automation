# Architectuur — Energy Manager

Dit document beschrijft het technisch ontwerp, de gemaakte keuzes en waarom.
Status per onderdeel staat in [DEVELOPMENT.md](DEVELOPMENT.md).

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
| API | **FastAPI + uvicorn** | Async, automatische OpenAPI-documentatie, WebSockets, Pydantic-native | Flask (sync), Django (zwaar) |
| Database | **SQLite (WAL)** standaard, **PostgreSQL** optioneel (SQLAlchemy Core, eigen migraties) | Eén bestand, geen extra container op de Pi; ruwe samples 14 dagen + eigen 15-min-aggregaten (onbeperkt) is ruim voldoende voor één locatie; PostgreSQL voor wie al een server heeft | TimescaleDB (oorspronkelijk plan; extra container en geheugen op de Pi zonder merkbaar voordeel bij deze datavolumes), InfluxDB (tweede querytaal) |
| Opslagmedium | **SSD via USB3** aanbevolen | SD-kaarten slijten door continue schrijfacties | — |
| Optimizer | **MILP met HiGHS** (via `scipy.optimize.milp`) met een eigen dunne modelleerlaag | Binaire keuzes waar nodig (niet tegelijk laden/ontladen, netrichting, EV-minimumstroom); HiGHS is de snelste open-source MILP-solver, MIT-licentie, ARM64-wheels. 36 h × 15 min → typisch 0,03–0,3 s | Pyomo (zwaar, extra solver nodig), OR-Tools (groot, CP-SAT minder geschikt voor continue vermogens), PuLP+CBC (trager, CBC-binary op ARM lastiger) |
| Communicatie app ↔ Pi | **REST (OpenAPI) + WebSocket** op het LAN (HTTP; HTTPS met `--tls-cert/--tls-key`) | REST voor configuratie/opdrachten, WebSocket voor live-data, planning en meldingen | gRPC (slecht in browsers) |
| Interne/externe bus | **In-process EventBus**; MQTT alleen als bron (generieke MQTT-driver) | Kern werkt zonder broker | Broker verplicht maken (extra faalpunt) |
| Windows-app | **Tauri 2** (Rust-shell + WebView2) met een eigen verbindingsscherm dat daarna de webinterface van de server laadt | Kleine app (enkele MB); geen tweede UI-codebase; app-instellingen (laatste server) lokaal | Electron (zwaar), WinUI/.NET (tweede UI-codebase) |
| Webinterface | **Vanilla ES-modules zonder buildstap**, eigen SVG-grafieken, geserveerd door de server | Geen Node-toolchain op de Pi of in de Docker-build; strikte CSP (`script-src 'self'`); light/dark/auto via CSS-variabelen; één UI voor Windows, telefoon en tablet | React/TS + Vite (oorspronkelijk plan; extra buildketen zonder functionele winst voor deze omvang) |
| Deployment | **Docker Compose** op Raspberry Pi OS Lite 64-bit | Reproduceerbaar, eenvoudige updates/rollback, volumes houden data en config bij updates | Bare-metal pip-installatie (lastiger updaten) |

## 3. Lagen en verantwoordelijkheden

```
            Windows-app (Tauri)        Telefoon / tablet (PWA)
                     \                    /
                      HTTP(S): REST + WebSocket
                               |
   ┌─────────────────────────── Raspberry Pi ────────────────────────────┐
   │ api/            FastAPI, auth, rate limiting, OpenAPI               │
   │ services/       history, export, back-up, notificaties, updates     │
   │ ─────────────────────────────────────────────────────────────────── │
   │ core/engine     regelcyclus, fail-safe, watchdog, journal, events   │
   │ control/        controllers, overrides, CommandGate                 │
   │ optimizer/      rolling-horizon MILP → plan → OptimizingController  │
   │ forecasting/    PV, verbruik, temperatuur, warmtevraag              │
   │ tariffs/        contract → get_import_price / get_export_price      │
   │ automations/    ALS/EN/OF/DAN/ANDERS-regels                         │
   │ ─────────────────────────────────────────────────────────────────── │
   │ devices/        driver-interface, registry, DeviceManager           │
   │ integrations/   homewizard/ dsmr/ generic/ (modbus,http,mqtt) mock/ │
   │ simulator/      fysiek model van een woning                         │
   │ gridmeter/      primaire netmeter, functiebeperking                 │
   │ database/       SQLite (standaard) / PostgreSQL                     │
   └─────────────────────────────────────────────────────────────────────┘
```

Afhankelijkheden lopen alleen naar beneden: `control` kent `core` en `devices`, maar `devices`
kent geen controllers. Integraties kennen alleen `core.models` en `devices.base`
(mockdrivers daarnaast de simulator).

## 4. De regelcyclus

Elke `control.interval_s` (standaard 10 s):

1. **Poll** — `DeviceManager` leest alle drivers gelijktijdig, met timeout per apparaat.
   Fouten → status `STALE`/`OFFLINE`, automatisch herverbinden met back-off.
2. **Valideer** — onmogelijke waarden (SOC 140 %, NaN, 1e12 W) worden geweigerd;
   een meter waarvan de *vingerafdruk* (vermogen + fasespanningen + meterstanden) minutenlang
   identiek is, geldt als bevroren.
3. **Snapshot** — alle apparaten worden samengevoegd tot één `SiteSnapshot`
   (meerdere PV-velden/batterijen/EV's = één systeem; SOC capaciteitsgewogen).
   De **primaire netmeter** (GridMeter, §7) is de waarheid aan de aansluiting; huisverbruik wordt afgeleid.
4. **Gezondheid** — ontbreekt een betrouwbare netmeting langer dan `grid_stale_after_s`,
   of crasht de controller → **fail-safe**: alle apparaten `release_control()`, journaalregel,
   event. Herstel pas na `failsafe_recover_ticks` gezonde cycli.
5. **Beslissen** — de actieve `Controller` levert `Decision`s (commando + redenen).
6. **Overrides** — handmatige bediening vervangt automatische beslissingen per commandogroep,
   verloopt automatisch (30 min … onbeperkt) en geeft het apparaat daarna vrij → terug naar AUTO.
7. **CommandGate** — de enige weg naar hardware:
   * `DRY_RUN` → niets uitvoeren, journaal "EMS zou …";
   * `SIMULATION_MODE` → alleen drivers met `manifest.simulated` worden beschreven;
   * **inbedrijfstellingsniveau per apparaat** (§8): READ ONLY → niets, SHADOW → alleen journaal "EMS zou…",
     LIMITED → begrensde opdrachten, FULL → alles;
   * dedupliceert (deadband per actie) en ververst periodiek (keep-alive voor Modbus-remote-control).
8. **Journaal + events** — iedere verstuurde/proef-beslissing met `run_id`, oude/nieuwe waarde,
   redenen, bron (controller/override/engine), prijs en verwacht voordeel.

### Fail-safe-lagen

| Storing | Opvang |
|---|---|
| Apparaat traag/offline | timeout per apparaat; regelcyclus loopt door |
| Netmeting weg / onrealistisch / bevroren | fail-safe na `grid_stale_after_s` |
| Bug in strategie | exception → fail-safe |
| Regelcyclus hangt | `Watchdog` (heartbeat) → vrijgeven; systemd `WatchdogSec` / Docker-healthcheck herstart de service |
| Raspberry Pi crasht | hardware-watchdog herstart de Pi; apparaten draaien op eigen regeling; drivers gebruiken waar mogelijk **time-outs in het apparaat zelf** (remote-control vervalt automatisch) |
| Internet/prijsdata weg | prijscache + schatting uit de afgelopen 7 dagen (gemarkeerd); geen plan → regelgebaseerde zelfconsumptie |
| Database weg | regelcyclus is onafhankelijk van de database; historie-schrijffouten worden gelogd |

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

## 7. GridMeter — de primaire netmeter

* Elke driver die als netmeter kan dienen, zet `manifest.grid_meter_kind`
  (`homewizard_p1`, `dsmr`, `modbus`, `rest`, `mqtt`, `simulated`).
* Selectie (`gridmeter.select_primary_grid_meter`): **expliciet gekozen** (rol `primary_grid_meter`)
  → **HomeWizard P1** → **DSMR P1** → Modbus/REST/MQTT-meters worden alleen **aangeboden** (bevestiging door
  de installateur) → **geen primaire netmeter**.
* Zonder primaire netmeter: melding *"Geen primaire netmeter ingesteld. Sommige EMS-functies zijn beperkt."*;
  `snap.features` schakelt zero-export, piekbegrenzing, fasebewaking en netgestuurde regeling uit.
  De fail-safe op "netmeting weg" geldt alleen als er een primaire meter is.
* Controllers en optimizer lezen alleen `snap.grid` / `snap.grid_power_w` — **nooit merken**.

## 8. Inbedrijfstelling (commissioning)

Per apparaat een `control_level`, afgedwongen door de `CommandGate`:

| Niveau | Effect |
|---|---|
| CONNECTION TEST | wizard: `self_test()` met ✓/✗ per functie, niets wordt gestuurd |
| READ ONLY | apparaat wordt gelezen; opdrachten → `NOT_COMMISSIONED` |
| SHADOW | optimizer/controllers draaien volledig; opdrachten → journaal "EMS zou …" (`SHADOW`) |
| LIMITED | opdrachten geschaald naar `limited_fraction` (bijv. 30 % van het laadvermogen, PV nooit meer dan die fractie afregelen) |
| FULL | volledige regeling |

Nieuwe echte apparaten starten altijd op READ ONLY. Drivers zonder `write_capable` kunnen niet hoger dan SHADOW.

## 9. Optimizer

* Rolling horizon: elke 5 min (en bij triggers: nieuwe prijzen, override, grote afwijking) een plan voor
  36 h in stappen van 15 min (`optimizer/model.py`, HiGHS).
* Doelfunctie: Σ (importprijs·import − exportprijs·export) + slijtage·doorvoer + comfortstraf (slack)
  − eindwaarde van opgeslagen energie; plus kleine tie-breakers ("eerder handelen", EV liever vroeg).
* Beperkingen: energiebalans per stap; SOC-dynamiek met rendementen; min/max/reserve-SOC uit het
  batterijprofiel (Battery Saver/Balanced/Profit/Aggressive); max. cycli per dag; netlimiet (aansluiting),
  exportlimiet (zero/smart/onbeperkt), piekgrens; PV-curtailment; lineair RC-model van de woning met
  comfortgrenzen; EV-vertrektijd/doel met minimumstroom.
* Binaire variabelen alleen waar nodig; netladen lineair begrensd (zie ADR 8 in DEVELOPMENT.md).
* Uitvoering: `OptimizingController` vertaalt het plan van het huidige kwartier naar apparaatopdrachten,
  vergeleken met wat het apparaat in AUTO zelf zou doen (alleen ingrijpen als het plan echt afwijkt);
  `ZeroExportRegulator` corrigeert in een gesloten lus; geen plan → `SelfConsumptionController`.
* Uitlegbaarheid: per stap baseline-kosten ("zonder EMS") vs. plan → verwacht voordeel; elke beslissing
  heeft redenen in het journaal.
* Dezelfde optimizer + simulator vormen de backtester; Auto-Tune vergelijkt parametervarianten en past
  alleen toe na expliciete keuze (APPLY).

## 10. Data & historie

* Ruwe samples (site + per apparaat) elke regelcyclus → 14 dagen; 15-minuten-aggregaten onbeperkt.
* Beslissingen 365 dagen, meldingen 180 dagen, plannen 30 dagen; prijzen en prognoses gecachet.
* Configuratieversies (wie, wanneer, waarom) in de database; YAML op schijf bevat geen geheimen.
* Alles gesleuteld op `site_id` → multi-site zonder schemawijziging.

## 11. Beveiliging

* Alleen LAN; geen poorten naar internet. Toegang op afstand via VPN (WireGuard/Tailscale).
* Optioneel HTTPS (`ems serve --tls-cert --tls-key`, of een reverse proxy).
* Lokale gebruikers met scrypt-wachtwoordhashes; JWT-access-tokens (12 h) in de `Authorization`-header
  (geen cookies → geen CSRF); API-tokens `ems_…` alleen gehasht opgeslagen; login-rate-limit.
* Rollen: `viewer` < `operator` (overrides) < `admin` (instellingen, back-up) < `installer` (apparaten).
* Geheimen (JWT-sleutel, apparaattokens, in de wizard ingevulde wachtwoorden) versleuteld (Fernet) in
  `secrets.enc` met sleutel `secret.key` (0600); in de configuratie alleen `${VAR}` (`.env`) of
  `secret:<naam>`.
* Strikte CSP (`script-src 'self'`), `nosniff`, `no-referrer`; CORS alleen voor de Windows-app-origin.
* HomeWizard: HTTPS met de officiële HomeWizard-CA en hostnaamcontrole `appliance/p1dongle/<serienummer>`.

## 12. Deployment & updates

* Docker-image (python:3.12-slim, niet-root uid 1000, groep `dialout` voor P1-kabels), amd64 + arm64.
* `docker-compose.yml`: host-netwerk (nodig voor mDNS-discovery), volume `ems-data`, healthcheck,
  logrotatie, optioneel profiel `postgres`.
* `install.sh update`: back-up → image taggen als `previous` → bouwen/starten → healthcheck →
  automatische rollback. Databasemigraties draaien bij het starten.
* Zonder Docker: systemd-unit met `Type=notify` en `WatchdogSec`.
* Windows: PyInstaller-server (optioneel, voor test/Demo Mode) + Tauri-app in één Inno Setup-installer.

## 13. Multi-site

Elke configuratie heeft een `site.id`; snapshots, journaal en database-records dragen die id.
Een tweede locatie = een tweede `EMSConfig` + runtime-instantie onder dezelfde API (nog niet gebouwd).
