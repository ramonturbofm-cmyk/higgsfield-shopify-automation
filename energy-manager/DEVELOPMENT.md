# DEVELOPMENT — voortgang en besluiten

Laatst bijgewerkt: fase 1 afgerond.

## Fase-overzicht

| Fase | Onderwerp | Status |
|---|---|---|
| 1 | Projectarchitectuur + simulator | ✅ klaar |
| 2 | Pi-backend: database, REST/WebSocket-API, auth, Docker, systemd-watchdog | ⏭ volgende |
| 3 | Windows-dashboard (Tauri) + webinterface | gepland |
| 4 | P1/DSMR-driver + live energiestromen | gepland |
| 5 | Prijsproviders + tariefengine | gepland |
| 6 | Batterijoptimizer (rolling-horizon MILP) | gepland |
| 7 | PV / zero-export / smart export | gepland |
| 8 | Warmtepompregeling (thermische buffer) | gepland |
| 9 | Forecasting | gepland |
| 10 | Automatiseringen (visuele editor) | gepland |
| 11 | Backtesting + Auto-Tune | gepland |
| 12 | Verdere apparaten (EV-planning, V2H, boiler, ...) | gepland |

## Fase 1 — wat is klaar

* Technisch ontwerp en keuzes: [ARCHITECTURE.md](ARCHITECTURE.md).
* `core/`: domeinmodellen en tekenconventies, `Clock` (systeem/gesimuleerd), getypeerde config met
  validatie + `${ENV}`-interpolatie + `SIMULATION_MODE`/`DRY_RUN`-overrides + UI-metadata,
  `SiteSnapshot`-aggregatie, sensorvalidatie, bevroren-data-detectie, `EMSEngine` met fail-safe,
  `Watchdog` + `sd_notify`, `EventBus`, `DecisionJournal` (JSONL + ringbuffer), JSON-logging.
* `devices/`: `DeviceDriver`-interface, `DriverManifest`, `DriverRegistry` (auto-discovery +
  entry points), `DeviceManager` (timeouts, reconnect met back-off, validatie), `self_test()`-rapport.
* `control/`: `NativeController` ("zonder EMS"), `SelfConsumptionController` (batterij zelfconsumptie,
  EV `pv_only`/`min_pv`/`max`/`off`, fasebewaking + importlimiet), `OverrideManager`, `CommandGate`.
* `integrations/mock/`: 5 mockdrivers met storingsinjectie.
* `simulator/`: omgeving (zon, bewolking, temperatuur, 15-min-prijzen; deterministisch per seed),
  componenten (PV, basislast, batterij, warmtepomp + RC-woningmodel, laadpaal + auto), site met
  fasemodel en meter, runner met KPI's en geplande gebeurtenissen, CLI `ems-sim`.
* 62 tests: config, validatie, natuurkunde (energiebalans, rendementen, comfort, min. looptijd),
  drivers/registry, controller-logica, engine/fail-safe/storingen, end-to-end-simulaties, CLI.

## Tussenstap — Windows-installer (simulatieversie)

* `ems/report.py`: zelfstandig HTML-rapport (grafieken met hover, KPI's, vergelijking, beslissingen;
  light/dark, gevalideerd kleurenpalet).
* `ems/desktop/app.py`: lokale webapp op 127.0.0.1 (token + Origin-controle tegen cross-site verzoeken),
  `--selftest` voor de buildpipeline.
* `windows/`: PyInstaller-spec, Inno Setup-script (per-gebruiker, Nederlands), `build.ps1`.
* GitHub Actions bouwt en test op `windows-latest` en levert installer + draagbare exe als artifact.
* Niet ondertekend (SmartScreen-waarschuwing); code signing later.

## Architectuurbesluiten (ADR-log)

1. **Python/asyncio-kern, Pydantic-config** — zie ARCHITECTURE §2.
2. **Apparaten houden hun eigen regeling; EMS verschuift setpoints.** `release_control()` is verplicht
   voor elke driver en wordt gebruikt bij fail-safe, afsluiten en verlopen overrides.
3. **Eén pad naar hardware (`CommandGate`).** Simulatie-/dry-run-bescherming en deduplicatie zitten op
   één plek; controllers kunnen hardware niet direct aanspreken.
4. **Bevroren-data via vingerafdruk** (vermogen + spanningen + meterstanden) i.p.v. alleen netvermogen.
   Aanleiding: in de simulatie hield de batterij het netvermogen urenlang op exact 0 W, wat met
   detectie op één waarde ten onrechte fail-safe (90×/dag) veroorzaakte.
5. **Keep-alive-verversingen zijn geen beslissingen** (`Outcome.REFRESHED`): ze worden niet gejournaliseerd
   (anders ~600 ruisregels per dag).
6. **Simulator binnen het `ems`-pakket** zodat drivers, backtester en optimizer hem kunnen hergebruiken.
7. **Deterministische, random-access omgevingssignalen** (seeded value noise) zodat prognoses later in
   "de toekomst" van dezelfde wereld kunnen kijken en tests reproduceerbaar zijn.
8. **Optimizer: MILP met HiGHS**; **database: TimescaleDB** (SQLite-profiel); **frontend: React/TS
   in Tauri + PWA** — onderbouwing in ARCHITECTURE §2.
9. **Gebruikersteksten in het Nederlands, code/identifiers in het Engels**; tijden intern UTC,
   weergave in de tijdzone van de locatie.
10. **Tijdelijke Windows-launcher = lokale webpagina** i.p.v. tkinter: dezelfde richting als de
   uiteindelijke webfrontend, volledig te testen zonder Windows, en geen extra GUI-toolkit in de bundel.

## Bekende problemen / beperkingen

* De batterij laadt in eigen zelfconsumptiemodus tot 100 %; `battery.max_soc` wordt pas afgedwongen
  door de batterijoptimizer (fase 6).
* Een 3-fase auto heeft ≥ 4,1 kW nodig; bij kleiner PV-overschot exporteert `pv_only`. Oplossingen:
  `min_pv`-modus (aanwezig), 1/3-fase-omschakeling (fase 12, hardware-afhankelijk).
* Plotselinge pieken (waterkoker) kunnen tot de volgende regelcyclus (10 s) een fase overbelasten
  (simulatie 3×16 A: 410 s/dag vs. 8,1 uur zonder EMS). Zekeringen tolereren dit; een snellere lokale lus
  op push-meterdata (P1 elke seconde) volgt in fase 4/7.
* De watchdog draait in dezelfde event loop; een volledig vastgelopen proces wordt pas opgevangen door
  systemd `WatchdogSec` + hardware-watchdog (fase 2).
* Geen doel-SOC/vertrektijd voor de auto; vergelijkingen met "zonder EMS" zijn daardoor ongelijk als de
  auto minder geladen wordt (de CLI meldt dit).
* "Spotwaarde" in simulaties is indicatief (geen opslagen, belasting, btw) tot de tariefengine (fase 5).
* Simulator nog zonder: ontdooicycli, tapwater/boiler, PV-temperatuurverlies, batterijveroudering,
  meetruis op apparaatsensoren.
* Het beslissingslogboek staat in een in-memory ringbuffer (+ JSONL als een map is opgegeven);
  databaseopslag volgt in fase 2.

## Wat nog moet (hoofdlijnen)

* Fase 2: `database/` (SQLAlchemy 2 async, Alembic, Timescale-hypertables + aggregaten, SQLite-profiel),
  `api/` (FastAPI: `/api/v1/sites/{site}/...` voor live, devices, config, overrides, journal, history;
  WebSocket `/ws` voor live/events; OpenAPI), auth (gebruikers, rollen, JWT, API-tokens), credential-
  versleuteling, `ems serve`-entrypoint (engine + watchdog + API in één proces), Dockerfile(s),
  docker-compose, systemd-unit met `WatchdogSec`, back-up/restore van config.
* Fase 3+: zie het fase-overzicht.

## Volgende stap

**Fase 2 starten**: persistente opslag van metingen/beslissingen, de REST/WebSocket-API met
authenticatie, en deployment op de Pi via Docker Compose — zodat fase 3 (dashboard) er direct
tegenaan kan bouwen. Hardwaregegevens zijn hiervoor nog niet nodig.
