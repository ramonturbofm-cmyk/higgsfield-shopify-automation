# DEVELOPMENT — voortgang, teststatus en besluiten

Laatst bijgewerkt: versie **0.3.0** — nodes (standalone en gedistribueerd), veiligheidsvalidatie op de
eigenaar-node, prioriteiten, settlement, datakwaliteit, Windows-acceptatietest.
Dit bestand is de waarheid over wat werkt.

## Legenda

| Teken | Betekenis |
|---|---|
| `[x]` | COMPLETE — klaar en getest |
| `[~]` | PARTIAL — werkt, met bekende beperking (zie toelichting) |
| `[ ]` | NOT STARTED |
| `[!]` | BLOCKED — externe factor (meestal: hardware/documentatie nodig) |

Teststatus: **UNIT TESTED** (pytest, 171 tests), **SIMULATOR TESTED** (Demo Mode / woningsimulator),
**HARDWARE TESTED** (met een echt apparaat). **Niets is hardware-getest**: er was geen hardware beschikbaar.
Windows-onderdelen zijn getest op een schone Windows-machine in GitHub Actions (`windows/acceptance.ps1`).

## Platformen en nodes

| Onderdeel | Status | Test |
|---|---|---|
| Windows alles-in-één (UI + EMS op de achtergrond + database + drivers) | `[x]` | Windows-acceptatietest in CI: installeren, starten, Demo, database, WebSocket, instellingen, app sluiten → EMS draait door, herstart via autostart, upgrade, verwijderen |
| Raspberry Pi / Linux standalone (Docker of systemd) | `[~]` | Docker amd64+arm64 gebouwd en healthcheck in CI; **niet op een fysieke Pi gedraaid** |
| Node-identiteit (UUID, platform, OS, versie, rollen, hartslag) | `[x]` | UNIT |
| Rollen: all_in_one / controller / gateway | `[x]` | UNIT, gedistribueerde acceptatietest |
| Ontdekken via mDNS (`_energymanager._tcp`), geen netwerkscans | `[~]` | Code getest op parsing; mDNS in CI/sandbox niet end-to-end (geen multicast) |
| Koppelen met 6-cijferige code (10 min, eenmalig, blokkade na 5 pogingen), tokens gehasht/versleuteld | `[x]` | UNIT, gedistribueerde test, browser (twee servers) |
| Regelrecht (lease + epoch-fencing): nooit twee controllers op één apparaat | `[x]` | UNIT + test met tweede controller en vervalste epoch |
| Apparaat-eigenaar-node (`node.remote`): P1 Pi → controller, opdracht controller → Pi | `[x]` | Gedistribueerde acceptatietest |
| Veiligheidscontrole op de eigenaar-node (online, verse data, capability, inbedrijfstelling, bereik, SOC, rate limit) | `[x]` | UNIT + gedistribueerd |
| Netwerkuitval: lease verloopt → apparaten terug naar eigen regeling; geen oude opdrachten (max. leeftijd) | `[x]` | Gedistribueerde test |
| Historie bufferen op de gateway en na herstel synchroniseren zonder dubbele records | `[x]` | Gedistribueerde test |
| Automatische failover van de primaire controller | `[ ]` | Bewust niet: handmatige overdracht (veiligheid boven snelle failover) |
| Migratie Windows → Pi | `[~]` | Via back-up/restore (instellingen, apparaten, tarieven, automatiseringen, historie); geen aparte wizard |

## Kern, regeling en veiligheid

| Onderdeel | Status | Test |
|---|---|---|
| EMS-kern: regelcyclus, snapshot, validatie, bevroren data, fail-safe, watchdog, journaal | `[x]` | UNIT, SIMULATOR |
| CommandGate + SafetyValidator vóór iedere schrijfactie | `[x]` | UNIT |
| Prioriteitenscheduler (veiligheid … standaard) met uitleg welke aanvraag won | `[x]` | UNIT |
| Netbeveiliging: begrenst EV-laden en batterijladen (ook handmatig) bij overbelasting | `[x]` | UNIT |
| Inbedrijfstelling CONNECTION TEST → READ ONLY → SHADOW → LIMITED → FULL (ook afgedwongen op de gateway) | `[x]` | UNIT, gedistribueerd |
| Schaduwmodus: optimizer draait echt, "EMS zou …" vs. werkelijk | `[x]` | UNIT, SIMULATOR |
| Handmatige bediening met verlooptijd (30 min … tot stoppen), "AUTO om …" | `[x]` | UNIT |
| Datakwaliteit per grootheid (GOOD/CALCULATED/STALE/INVALID/MISSING/UNKNOWN), N/A in de UI | `[x]` | UNIT, browser |
| Energiebalanscontrole met mogelijke oorzaken | `[x]` | UNIT |
| EMS-status (AUTOMATISCH/SCHADUW/HANDMATIG/BEPERKT/VEILIGE MODUS/STORING), gezondheidsscore | `[x]` | UNIT, browser |
| Configuratiecontrole (bijv. max. afname > aansluiting, zero-export zonder netmeter) | `[x]` | UNIT |
| Windows-slaapstandwaarschuwing (alleen lezen, `powercfg`) | `[~]` | UNIT (parser, NL/EN-uitvoer); op Windows-CI draait de check, waarde afhankelijk van de runner |
| Audit: elke hardwareactie in het journaal (wie/wat/waarom/resultaat, run-id, bron incl. node) | `[x]` | UNIT |

## Prijzen, tarieven, settlement, optimizer

| Onderdeel | Status | Test |
|---|---|---|
| Prijsbronnen: EnergyZero (NL, kwartier/uur, geen token), ENTSO-E (token), handmatig, demo; cache + schatting | `[x]` | UNIT; EnergyZero live gecontroleerd in CI |
| Kwartierprijzen, DST: 92/96/100 kwartieren, ontbrekende kwartieren gedetecteerd | `[x]` | UNIT |
| Drie prijzen: markt, werkelijke afname, werkelijke teruglevering (marginaal; vaste kosten niet marginaal) | `[x]` | UNIT, browser |
| Tariefengine dynamisch/vast/variabel, opslagen, afslagen, transactiekosten, btw | `[x]` | UNIT |
| Energiebelasting versieerbaar per land/jaar (NL 2023–2026; 2026 gemarkeerd "controleer") | `[~]` | UNIT — controleer het 2026-tarief op belastingdienst.nl |
| Settlement engine: NL 2026 (salderen) vs. 2027 (zonder), scenariovergelijking | `[~]` | UNIT — salderen benaderd met de gemiddelde afnameprijs |
| Leveranciersprofielen | `[ ]` | Handmatige contractinstelling volstaat; geen profielen van leveranciers opgenomen (geen bronnen geverifieerd) |
| Rolling-horizon MILP-optimizer (HiGHS), 36 h × 15 min, herplannen elke 5 min + events | `[x]` | UNIT, SIMULATOR, E2E 12:00 / 19:00 |
| Strategieën (laagste kosten, max. opbrengst, max. zelfconsumptie, gebalanceerd, eco, comfort, zero export, piekbegrenzing, batterij sparen, noodstroom, aangepast) + "Simuleer wijziging" | `[x]` | UNIT, browser |
| Batterij: SOC-grenzen, reserve, slijtage, cycli, PV-ruimte (optimizer houdt ruimte vrij bij veel PV) | `[x]` | UNIT, SIMULATOR |
| Warmtepomp (RC-model, comfort), EV (vertrektijd, doel, min. stroom), zero-export (gesloten lus) | `[x]` | UNIT, SIMULATOR |
| COP-afhankelijke warmtekosten, ontdooien, tapwater, legionella | `[~]` | COP-schatting in de optimizer; ontdooien/tapwater/legionella niet gemodelleerd |
| V2H/V2G | `[ ]` | Architectuur laat het toe; geen implementatie |
| Backtesting (1–365 dagen), Auto-Tune (IGNORE/TEST/APPLY), financieel met/zonder EMS | `[x]` | UNIT, SIMULATOR |

## Apparaten en protocollen

| Integratie | Status | Classificatie | Test |
|---|---|---|---|
| HomeWizard P1 (API v1 + v2, mDNS, knop-koppeling, token, HTTPS met HomeWizard-CA, WebSocket) | `[~]` | OFFICIAL (officiële API-docs) | UNIT — **niet HARDWARE TESTED** |
| DSMR P1 direct (USB/COM-poort of TCP-bridge), CRC | `[~]` | OFFICIAL (DSMR 5.0.2) | UNIT — **niet HARDWARE TESTED** |
| Generiek Modbus TCP / HTTP-JSON / MQTT (alleen lezen, mapping uit eigen documentatie) | `[~]` | GENERIC | UNIT (+ echte Mosquitto) — **niet HARDWARE TESTED** |
| Generiek Modbus RTU/RS485, WebSocket, serieel, Home Assistant-entiteit | `[ ]` | GENERIC | Nog niet gebouwd (Modbus TCP-client kan hergebruikt worden voor RTU) |
| OCPP, SG Ready | `[!]` | — | Vereist hardware/specificatie van de gebruiker |
| Schrijvende drivers voor echte omvormers/batterijen/warmtepompen/laadpalen | `[!]` | — | Merk, model, firmware, interface en officiële documentatie nodig |
| Mockdrivers (Demo Mode) | `[x]` | — | UNIT, SIMULATOR |

## Bediening, installatie, release

| Onderdeel | Status | Test |
|---|---|---|
| Webinterface (18 schermen): dashboard met "wat/waarom/tot wanneer/voordeel/grenzen", Mijn installatie, Nodes, planning met "Waarom?", financiën + salderen 2026/2027 | `[x]` | Browser (Playwright): alle pagina's zonder consolefouten, licht/donker/mobiel |
| SIMPLE / ADVANCED / EXPERT met uitleg per instelling | `[x]` | Browser |
| Windows-app: start-keuze (alles op deze computer / verbinden / node toevoegen), EMS starten/stoppen | `[x]` | Chromium-test van het startscherm met echte server; app-start op Windows in CI |
| Windows-installer (per gebruiker, autostart, upgrade stopt het EMS eerst, verwijderen behoudt data) | `[x]` | Windows-acceptatietest in CI |
| Windows-service die vóór aanmelden start | `[~]` | Autostart bij aanmelden (HKCU Run); echte service vereist beheerdersrechten — niet gebouwd |
| Code signing | `[!]` | Voorbereid in CI; zonder certificaat: **UNSIGNED DEVELOPMENT BUILD** |
| Raspberry Pi/Linux: `install.sh`, Docker (amd64/arm64), systemd | `[~]` | CI (Docker + healthcheck); niet op een fysieke Pi |
| Back-up/restore (incl. migratie naar andere node) | `[x]` | UNIT (Linux + Windows CI) |

## Openstaande punten / wat de gebruiker moet aanleveren

1. **Hardware voor schrijvende drivers** `[!]`: per apparaat merk, model, firmware, aansluiting en officiële
   documentatie.
2. **Hardwaretest** HomeWizard P1 / DSMR P1 op de echte installatie; daarna `verified=True` en deze tabel bijwerken.
3. **Code-signing-certificaat** (optioneel) als repository-secrets `WINDOWS_SIGNING_PFX_BASE64` +
   `WINDOWS_SIGNING_PASSWORD`.
4. Controle van het energiebelastingtarief 2026 (gemarkeerd in de app).

## Bekende beperkingen

* Zonder schrijvende driver kan het EMS niets aansturen; inbedrijfstelling blijft op READ ONLY / SHADOW.
* Windows: het EMS start bij aanmelden van de gebruiker, niet al bij het opstarten vóór aanmelden; de pc
  moet aan blijven en mag niet slapen (de app waarschuwt).
* mDNS-ontdekking kan door firewalls of gescheiden netwerken (gast-wifi, VLAN) geblokkeerd worden; koppelen
  met adres + code werkt altijd.
* Geen automatische controller-failover; bij uitval van de controller vallen apparaten terug op hun eigen
  regeling tot de controller terug is (of een andere controller het regelrecht krijgt na verlopen lease).
* MQTT-driver op een Windows-host niet getest.
* Simulator: geen ontdooicycli, tapwater of batterijveroudering in de fysica.
* HomeWizard-API: licentie voor persoonlijk, niet-commercieel gebruik.

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
18. **Nodes: de eigenaar van een apparaat is de autoriteit.** Elke node met apparaten verleent het
    regelrecht (lease met epoch als fencing-token) aan precies één controller en valideert iedere opdracht
    zelf (SafetyValidator). Zo kunnen twee controllers (split brain) nooit hetzelfde apparaat sturen en
    beschermt de gateway zich ook tegen een foutieve centrale optimizer. Failover is bewust handmatig:
    veiligheid gaat boven snelle overname.
19. **Apparaten op een andere node zijn gewone drivers (`node.remote`)**: EMS-kern, optimizer en UI
    hoeven niets van distributie te weten.
20. **Prioriteiten in één scheduler** (veiligheid > nood > netbeveiliging > inbedrijfstelling > handmatig >
    optimizer > automatisering > standaard); de netbeveiliging begrenst daarna wat er gewonnen heeft.
21. **Settlement los van de tariefengine**: de tariefengine bepaalt prijzen per kWh (marginaal), de
    settlement engine de jaarverrekening (salderen 2026 / geen salderen 2027).
22. **Windows "alles in één" = achtergrondproces + autostart bij aanmelden** (HKCU Run, geen
    beheerdersrechten) i.p.v. een Windows-service: werkt zonder admin en zonder wachtwoord op te slaan;
    nadeel: start pas na aanmelden (zie beperkingen).

## Testen

```bash
cd energy-manager
pip install -e ".[dev]"
ruff check backend tests
pytest -q                 # 171 tests, ~1,5 min
ems selftest              # snelle zelftest van een geïnstalleerde build
```

CI (`.github/workflows/energy-manager-ci.yml`): lint, tests (met Mosquitto), selftest, Docker
amd64+arm64 en container-healthcheck. Windows (`energy-manager-windows.yml`): tests op Windows,
PyInstaller-server + selftest, Tauri-app, Inno Setup-installer en de Windows-acceptatietest
(`windows/acceptance.ps1`); alleen bij succes wordt de installer gepubliceerd.

Releaseprocedure: [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
