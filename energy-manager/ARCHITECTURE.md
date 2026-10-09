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

## 14. Nodes: standalone en gedistribueerd

Iedere computer met Energy Manager is een **node** (Windows, Raspberry Pi of Linux), met een vaste
`node_id` (UUID in `node.json`), naam, platform, OS, versie, rollen en hartslag (`/api/v1/node/info`).

| Rolpreset | Rollen | Gebruik |
|---|---|---|
| `all_in_one` (standaard) | alle | Windows-only, Pi-only of Linux-only installatie |
| `controller` | alle | regelt ook apparaten van gekoppelde nodes |
| `gateway` | DEVICE_GATEWAY, USER_INTERFACE | bijv. Pi in de meterkast met P1/RS485; beslist zelf niets |

```
 Windows (UI)            Linux mini-pc (controller)                   Raspberry Pi (gateway)
 ┌──────────┐  HTTP/WS   ┌────────────────────────────┐  node-API   ┌─────────────────────────────┐
 │ app / web│──────────▶ │ optimizer → scheduler      │ ──────────▶ │ lease-check (epoch) → age   │
 └──────────┘            │ → grid guard → CommandGate │  token +    │ → inbedrijfstelling →       │
                         │   (inbedrijfstelling,      │  epoch      │   SafetyValidator → driver  │
                         │    safety, lease)          │ ◀────────── │ → P1 / Modbus / RS485       │
                         │ node.remote-drivers        │  metingen   │ buffer historie (SQLite)    │
                         └────────────────────────────┘             └─────────────────────────────┘
```

* **Eigenaar-node is de autoriteit.** Elke node met apparaten verleent een *lease* (TTL 30 s, verlengd met
  de hartslag elke 5 s) aan één controller; bij iedere wissel stijgt de *epoch*. Opdrachten dragen
  houder + epoch + uitgiftetijd; anders weigert de eigenaar-node (fencing tegen split brain, geen oude
  opdrachten na een netwerkstoring).
* **Lokale veiligheid blijft altijd actief**: de eigenaar-node controleert online/verse data, capability,
  inbedrijfstellingsniveau (ook daar opgeslagen), bereik, SOC-grenzen en rate limit — ook als de
  controller iets anders vraagt.
* **Netwerkuitval**: lease verloopt → de eigenaar-node geeft alle op afstand aangestuurde apparaten terug
  aan hun eigen regeling; de controller ziet de apparaten als offline en plant opnieuw. Na herstel haalt
  de controller de op de gateway gebufferde historie op (vervangt het gat in één transactie, geen dubbele
  records).
* **Koppelen**: 6-cijferige code op de nieuwe node (10 min, eenmalig, blokkade na 5 pogingen) → lang
  node-token; op de eigenaar-node alleen de SHA-256-hash, bij de controller versleuteld in de secret store.
* **Ontdekken**: mDNS/DNS-SD `_energymanager._tcp` (alleen adverteren en luisteren; geen netwerkscans).
* **Failover** is bewust niet automatisch: een tweede controller krijgt pas regelrecht als de lease van de
  eerste verlopen is. Veiligheid gaat boven snelle overname.

## 15. Commandoroute en prioriteiten

`optimizer / handmatig / automatisering` → **scheduler** (één winnaar per apparaatfunctie: 1 veiligheid,
2 nood, 3 netbeveiliging, 4 inbedrijfstelling, 5 handmatig, 6 optimizer, 7 automatisering, 8 standaard)
→ **GridGuard** (begrenst EV-laden en batterijladen als de aansluiting overbelast zou raken, ook bij
handmatige opdrachten) → **CommandGate** (inbedrijfstelling, SafetyValidator, regelrecht, dry-run/simulatie,
deduplicatie) → driver (lokaal of `node.remote` → eigenaar-node → zelfde controles) → hardware.
Elke uitkomst komt in het journaal met bron (optimizer, handmatig, automatisering, `node:<naam>`, safety).

## 16. Datakwaliteit, energiebalans, prijzen en settlement

* Iedere grootheid in de snapshot krijgt een kwaliteit (GOOD, CALCULATED, ESTIMATED, STALE, INVALID,
  MISSING, UNKNOWN). Netgestuurde functies (zero-export, piekbegrenzing, fasebewaking) draaien alleen op een
  GOOD primaire netmeting.
* Energiebalans: net + PV = batterij + warmtepomp + EV + huis; een duidelijk negatieve rest wordt gemeld
  met mogelijke oorzaken (tekenconventie, dubbele meting, verkeerde meter).
* Intervallen: MARKET (15 min), CONTRACT (15/60 min), OPTIMIZER (15 min, elke 5 min herberekend),
  CONTROL (10 s / apparaatafhankelijk). Lokale dagen hebben 92/96/100 kwartieren rond zomer-/wintertijd.
* Tariefengine = marginale prijzen per kWh (vaste kosten nooit marginaal); energiebelasting per land/jaar.
* Settlement engine = verrekening over een periode volgens landregels (NL 2026 salderen, 2027 niet).

## 17. Capabilities, toegang en uitvoering (0.4.0)

**Capabilities — één bron.** `devices/capabilities.py` beschrijft per apparaatsoort (`TYPE_SCHEMAS`) welke
functies bestaan, welke parameters (met eenheid, bereik, hulptekst, `required_for_control`) erbij horen en
of de fase relevant is. Wat een concreet apparaat kan is `driver.capabilities() ∩ typeschema`
(`device_capabilities()`); generieke drivers leiden hun capabilities alleen af uit de gevalideerde mapping.
UI-knoppen, automatiseringen, overrides, commissioning en controllers lezen allemaal deze ene bron.

**Toegang per actie.** `control/authority.py::can_execute(device, command, user, control_state)` controleert
rol, type- en drivercapability, waarde t.o.v. de eigen apparaatlimieten, control state en gate-modus, en
zegt of de opdracht echt wordt uitgevoerd of alleen als "EMS zou …" verschijnt. Daarna volgt de bestaande
route: OverrideManager → prioriteiten → netbeveiliging → CommandGate (inbedrijfstelling → beperkt →
SafetyValidator → schaduw → lease → dedupe → modus → driver).

**Control state** is één waarde per apparaat (READ_ONLY, SHADOW, LIMITED_CONTROL, FULL_CONTROL,
MANUAL_OVERRIDE, SAFE_MODE, OFFLINE, ERROR), berekend door de engine. De `ConfirmationTracker` volgt per
opdracht gewenst → gevalideerd → verzonden → bevestigd / onbevestigd (90 s) / geen terugmelding, op basis
van de volgende meting van het apparaat.

**Inbedrijfstelling** (`server/commissioning.py`): niveaus hebben harde voorwaarden. Beperkt: schrijvende
driver met documentatiebron, veiligheidsparameters van het type ingevuld, schaduwmodus waargenomen. Volledig:
daarnaast een geslaagde schrijftest (vrijgeven + teruglezen), een op hardware bewezen driver en de getypte
apparaatnaam. Een gateway-node controleert zijn eigen apparaten opnieuw (`node_level_problem`).

**Sessies.** Browsers krijgen een HttpOnly-cookie (SameSite=Strict, Secure op HTTPS) plus een leesbare
CSRF-cookie die bij elke wijziging als `X-CSRF-Token` mee moet; de sessie schuift mee (rotatie na 15 min) en
uitloggen trekt haar in. De WebSocket opent met een eenmalig ticket (30 s). Scripts gebruiken Bearer-tokens.

**Node-opdrachten** dragen een opdracht-ID (eenmalig), epoch, uitgiftetijd en TTL; de controller verstuurt
niets bij een klokverschil > 10 s.

## 18. Prijzen, afrekening, financiën en planningsuitleg (0.4.0)

* Markt per 15 min; het contract rekent per 15 of 60 min (`price_resolution_min`); de optimizerstap en de
  UI-aggregatie zijn daar los van. De "nu"-prijs is altijd het gepubliceerde interval met `start ≤ nu < einde`.
* Energiebelasting per jaar met schijven (`tariffs/taxes.py`); afrekening per kalenderjaar met de regels van
  dat jaar en een contractafhankelijke saldeermethode (`services/settlement.py`). Uitkomsten zijn
  `estimate` (factuurschatting) of `scenario` (vergelijking), nooit "factuur".
* Financiën: baseline-ladder B0 (geen PV/batterij) → B1 (PV) → B2 (batterij op eigen regeling, gesimuleerd)
  → B3 (werkelijk). Posten zijn verschillen tussen opeenvolgende baselines en tellen dus exact op.
* Planningsuitleg komt uit de optimizer (`optimizer/explain.py`): per slot redencodes met de gebruikte
  getallen en actieve grenzen, gekoppeld aan de run-id; vensters op tijdstempels; uur-aggregatie met sommen
  en tijdgewogen gemiddelden; de warmtepompactie is een expliciet planveld dat ook de controller gebruikt.

## 19. Versies en upgrades (0.4.0)

`ems.__version__` is de enige bron; `tools/set_version.py` schrijft hem naar pyproject, app en installer
(`--check` in de tests). De installer weigert een oudere versie over een nieuwere; de database weigert te
starten met een nieuwer schema dan de software kent.

## 20. Marktgegevens, energiecontract en prijsprognoses (0.5.0)

Drie gescheiden lagen:

| Laag | Sectie | Inhoud |
|---|---|---|
| Marktgegevens | `prices` (`PriceConfig`) | bron, marktgebied, marktinterval, verbinding, reservebron, publicatiecontrole |
| Energiecontract | `tariff` (`TariffConfig`) | leverancier, soort contract, contractinterval, opslagen, terugleververgoeding/-kosten, energiebelasting, btw, transactiekosten, overige voorwaarden |
| Prognoses | `forecast` (`ForecastConfig`) | prijsprognose aan/uit, horizon, model, betrouwbaarheidsdrempel, gebruik door optimizer en batterijhandel, gedrag bij ontbrekende prijzen |

**Bronafhankelijke instellingen.** Elk bronspecifiek veld draagt in het schema `providers=[...]`;
`prices/settings.py::visible_fields` bepaalt welke velden gelden voor de gekozen bron en (alleen als
"Reserveprijsbron gebruiken" aan staat) de reservebron. De UI vraagt dit op via `POST /prices/fields`; bij
EnergyZero verschijnt dus geen ENTSO-E-token. Tokens/headers staan versleuteld in de secret store
(`SECRET_FIELDS`), nooit in de YAML, en komen gemaskeerd terug.

**EnergyZero.** Alleen het officiële endpoint `https://public.api.energyzero.nl/v1/prices` met `date`,
`interval` (`INTERVAL_QUARTER`/`INTERVAL_HOUR`) en `energy_type=ENERGY_TYPE_ELECTRICITY`; geen token. Een
ander adres vereist de expertinstelling "Aangepaste API-adressen toestaan".

**Uitgaande verzoeken (SSRF).** `validate_endpoint`: alleen `https`, geen gebruikersnaam/wachtwoord in de
URL, geen `.local/.lan/.internal/localhost`, en de host moet uitsluitend naar publieke adressen resolven
(geen loopback, privé, link-local incl. 169.254.169.254, CGNAT, ULA, multicast, gereserveerd). Redirects
worden nooit gevolgd; antwoorden > 5 MB worden geweigerd; retries (1/3/9 s) alleen bij netwerkfouten, 429
en 5xx. Een Authorization-header gaat alleen naar het ingestelde adres.

**Controle.** `validate_points` verwerpt niet-eindige of onwaarschijnlijke waarden (buiten −2..10 €/kWh),
niet-uitgelijnde intervallen en tegenstrijdige dubbele intervallen. Dekking per lokale dag (92/96/100
kwartieren) bepaalt of "morgen beschikbaar" is.

**Statussen** (`PriceStatus`): `OFFICIAL_DAY_AHEAD` (gepubliceerd en gecontroleerd), `ESTIMATED` (één gat
≤ 2 uur binnen gepubliceerde prijzen, lineair geïnterpoleerd), `FORECAST` (na de laatste beursprijs, binnen
de horizon, met betrouwbaarheid 0–1 en p10–p90-band), `STALE` (gepubliceerde prijs voor de toekomst terwijl
de bron > 26 uur faalt), `MISSING`. Een prognose wordt nooit als beursprijs getoond; de grafiek tekent
gepubliceerd doorgetrokken, prognose gestippeld met band, verouderd/ontbrekend grijs.

**Publicatie.** Na `publication_expected` (13:00) controleert de prijsloop elke `publication_retry_minutes`
tot morgen volledig is; na `publication_alert_after` (15:30) status `delayed` en een melding. De laatste
geslaagde synchronisatie per bron staat in de database (`kv: prices.sync`) en overleeft een herstart.

**Reservebron.** Alleen gevraagd als de hoofdbron faalt, vandaag onvolledig is of morgen na de verwachte
publicatie nog ontbreekt; vult alleen intervallen die de hoofdbron niet leverde; de bron staat per interval
in de database.

**Optimizer.** `OptimizerService.price_usable`: gepubliceerd (ook `STALE`) altijd; `ESTIMATED` alleen bij
"Schatting gebruiken"; `FORECAST` alleen als "Optimizer gebruikt prognoses" aan staat én de betrouwbaarheid ≥
de drempel. De planning stopt bij het eerste onbruikbare kwartier (`inputs_summary.price_stop`). Op niet-
gepubliceerde prijzen plant de optimizer geen laden uit het net (`grid_charge_allowed`), tenzij
"Batterijhandel op prognoses" bewust aan staat. Bij een contract met vaste prijs zijn marktprijzen niet nodig.
