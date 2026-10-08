# Energy Manager

Een universeel, uitbreidbaar **Energy Management System (EMS)** voor woningen en kleine bedrijven.
De EMS-server draait 24/7 zelfstandig op een **Raspberry Pi 4/5** (of andere Linux-computer) en regelt,
plant en logt. De **Windows-app** en de **webinterface** (telefoon/tablet) zijn alleen bediening:
het EMS blijft werken als de app dicht is.

> **Versie 0.3.0.** Volledige server, optimizer, webinterface, Windows-app en Demo Mode werken.
> Uitlezen van echte apparaten: HomeWizard P1, DSMR P1 en generieke Modbus TCP/HTTP/MQTT
> (alleen-lezen). Er is nog **niets met echte hardware getest** en er zijn bewust **geen schrijvende
> drivers** voor echte apparaten (geen verzonnen protocollen). Status: [DEVELOPMENT.md](DEVELOPMENT.md).

## Wat het doet

* **Live dashboard** met energiestromen (net, PV, batterij, warmtepomp, laadpaal, huis), fasestromen en prijzen.
* **Planning 24–36 uur** via een rolling-horizon optimizer (MILP, elke 5 min): wanneer laden/ontladen,
  PV afregelen, warmtepomp voorverwarmen, auto laden — met verwachte kosten en voordeel t.o.v. "zonder EMS".
* **Tariefengine** (dynamisch/vast/dal-piek, opslagen, energiebelasting, btw, salderen) en prijsbronnen
  (EnergyZero zonder token, ENTSO-E, handmatig) met cache.
* **Veiligheid eerst**: fail-safe bij wegvallende netmeting, watchdog, fasebewaking, piekbegrenzing,
  zero-export; apparaten vallen altijd terug op hun eigen regeling.
* **Inbedrijfstelling per apparaat**: verbindingstest → alleen lezen → schaduwmodus ("EMS zou…") →
  beperkt → volledig.
* Automatiseringen, handmatige overrides met verlooptijd, uitlegbare beslissingen, historie en export,
  financieel overzicht, backtesting en Auto-Tune (nooit automatisch toegepast), meldingen,
  back-up/restore, gebruikers met rollen.
* **Demo Mode**: een complete gesimuleerde "Demo Home" (PV 8 kWp, batterij 15 kWh, warmtepomp, EV,
  3×25 A, dynamische prijs) om alles zonder hardware te proberen. In productie worden nooit nepwaarden
  getoond.

## Installeren op de Raspberry Pi

Benodigd: Raspberry Pi 4/5 met **Raspberry Pi OS 64-bit** (Lite volstaat), bij voorkeur een SSD via USB3,
netwerkkabel. Geef de Pi de hostnaam `energy-manager` (Raspberry Pi Imager → instellingen); de Windows-app
vindt hem dan automatisch.

```bash
git clone <deze repository> && cd <repository>/energy-manager
cp .env.example .env          # optioneel: tijdzone, poort, ENTSOE_TOKEN
./install.sh                  # installeert Docker indien nodig, bouwt en start (productie)
# of: ./install.sh demo       # start in Demo Mode
```

Open daarna `http://energy-manager.local:8080` (of `http://<ip-van-de-pi>:8080`). Bij de eerste keer
maakt u een beheerdersaccount aan.

Beheer:

| Opdracht | Doet |
|---|---|
| `./install.sh status` | containerstatus + health |
| `./install.sh logs` | live logboek |
| `./install.sh update` | back-up → nieuwe versie → healthcheck → **automatische rollback** bij een fout |
| `./install.sh backup` | back-up naar `./backups/` |
| `./install.sh watchdog` | hardware-watchdog van de Pi inschakelen (herstart bij vastlopen) |
| `./install.sh uninstall` | stoppen (gegevens blijven bewaard in het Docker-volume) |

Zonder Docker: `pip install .` en de systemd-unit `deploy/energy-manager.service`
(`Type=notify`, `WatchdogSec`). Optioneel PostgreSQL: `docker compose --profile postgres up -d`
en `EMS_DATABASE_URL` in `.env`.

**Beveiliging:** zet poort 8080 nooit open naar internet. Gebruik voor toegang op afstand een VPN
(WireGuard/Tailscale). Wachtwoorden en tokens staan versleuteld in de datamap, nooit in de configuratie;
gebruik `${VARIABELE}` met de waarde in `.env` voor eigen geheimen.

## Windows

**Downloaden (altijd de nieuwste geteste versie):**
https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation/releases/download/energy-manager-windows/EnergyManagerSetup.exe

Iedere versie wordt op een schone Windows-machine automatisch getest (installeren, starten, Demo Mode,
database, WebSocket, instellingen, app sluiten, herstart, upgrade, verwijderen); alleen bij succes wordt hij
gepubliceerd. De testuitslag staat bij de download (`acceptance-results.txt`). Nog niet digitaal ondertekend:
SmartScreen → *Meer informatie* → *Toch uitvoeren*.

Bij het openen kiest u **hoe u Energy Manager wilt gebruiken**:

* **Alles op deze computer** — geen Raspberry Pi nodig. Kies *Mijn eigen installatie* (echte apparaten) of
  *Demo* (gesimuleerd huis, inloggen demo/demo) en klik **Starten**. Het EMS draait op de achtergrond
  (zonder venster), blijft werken als u de app sluit en start automatisch na aanmelden in Windows. Zet de
  slaapstand van de pc uit; de app waarschuwt als de pc in slaap kan vallen.
* **Verbinden met bestaand Energy Manager-systeem** — de app zoekt het EMS op het netwerk
  (`energy-manager.local`, `raspberrypi.local`, eerdere servers) of u vult het adres in.
* **Raspberry Pi / Linux-node toevoegen** — installeer Energy Manager op de Pi, toon daar onder *Nodes* de
  koppelcode en koppel hem op deze computer onder *Nodes*. De Pi kan bijvoorbeeld de P1-meter of
  RS485-apparaten aansluiten; samen vormen ze één EMS.

Stoppen: in de app *Andere server* → **EMS stoppen**, of Startmenu → *Energy Manager → Hulpmiddelen →
EMS stoppen*. Logboek: `%LOCALAPPDATA%\EnergyManager\logs\server.log`. Gegevens blijven bewaard bij
bijwerken en verwijderen (`%LOCALAPPDATA%\EnergyManager`).

Zelf bouwen op Windows (Python 3.12, Node 20+, Rust, Inno Setup 6): `.\windows\build.ps1`;
acceptatietest: `.\windows\acceptance.ps1 -Installer dist\EnergyManagerSetup-<versie>.exe`.

## Meerdere computers (nodes)

Windows, Raspberry Pi en Linux zijn gelijkwaardig. Iedere installatie is een *node*; u kunt ze koppelen tot
één EMS, bijvoorbeeld:

| Opzet | Rollen |
|---|---|
| Alleen Windows / alleen Pi / alleen Linux | alles op die ene computer (standaard) |
| Windows + Pi | Windows regelt en toont, de Pi in de meterkast leest P1/RS485 (rol *gateway*) |
| Linux + Pi + Windows | Linux-mini-pc regelt (rol *controller*), Pi is gateway, Windows alleen bediening |

Rol instellen: *Instellingen → Algemeen → Deze computer (node)* (ADVANCED). Koppelen gebeurt altijd met een
koppelcode; een nieuwe node krijgt nooit automatisch toegang. Er regelt altijd maar één controller per
apparaat; valt de verbinding weg, dan gaan de apparaten terug naar hun eigen regeling.

## Apparaten koppelen

In de webinterface: **Apparaten → Apparaat toevoegen**. De wizard test de verbinding en toont per functie
✓/✗. Nieuwe apparaten starten **alleen-lezen**; via *Inbedrijfstelling* gaat u stap voor stap naar
schaduwmodus en (zodra er een schrijvende driver is) beperkte/volledige regeling.

| Apparaat | Hoe |
|---|---|
| **HomeWizard P1-meter** | automatisch gevonden (mDNS); bij API v2 drukt u op de knop van de meter om te koppelen. Wordt automatisch de primaire netmeter. |
| **Slimme meter via P1-kabel** | driver *DSMR P1*: USB-kabel (`/dev/ttyUSB0`) of een netwerk-P1-bridge (TCP). In Docker: `devices:` in `docker-compose.yml` aanzetten. |
| **Modbus TCP-apparaat** | *Generiek Modbus TCP*: IP, unit-ID en een waardetoewijzing (adres, type, schaal) **uit de handleiding van het apparaat**. |
| **Apparaat met JSON-API** | *Generiek HTTP/JSON*: URL en JSON-paden. |
| **Via MQTT** (bijv. bestaande gateway) | *Generiek MQTT*: broker, topics en (optioneel) JSON-pad. |

Voor het **aansturen** van een specifieke omvormer, batterij, warmtepomp of laadpaal is een driver
nodig op basis van de officiële documentatie. Lever daarvoor aan: merk, exact model, firmwareversie,
aansluiting (LAN/RS485/USB) en de protocoldocumentatie — zie
[DEVICE_INTEGRATION_GUIDE.md](DEVICE_INTEGRATION_GUIDE.md).

Zonder primaire netmeter toont het EMS: *"Geen primaire netmeter ingesteld. Sommige EMS-functies zijn
beperkt."* (geen zero-export, piekbegrenzing of fasebewaking).

## Ontwikkelen

Vereist Python 3.11+.

```bash
cd energy-manager
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ems serve --demo --data-dir ./data-demo     # http://127.0.0.1:8080 (login demo/demo)
pytest -q                                   # 171 tests
ruff check backend tests
ems-sim --start 2026-06-15 --days 2 --compare-native   # simulator-CLI
```

Andere opdrachten: `ems create-user <naam> --role admin`, `ems backup <bestand.zip>`,
`ems check-config <ems.yaml>`, `ems selftest`. API-documentatie: `http://<server>:8080/api/docs`.

## Projectstructuur

```
energy-manager/
├── backend/ems/
│   ├── core/          modellen, config, engine, validatie, journaal, watchdog, events, klok
│   ├── devices/       driver-interface, plugin-registry, DeviceManager
│   ├── gridmeter/     GridMeter-abstractie en selectie van de primaire netmeter
│   ├── integrations/  homewizard/, dsmr/, generic/ (Modbus TCP, HTTP/JSON, MQTT), mock/ (Demo Mode)
│   ├── nodes/         node-identiteit, koppelen, regelrecht (lease), gateway, node.remote-driver
│   ├── control/       controllers, zero-export, overrides, CommandGate (inbedrijfstelling)
│   ├── tariffs/ prices/ forecasting/ optimizer/ automations/
│   ├── services/      financieel, backtest/Auto-Tune, back-up
│   ├── server/        runtime (alles samen), historie, meldingen, Demo Mode
│   ├── api/           FastAPI REST + WebSocket
│   ├── database/ security/
│   ├── web/           webinterface (ook gebruikt door de Windows-app)
│   └── simulator/     fysiek woningmodel
├── windows-app/       Tauri 2-app (verbindingsscherm)
├── windows/           PyInstaller-server + Inno Setup-installer
├── deploy/            systemd-unit
├── Dockerfile, docker-compose.yml, install.sh, .env.example
└── tests/
```

## Documentatie

* [ARCHITECTURE.md](ARCHITECTURE.md) — technisch ontwerp en keuzes
* [DEVELOPMENT.md](DEVELOPMENT.md) — status per onderdeel, teststatus, besluiten, beperkingen
* [DEVICE_INTEGRATION_GUIDE.md](DEVICE_INTEGRATION_GUIDE.md) — apparaten en drivers
* [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) — controle vóór een release
* [backend/ems/CHANGELOG.md](backend/ems/CHANGELOG.md)
