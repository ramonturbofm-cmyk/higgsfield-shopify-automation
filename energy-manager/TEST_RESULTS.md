# Testresultaten — Energy Manager 0.6.0

Alles hieronder is echt uitgevoerd. **Niets is met echte hardware getest.** De Windows-installatietest is
uitgevoerd op een schone Windows-runner in CI (GitHub Actions `windows-latest`), **niet** op uw eigen pc.

## Samenvatting

| Suite | Waar | Resultaat |
|---|---|---|
| pytest EMS (Python 3.13) | ontwikkelomgeving (Linux) | **363 passed, 0 failed** |
| pytest EMS | CI Linux (Python 3.12) en CI Windows | groen (commit `314806b`; de Home Assistant-brugtest is daarna toegevoegd en alleen lokaal gedraaid) |
| pytest Cloud (SQLite / PostgreSQL 16) | ontwikkelomgeving / CI | **26 passed** / groen — cloud ongewijzigd |
| ruff (backend, tests, tools, cloud, windows) | ontwikkelomgeving + CI | geen meldingen |
| Nieuwe end-to-end tests `tests/test_device_library.py` | ontwikkelomgeving (23) + CI (22, zonder de later toegevoegde HA-test) | **23 passed** |
| Wizard-acceptatie `tests/ui/wizard_acceptance.py` (Chromium, tegen `ems serve --lab`) | ontwikkelomgeving | **9/9 PASS**, geen console- of paginafouten |
| Browser-acceptatie `tests/ui/ui_acceptance.py` (Demo, 22 schermen × 2 niveaus × 2 resoluties, touch, toetsenbord, offline) | ontwikkelomgeving | **5/5 PASS** |
| Windows-installer: bouw + acceptatie op schone Windows-runner | CI run 38639047927 | **13/13 PASS** (zie hieronder) |
| Linux (Docker amd64), Raspberry Pi (arm64 onder QEMU), systemd, cloud-PostgreSQL | CI run 38639048054 | **alles groen** |
| Hardware | — | **NOT TESTED** |

## End-to-end tests tegen virtuele apparaten (opdracht §24)

Echte protocollen op 127.0.0.1 (pymodbus Modbus TCP met officiële SunSpec-modellen, HomeWizard API v1,
Shelly Gen2 RPC), productiedrivers, niets gemockt in de drivers.

| Groep | Tests |
|---|---|
| A — detectie | discovery herkent virtuele warmtepomp/batterij/laadpaal/SunSpec/P1/Shelly als EXACT_MODEL; onbekend Modbus-apparaat blijft PROTOCOL zonder merk; ontdubbelen op serienummer; adreswijziging herkend; adres bijwerken via API |
| B — capabilities | rapport per functie (laadpaal zonder SOC; bediening "na inbedrijfstelling"); SG Ready: "geen vermogensregeling" (NOT_SUPPORTED); generieke/bibliotheekdrivers zonder profiel claimen niets |
| C — alleen lezen / schaduw / volledige keten (API) | Testomgeving start leeg en gemarkeerd; batterij toevoegen via merk/model met profielstandaarden; handmatige opdracht in alleen-lezen bereikt het apparaat niet; schaduwmodus schrijft niets; schrijftest; beperkt → volledig (alleen virtueel); opdracht wordt echt uitgevoerd en losgelaten; zelfde profiel op een echt adres: volledige regeling geblokkeerd; geheime velden via metadata versleuteld; storingsinjectie-API |
| D — Modbus-opdrachten | schrijven + terugleescontrole; buiten bereik geweigerd; onbekende stand geweigerd; Modbus-foutcode; time-out: schrijfopdracht **niet herhaald**; offline; apparaatwatchdog zet opdracht terug |
| E — warmtepomp | SG Ready: standaard alleen stand 2/3; boost uitgevoerd en teruggemeld; eco geweigerd tenzij toegestaan; relais-watchdog; veilige standaard bij loslaten; controller vraagt nooit een geweigerde stand |
| F — batterij en PV | batterij ontladen 3 kW gemeten, boven maximum geweigerd, loslaten = automatisch; SunSpec-identiteit, begrenzing 2 kW werkt, loslaten zet `WMaxLim_Ena` uit; P1 uitlezen; Shelly-schakelaar |
| Profielen | codec (alle datatypes, woord-/bytevolgorde, NaN); ingebouwde catalogus valide en eerlijk (geen HARDWARE_TESTED, community niet schrijfbaar); validatie weigert profielen zonder bron, met onbekende velden, verkeerde status of capability; eigen/community-profielen kunnen schrijven nooit aanzetten (ook niet met een HARDWARE_TESTED-claim); versies + rollback; merkenlijst eerlijk |
| Home Assistant | brug tegen een nagebootste HA-API: kW→W, bediening alleen na bevestiging, juiste service-call, ongeldig token |
| G — UI | wizard-acceptatie (15 soorten, merk/model, automatisch zoeken, test + rapport, opslaan, bewerkformulier blijft staan tijdens live-updates, wachtwoordveld, storing → offline, mobiel 390 px) |
| H — Windows | acceptatiestap 9c in CI (hieronder) |

## Windows-acceptatie 0.6.0 (schone Windows-runner, CI run 38639047927, commit `314806b`)

```
PASS  install: files, Start menu entries and autostart entry present
PASS  Windows app launches (WebView2) and stays open
PASS  background EMS (no window) runs; database created (sqlite, schema v2)
PASS  live dashboard data: grid 5310 W, EMS status AUTOMATIC
PASS  optimizer active: 107 planned slots, status optimal
PASS  WebSocket delivers live updates
PASS  settings saved
PASS  app closed, EMS keeps running in the background
PASS  restart via autostart (as after a Windows reboot): settings, mode and history kept (4 -> 5 samples)
PASS  upgrade over existing install: EMS stopped by installer, data and settings kept
PASS  version 0.6.0 everywhere; downgrade over a newer version refused (exit code 1), EMS untouched
PASS  Testomgeving: 8 virtual devices; search recognises exact model; Modbus TCP probe, identity, add, test, shadow and write test OK; SunSpec found
PASS  uninstall: program, autostart entry and background EMS removed; user data kept
```

Installer `EnergyManagerSetup-0.6.0.exe`, 71.861.715 bytes (68,5 MB), SHA-256
`f88dd88625a1d80a19ee33b30b8265635e5d86cfbdb014ee86b4027248de3d21` — na publicatie opnieuw gedownload en
nagerekend: gelijk aan SHA256SUMS.txt. **UNSIGNED TEST BUILD** (geen handtekening).

## Niet getest

* Echte apparaten (geen enkel merk) — BLOCKED BY HARDWARE; Modbus RTU over een echte seriële poort;
  een echte Home Assistant; mDNS-discovery op een echt netwerk (geen multicast in CI).
* De Windows-app is niet handmatig door een mens doorgeklikt; de CI-acceptatie test installatie, app-start,
  achtergrond-EMS en de Testomgeving via de API. De wizard is in Chromium getest (zelfde webinterface).
* Fysieke Raspberry Pi; nodes op meerdere fysieke machines; Energy Manager Cloud op echte hosting.

---

# Eerdere release: 0.5.0

Alles hieronder is echt uitgevoerd. Niets is met echte hardware getest. De cloud is **niet** op een echte
hostingomgeving getest (zie `cloud/DEPLOYMENT.md`).

## Samenvatting

| Suite | Waar | Resultaat |
|---|---|---|
| pytest EMS (Linux, Python 3.13) | ontwikkelomgeving | **340 passed, 0 failed, 0 skipped** |
| pytest Cloud (SQLite) | ontwikkelomgeving | **26 passed** |
| pytest Cloud (PostgreSQL 16) | ontwikkelomgeving (lokale PostgreSQL 16) | **26 passed** |
| ruff (backend, tests, tools, cloud) | ontwikkelomgeving | geen meldingen |
| Browser-acceptatie EMS (`tests/ui/ui_acceptance.py`, Chromium) | lokaal tegen Demo-server | **5/5 PASS**, geen console- of paginafouten |
| Browser-test cloudportal (Chromium) | lokaal tegen `emcloud serve` | registratie → verificatie → login → locatie → koppelcode → organisatie → account → beheerportaal met verplichte MFA → klant toevoegen → prijs instellen; mobiel 390 px zonder horizontale scroll; alleen verwachte 401's (niet ingelogd) |
| Cloud productiemodus | lokaal | start geweigerd zonder sleutel/https/SMTP/PostgreSQL; met volledige instellingen: health `ok`, geen API-docs, HSTS aan |
| Cloud Docker-image | lokaal (basisimage vervangen door lokaal image i.v.m. Docker Hub-limiet) | gebouwd, start als niet-root, healthcheck `healthy` tegen PostgreSQL 16 |
| EnergyZero live (officieel endpoint, schema) | CI run 37974579113 | HTTP 200; sleutels `base`, `start/end/price.value`; kwartier: 96/96 vandaag, morgen beschikbaar, 0 verworpen; uur idem |
| CI 0.5.0 (Linux, Pi/QEMU, cloud/PostgreSQL, Windows-installer) | CI runs 37978409322 en 37978409302 | **alles groen** — zie hieronder |
| Hardware | — | **NOT TESTED** |

## Nieuwe tests in 0.5.0

| Bestand | Tests | Dekt (opdracht §7 en §21) |
|---|---|---|
| `tests/test_price_sources.py` | 29 | EnergyZero zonder token; ENTSO-E met token; reservebron aan/uit en alleen gaten vullen; SSRF (privé, loopback, 169.254.169.254, CGNAT, ULA, http, inloggegevens, .local, localhost); geen redirects; officiële parameters + retry; ontbrekend kwartier (geschat) en groot gat (ontbreekt); vertraagde publicatie en herhaalschema; 92/96/100 kwartieren via EnergyZero; beursprijs vs. prognose (band, betrouwbaarheid, horizon); verlopen en verouderde prijzen; leveranciersopslagen; negatieve terugleverwaarde; API offline; cache + synchronisatiestatus na herstart; optimizer gebruikt alleen toegestane statussen; geen netladen op prognoses |
| `tests/test_api.py` (+1) | 1 | `/prices/fields`, `/prices/test` (SSRF, uitgeschakelde eigen adressen, demo), gemaskeerde Authorization-header, `/prices/status`, statussen in `/prices` |
| `cloud/tests/test_auth.py` | 11 | registratie + verificatie, wachtwoordbeleid, zelfregistratie uit, cookie + CSRF, uitloggen, brute force (blokkade + IP-limiet), reset (beëindigt sessies, verloopt), wachtwoordloos, MFA (replay, verplicht voor platformbeheer, per organisatie), sessies intrekken, inactiviteit en maximale duur, export en verwijderen |
| `cloud/tests/test_isolation.py` | 5 | geen cross-tenant toegang (404), rollen en privilege-escalatie, toegang per locatie, platformbeheer zonder klantdata, tijdelijke supporttoegang (verloopt, intrekbaar, gelogd) |
| `cloud/tests/test_nodes_billing.py` | 10 | koppelcode (eenmalig, verloopt), brute force op codes, heartbeat/online, geen waarden zonder toestemming, tokenrotatie + intrekken, opdracht zonder recht/locatieschakelaar/licentie, afgeleverd één keer, andere node kan niet bevestigen, verlopen opdrachten, prijzen niet in code, abonnementscyclus, klant toevoegen + prijs + verlengen + opschorten, limieten, productie-instellingen |
| `tests/test_cloud_link.py` | 4 | echt lokaal EMS ↔ cloud: koppelen via lokale API, token niet in YAML, opdracht geweigerd zonder lokale toestemming, geaccepteerd via override-pad + engine, geweigerd door `can_execute` (meter, onbekend apparaat/actie), cloud offline / licentie verlopen / koppeling ingetrokken → lokaal blijft regelen; kijker kan niet koppelen |

## CI 0.5.0

Commit `468046f`, 2026-10-09.

| Job | Resultaat |
|---|---|
| test (Ubuntu, Python 3.12) | ruff schoon; pytest EMS groen; cloudtests (SQLite) groen; `ems selftest` OK; EnergyZero live OK |
| cloud-postgres | cloudtests op PostgreSQL 16 groen; image gebouwd; start in productiemodus zonder instellingen **geweigerd**; met database: health `ok` |
| docker | amd64 + arm64 gebouwd; Linux-acceptatie (Docker amd64) PASS; Raspberry Pi-acceptatie (arm64 onder QEMU, geen fysieke Pi) PASS |
| linux-native | systemd-installatie, herstart, crashherstel, herinstallatie PASS |
| windows | tests (Windows) groen; PyInstaller, Tauri, Inno Setup; acceptatie **12/12 PASS** |

Windows-acceptatie (schone Windows-runner):

```
PASS  install: files, Start menu entries and autostart entry present
PASS  Windows app launches (WebView2) and stays open
PASS  background EMS (no window) runs; database created (sqlite, schema v2)
PASS  live dashboard data: grid 5524 W, EMS status AUTOMATIC
PASS  optimizer active: 115 planned slots, status optimal
PASS  WebSocket delivers live updates
PASS  settings saved
PASS  app closed, EMS keeps running in the background
PASS  restart via autostart (as after a Windows reboot): settings, mode and history kept (4 -> 5 samples)
PASS  upgrade over existing install: EMS stopped by installer, data and settings kept
PASS  version 0.5.0 everywhere; downgrade over a newer version refused (exit code 1), EMS untouched
PASS  uninstall: program, autostart entry and background EMS removed; user data kept
```

Installer `EnergyManagerSetup-0.5.0.exe`, 70.957.052 bytes (67,7 MB), SHA-256
`97575a14129c89a62723c27d782aa9ccee1aa6ce42255327139681aa65643541` — na publicatie opnieuw gedownload en
nagerekend: gelijk aan SHA256SUMS.txt. **UNSIGNED TEST BUILD.**

## Niet getest

* Echte apparaten — BLOCKED BY HARDWARE; fysieke Raspberry Pi; nodes op meerdere fysieke machines.
* Energy Manager Cloud op een echte hostingomgeving (domein, TLS-certificaat, SMTP, back-up/herstel,
  betaalprovider) en de volledige compose-stack (Caddy + PostgreSQL) als geheel.
* Opdrachten op afstand naar echte hardware.
