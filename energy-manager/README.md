# Energy Manager

Een universeel, uitbreidbaar **Energy Management System (EMS)** voor woningen en kleine bedrijven.
De EMS-engine draait 24/7 zelfstandig op een Raspberry Pi 4/5 (of andere Linux-computer);
een Windows-app en een webinterface dienen als bediening.

> **Status: fase 1 van 12 — architectuur + simulator.** De kern, het apparaatpluginsysteem,
> de mockapparaten, het fysieke woningmodel, de fail-safe-mechanismen en een eerste
> regelstrategie werken en zijn getest. Er is nog geen API, database, app of optimizer.
> Zie [DEVELOPMENT.md](DEVELOPMENT.md).

## Wat werkt nu

* **Simulator** van een complete woning: zon/bewolking/buitentemperatuur, PV (meerdere velden,
  oriëntatie), huishoudelijk verbruik met pieken, thuisbatterij met rendementen, warmtepomp met
  thermisch woningmodel en eigen thermostaat, laadpaal met aankomst/vertrek, slimme meter met
  fasewaarden, synthetische 15-minuten day-ahead-prijzen.
* **EMS-kern**: regelcyclus, samengevoegde `SiteSnapshot`, sensorvalidatie, detectie van bevroren
  data, fail-safe met automatisch herstel, watchdog, handmatige overrides die verlopen,
  `SIMULATION_MODE` en `DRY_RUN`, uitlegbaar beslissingslogboek (JSONL).
* **Plugin-/driverarchitectuur** met registry, capabilities en een verbindingstest-rapport
  (✓/✗ per functie) voor de apparaatwizard.
* **Mockdrivers**: `MockSmartMeter`, `MockSolarInverter`, `MockBattery`, `MockHeatPump`,
  `MockEVCharger` — inclusief storingsinjectie (offline, bevroren, onzinwaarden, traag, commando's weigeren).
* **Strategie "zelfconsumptie"**: batterij in eigen zelfconsumptiemodus, laadpaal op PV-overschot
  (met smoothing, start/stop-vertraging en hysterese) en **dynamische fasebewaking**.
* **Configuratie** in YAML met validatie, `${GEHEIM}`-verwijzingen naar `.env` en UI-metadata
  (labels, uitleg, SIMPLE/ADVANCED/EXPERT) voor de toekomstige instellingenschermen.

## Snel starten

Vereist Python 3.11+.

```bash
cd energy-manager
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2 zomerdagen simuleren en vergelijken met "zonder EMS"
ems-sim --config config/ems.example.yaml --start 2026-06-15 --days 2 --compare-native

# Winter, met CSV/JSON-uitvoer
ems-sim --start 2026-01-20 --days 7 --out out/winter

# Proefdraaien: alles berekenen, niets uitvoeren
DRY_RUN=true ems-sim --days 1

# Tests
pytest
```

Voorbeelduitvoer (ingekort):

```
Simulatie (self_consumption) vanaf 2026-06-15T00:00:00+02:00, 48 uur
  PV-opwek                   83.86 kWh   (afgeregeld 0.00 kWh)
  Netafname                   0.74 kWh
  Teruglevering              39.12 kWh
  Batterij in / uit          13.76 / 7.41 kWh  (1.06 cycli)
  Zelfvoorzienendheid           98.1 %
  Max. fasestroom             10.6 A     (overbelast 0 s)
  Fail-safe gebeurtenissen       0

2026-06-16 14:22:30 EMS: Laadpaal: laadstroom 6 A [sent]
Reden:
  - PV-overschot 4.6 kW stabiel — laden gestart met 6 A
```

## Projectstructuur

```
energy-manager/
├── backend/ems/
│   ├── core/          modellen, config, engine, validatie, journaal, watchdog, events, klok
│   ├── devices/       driver-interface, plugin-registry, DeviceManager
│   ├── control/       controllers (strategieën), overrides, CommandGate
│   ├── integrations/  drivers; mock/ in fase 1 (vendor-drivers alleen met officiële documentatie)
│   ├── simulator/     fysiek woningmodel, omgeving, runner, CLI (python -m ems.simulator)
│   ├── api/           (fase 2) FastAPI + WebSocket
│   ├── database/      (fase 2) TimescaleDB/SQLite
│   ├── tariffs/       (fase 5) tariefengine
│   ├── optimizer/     (fase 6) rolling-horizon MILP
│   ├── forecasting/   (fase 9)
│   ├── automations/   (fase 10)
│   └── services/      (fase 2+) historie, export, back-up, notificaties, updates
├── frontend/          (fase 3) React/TS-webapp, ook gebruikt door de Tauri Windows-app
├── config/            ems.example.yaml
├── tests/             unit-, integratie-, simulator-, regel- en storingstests
├── docker/            (fase 2) Dockerfiles, compose
└── scripts/           hulpscripts
```

De simulator staat binnen het `ems`-pakket (i.p.v. een losse map) omdat mockdrivers, backtesting
en optimizer-validatie hem als bibliotheek gebruiken.

## Documentatie

* [ARCHITECTURE.md](ARCHITECTURE.md) — technisch ontwerp en keuzes
* [DEVELOPMENT.md](DEVELOPMENT.md) — voortgang, besluiten, bekende problemen, volgende stap
* [DEVICE_INTEGRATION_GUIDE.md](DEVICE_INTEGRATION_GUIDE.md) — een nieuw apparaat toevoegen

## Veiligheid

Het EMS verschuift alleen setpoints; elk apparaat behoudt zijn eigen veilige regeling.
Bij ontbrekende of onbetrouwbare netmeting, een fout in de strategie of een vastgelopen regelcyclus
geeft het EMS alle apparaten vrij. Laat de EMS-poorten nooit openstaan naar internet.
