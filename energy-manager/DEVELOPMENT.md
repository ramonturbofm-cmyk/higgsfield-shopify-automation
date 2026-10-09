# DEVELOPMENT — voortgang, teststatus en besluiten

Laatst bijgewerkt: versie **0.5.0** — prijzen/prognoses en Energy Manager Cloud (0.4.0: audit-remediatie, zie [AUDIT_REMEDIATION.md](AUDIT_REMEDIATION.md)).
Dit bestand is de waarheid over wat werkt.

## Legenda

Implementatie: **COMPLETE** · **PARTIALLY IMPLEMENTED** · **UI ONLY** · **MOCK ONLY** (alleen gesimuleerd) ·
**BROKEN** · **NOT IMPLEMENTED** · **BLOCKED BY HARDWARE** (alleen met echte apparatuur te bouwen of te bewijzen).

Test: **TESTED** (geautomatiseerd: unit/API/simulator/browser/CI) of **NOT TESTED**. Waar relevant staat erbij
*hoe*. **Niets is HARDWARE TESTED**: er was geen hardware beschikbaar. Windows-onderdelen zijn getest op een
schone Windows-runner (`windows/acceptance.ps1`), Linux op een schone Ubuntu-runner
(`deploy/acceptance-linux.sh`: Docker amd64, Docker arm64 onder QEMU, systemd).

Tests: 340 pytest-tests EMS (Linux en Windows), 26 cloudtests (SQLite en PostgreSQL 16), 5
browser-acceptatiechecks (`tests/ui/ui_acceptance.py`), Windows- en Linux-acceptatiescripts in CI.

## Platformen en nodes

| Onderdeel | Implementatie | Test |
|---|---|---|
| Windows alles-in-één (app + EMS op de achtergrond, autostart bij aanmelden) | COMPLETE | TESTED — Windows-acceptatie in CI |
| Windows: downgradebescherming installer | COMPLETE | TESTED — acceptatiestap 9b |
| Windows-service vóór aanmelden | NOT IMPLEMENTED | — (autostart bij aanmelden) |
| Code signing | BLOCKED (geen certificaat) | — UNSIGNED TEST BUILD |
| Linux standalone (Docker, systemd) | COMPLETE | TESTED — CI (start, herstart, crash, upgrade, persistentie) |
| Raspberry Pi standalone | COMPLETE (arm64-image) | TESTED onder QEMU; fysieke Pi: NOT TESTED (BLOCKED BY HARDWARE) |
| Node-identiteit, rollen, mDNS, koppelcode | COMPLETE | TESTED (mDNS niet end-to-end: geen multicast in CI) |
| Regelrecht (lease + epoch), opdracht-ID's tegen replay, klokverschilcontrole | COMPLETE | TESTED |
| `node.remote`, gateway valideert zelf, historie-synchronisatie na uitval | COMPLETE | TESTED (in-process, niet op meerdere machines) |
| Automatische controller-failover | NOT IMPLEMENTED | — bewust (veiligheid) |
| Multi-site | NOT IMPLEMENTED | — |

## Veiligheid en regeling

| Onderdeel | Implementatie | Test |
|---|---|---|
| Typegebonden capabilityschema's (één bron) | COMPLETE | TESTED |
| Centrale `can_execute` (rol, capability, bereik, control state, gate-modus) | COMPLETE | TESTED |
| Control state (READ_ONLY … ERROR), "EMS zou" vs "EMS doet", uitvoeringsbevestiging | COMPLETE | TESTED (bevestiging alleen bij terugmelding van het apparaat) |
| CommandGate + SafetyValidator, testmatrix commandotype × storing | COMPLETE | TESTED (75 matrix-tests) |
| Fail-safe op verouderde netmeting, alleen vrijgave-opdrachten | COMPLETE | TESTED (simulatie); hardware-in-the-loop: BLOCKED BY HARDWARE |
| Inbedrijfstelling per apparaat met schrijftest en getypte bevestiging | COMPLETE | TESTED |
| Volledige regeling op echte hardware | BLOCKED BY HARDWARE | vereist een op hardware bewezen schrijvende driver |
| Automatiseringen met apparaatgebonden acties | COMPLETE | TESTED |
| Primaire netmeter alleen expliciet vervangen | COMPLETE | TESTED |
| Prioriteitenscheduler, netbeveiliging, overrides met verlooptijd | COMPLETE | TESTED |
| Datakwaliteit, energiebalans, EMS-status | COMPLETE | TESTED |

## Beveiliging

| Onderdeel | Implementatie | Test |
|---|---|---|
| HttpOnly-sessiecookie, CSRF, rotatie, uitloggen, WS-ticket | COMPLETE | TESTED |
| API-tokens (Bearer) voor scripts | COMPLETE | TESTED |
| Back-up zonder sleutels standaard; met sleutels versleuteld + wachtwoord | COMPLETE | TESTED |
| Geheimen versleuteld (Fernet), nooit in YAML | COMPLETE | TESTED |
| HTTPS in de server zelf | NOT IMPLEMENTED | — reverse proxy (INSTALL_LINUX.md) |

## Prijzen, tarieven, financiën, optimizer

| Onderdeel | Implementatie | Test |
|---|---|---|
| Prijsbronnen EnergyZero (officieel endpoint), ENTSO-E, eigen API (expert), handmatig, demo | COMPLETE | TESTED (EnergyZero live-schemacheck in CI) |
| Instellingen per bron, reservebron, SSRF-bescherming, verbindingstest | COMPLETE | TESTED |
| Prijscontrole, publicatiebewaking 13:00/15:30, statussen beursprijs/prognose/geschat/verouderd/ontbreekt | COMPLETE | TESTED |
| Prijsprognose (same_slot_7d, weekday_profile_4w) met band en betrouwbaarheid | COMPLETE | TESTED — statistisch, niet gekalibreerd |
| "Nu"-prijs uit lopend interval, status per interval | COMPLETE | TESTED |
| Contractprijs per kwartier of uur, los van marktresolutie | COMPLETE | TESTED |
| Energiebelasting per jaar met schijven (NL 2026 € 0,09161) | COMPLETE | TESTED — hogere schijven 2026 "nog controleren" |
| Afrekening contractafhankelijk, per jaar, negatieve teruglevering | COMPLETE | TESTED — schatting, geen factuur |
| Financiële baselines B0–B3, reconciliatie, datadekking | COMPLETE | TESTED |
| Backtest eigen periode, DST, reproduceerbaar, Auto-Tune | COMPLETE | TESTED |
| Optimizer (MILP), redencodes per run, WP-actie, horizon, aggregatie | COMPLETE | TESTED (5/15/60 min) |
| Break-even incl. verlies en slijtage | COMPLETE | TESTED |
| Leveranciersprofielen | NOT IMPLEMENTED | — contract handmatig |
| WP ontdooien/tapwater/legionella, V2H/V2G | NOT IMPLEMENTED | — |

## Energy Manager Cloud (0.5.0)

| Onderdeel | Implementatie | Test |
|---|---|---|
| Accounts (registratie, verificatie, wachtwoord/e-maillink, reset, MFA, sessies, export, verwijderen) | COMPLETE | TESTED |
| Organisaties, rollen, rechten, tenant-isolatie, auditlog | COMPLETE | TESTED |
| Koppelen lokaal ↔ cloud, node-token, rotatie, intrekken | COMPLETE | TESTED (in-process, echte EMS-runtime) |
| Opdrachten op afstand via lokale veiligheidscontrole | COMPLETE | TESTED (Demo Mode) — niet met hardware |
| Licenties, abonnementen, limieten, prijzen als data | COMPLETE | TESTED |
| Betaalprovider (Mollie/Stripe) | NOT IMPLEMENTED | — interface aanwezig, handmatig verlengen |
| Klantportaal en beheerportaal | COMPLETE | TESTED (browser, lokaal) |
| Deployment (Docker, compose met Caddy + PostgreSQL) | COMPLETE | PARTIALLY — image gebouwd/gestart tegen PostgreSQL; compose niet als geheel gestart; geen echte hosting |
| Centrale prijsdienst | NOT IMPLEMENTED | — alleen ontwerp (ARCHITECTURE §21) |

## Apparaten en protocollen

| Integratie | Implementatie | Test |
|---|---|---|
| HomeWizard P1 (API v1/v2, mDNS, koppelen, TLS met eigen CA, WebSocket) | COMPLETE (alleen lezen) | TESTED tegen nep-apparaat; HARDWARE: NOT TESTED |
| DSMR P1 (USB/TCP, CRC) | COMPLETE (alleen lezen) | TESTED met telegrammen; HARDWARE: NOT TESTED |
| Generiek Modbus TCP / HTTP-JSON / MQTT (capabilities alleen uit mapping) | COMPLETE (alleen lezen) | TESTED (+ echte Mosquitto); HARDWARE: NOT TESTED |
| Modbus RTU/RS485, OCPP, SG Ready | NOT IMPLEMENTED | — |
| Schrijvende drivers voor echte apparaten | BLOCKED BY HARDWARE | merk, model, firmware en officiële documentatie nodig |
| Demo-apparaten | MOCK ONLY | TESTED |

## Interface

| Onderdeel | Implementatie | Test |
|---|---|---|
| Wizard merk/model, "staat er niet tussen", schema-velden met bereiken, bewerken | COMPLETE | TESTED (API + browser) |
| Niveaus Eenvoudig/Uitgebreid/Expert app-breed, dynamisch menu | PARTIALLY IMPLEMENTED | TESTED — niet alle teksten per niveau herschreven |
| Dashboard dynamisch, tijdigheidslabels, "wat doet het EMS" | COMPLETE | TESTED (browser) |
| Grafieken met tijdemmers en min/max | COMPLETE | TESTED |
| Time-outs, offline-indicator | COMPLETE | TESTED (browser, trage en offline API) |
| Toegankelijkheid (toetsenbord, focus, reduced motion, tabellen, resoluties, touch) | PARTIALLY IMPLEMENTED | TESTED — geen volledige WCAG-contrastaudit |

## Openstaande punten / wat de gebruiker moet aanleveren

1. **Hardware voor schrijvende drivers**: per apparaat merk, model, firmware, aansluiting en officiële
   documentatie. Daarna hardwaretest en `verified=True`.
2. **Hardwaretest** HomeWizard P1 / DSMR P1 en een fysieke Raspberry Pi.
3. **Code-signing-certificaat** (optioneel) als repository-secrets.
4. Controle van de hogere energiebelastingschijven 2026.
5. **Cloud live zetten**: domein, hosting (EU), SMTP-dienst, later een betaalprovider, juridische documenten
   (zie `cloud/DEPLOYMENT.md` en `PRIVACY_COMPLIANCE.md`). Geheimen genereert de beheerder op de server.

Volledige lijst: [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

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

23. **Capabilities typegebonden, één bron** (`devices/capabilities.py`): wat een apparaat kan = driver ∩
    typeschema; UI-knoppen, automatiseringen, overrides en commissioning lezen allemaal dezelfde bron.
24. **`can_execute` als enige poort voor menselijke en geautomatiseerde opdrachten** vóór de CommandGate;
    de gate blijft de laatste controle (inbedrijfstelling, veiligheid, lease).
25. **Sessies via HttpOnly-cookie + CSRF** i.p.v. tokens in de browser; scripts houden Bearer-tokens.
26. **Financiën als baseline-ladder** (B0–B3): elke post is een verschil tussen twee baselines, dus nooit
    dubbel geteld.
27. **Redenen in de optimizer, niet in de browser**: elke planregel draagt redencodes uit dezelfde run.
28. **Volledige regeling alleen voor op hardware bewezen drivers**: zolang hardware-in-the-loop ontbreekt
    blijft FULL voor echte apparaten geblokkeerd.

## Testen

```bash
cd energy-manager
pip install -e ".[dev]"
ruff check backend tests
pytest -q                 # 306 tests, ~2 min
ems selftest              # snelle zelftest van een geïnstalleerde build
```

CI (`.github/workflows/energy-manager-ci.yml`): lint, tests (met Mosquitto), selftest, Docker
amd64+arm64, Linux-acceptatie (Docker amd64, Docker arm64 onder QEMU, systemd). Windows (`energy-manager-windows.yml`): tests op Windows,
PyInstaller-server + selftest, Tauri-app, Inno Setup-installer en de Windows-acceptatietest
(`windows/acceptance.ps1`); alleen bij succes wordt de installer gepubliceerd.

Releaseprocedure: [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

Browser-acceptatie (lokaal, tegen een Demo-server): `python tests/ui/ui_acceptance.py http://127.0.0.1:8795 <map>`.
