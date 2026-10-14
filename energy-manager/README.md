# Energy Manager

Een universeel, uitbreidbaar **Energy Management System (EMS)** voor woningen en kleine bedrijven.
De EMS-server draait 24/7 zelfstandig op een **Raspberry Pi 4/5** (of andere Linux-computer) en regelt,
plant en logt. De **Windows-app** en de **webinterface** (telefoon/tablet) zijn alleen bediening:
het EMS blijft werken als de app dicht is.

> **Versie 0.6.0 — UNSIGNED TEST BUILD.** Nieuw: apparaat toevoegen op **merk en model** (of automatisch
> zoeken), een lokale **apparatenbibliotheek** met gevalideerde profielen, echte protocollen (Modbus TCP/RTU
> via pymodbus, **SunSpec**, Shelly Gen2+, **SG Ready**-relais, optionele **Home Assistant**-brug), een eerlijk
> functierapport per apparaat, en de startmodus **Testomgeving**: een virtuele woning waarvan de apparaten
> echte protocollen spreken — zie [CHANGELOG.md](CHANGELOG.md) en [TEST_MORGEN_WINDOWS.md](TEST_MORGEN_WINDOWS.md).
> Er is nog **niets met echte hardware getest**; volledige regeling van echte apparaten blijft daarom
> geblokkeerd. Ondersteunde apparaten: [SUPPORTED_DEVICES.md](SUPPORTED_DEVICES.md). Beperkingen:
> [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md). De cloud (`cloud/`) is ongewijzigd en niet productierijp verklaard.

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
* **Testomgeving**: een lege installatie met een virtuele woning; u voegt de virtuele apparaten zelf toe via de
  wizard en doorloopt de hele keten (zoeken, testen, inbedrijfstelling, sturen, terugmelden, storingen).
* **Demo Mode**: een complete gesimuleerde "Demo Home" (PV 8 kWp, batterij 15 kWh, warmtepomp, EV,
  3×25 A, dynamische prijs) om alles zonder hardware te proberen. In productie worden nooit nepwaarden
  getoond.

## Installeren

* Windows: [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md)
* Linux (Docker of systemd): [INSTALL_LINUX.md](INSTALL_LINUX.md)
* Raspberry Pi: [INSTALL_RASPBERRY_PI.md](INSTALL_RASPBERRY_PI.md)

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
(WireGuard/Tailscale) en voor HTTPS een reverse proxy. Inloggen gebeurt met een HttpOnly-sessiecookie en
CSRF-bescherming. Wachtwoorden en tokens staan versleuteld in de datamap, nooit in de configuratie;
gebruik `${VARIABELE}` met de waarde in `.env` voor eigen geheimen. Back-ups bevatten standaard geen sleutels.

## Windows

**Downloaden (altijd de nieuwste geteste versie):**
https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation/releases/download/energy-manager-windows/EnergyManagerSetup.exe

Iedere versie wordt op een schone Windows-machine automatisch getest (installeren, starten, Demo Mode,
database, WebSocket, instellingen, app sluiten, herstart, upgrade, downgradeweigering, verwijderen); alleen
bij succes wordt hij gepubliceerd, met `SHA256SUMS.txt` en de testuitslag (`acceptance-results.txt`).
**UNSIGNED TEST BUILD** (geen code-signing-certificaat): SmartScreen → *Meer informatie* → *Toch uitvoeren*.

Bij het openen kiest u **hoe u Energy Manager wilt gebruiken**:

* **Alles op deze computer** — geen Raspberry Pi nodig. Kies *Mijn eigen installatie* (echte apparaten),
  *Demo* (gesimuleerd huis, inloggen demo/demo) of *Testomgeving* (virtuele apparaten via echte protocollen,
  inloggen demo/demo) en klik **Starten**. Het EMS draait op de achtergrond
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

## Energy Manager Cloud (optioneel)

Op afstand bekijken en — als u dat zelf toestaat — bedienen, met accounts, organisaties en licenties. Uw
installatie maakt alleen een uitgaande HTTPS-verbinding; er gaan geen poorten open op uw router. Koppelen:
maak in de cloudportal bij uw locatie een koppelcode en vul die lokaal in bij *Instellingen → Cloud*
(Uitgebreid). Zonder cloud werkt alles lokaal precies hetzelfde. Server installeren: [cloud/DEPLOYMENT.md](cloud/DEPLOYMENT.md);
privacy: [PRIVACY_COMPLIANCE.md](PRIVACY_COMPLIANCE.md).

## Apparaten koppelen

In de webinterface: **Apparaten → Apparaat toevoegen**: kies de **soort** (15 tegels) en daarna **hoe**:

1. **Automatisch zoeken** — mDNS (HomeWizard, Shelly, Home Assistant) en een identiteitsvraag op een IP-adres
   dat u opgeeft (HTTP en Modbus/SunSpec op poort 502). Geen poortscan. Herkenning: *exact model*,
   *fabrikant* of *alleen protocol* — een onzeker apparaat wordt nooit als een bepaald merk behandeld.
2. **Merk en model** — de lijst toont per model eerlijk wat kan (ondersteund, alleen uitlezen, via SG Ready,
   via Home Assistant, nog niet ondersteund + alternatief).
3. **Via een gateway** — SG Ready-relais (warmtepomp), Shelly-relais, Home Assistant, Modbus-gateway.
4. **Handmatig (Expert)** — generiek Modbus/HTTP/MQTT, alleen uitlezen.

Daarna: gegevens (alleen de velden die bij uw niveau horen) → **Verbinding testen** → identiteit en
**functierapport** → opslaan. Nieuwe apparaten starten **alleen-lezen**; via *Inbedrijfstelling* volgt u per
apparaat de procedure ([COMMISSIONING_GUIDE.md](COMMISSIONING_GUIDE.md)). Overzicht:
[SUPPORTED_DEVICES.md](SUPPORTED_DEVICES.md); technisch: [DEVICE_CATALOG.md](DEVICE_CATALOG.md).

Een nieuw merk toevoegen kan vaak met alleen een JSON-profiel ([DEVICE_PROFILE_SCHEMA.md](DEVICE_PROFILE_SCHEMA.md),
[DRIVER_SDK.md](DRIVER_SDK.md)), maar nooit zonder officiële documentatie. Lever daarvoor aan: merk, exact model,
firmwareversie, aansluiting (LAN/RS485/USB) en de protocoldocumentatie.

Zonder primaire netmeter toont het EMS: *"Geen primaire netmeter ingesteld. Sommige EMS-functies zijn
beperkt."* (geen zero-export, piekbegrenzing of fasebewaking).

## Ontwikkelen

Vereist Python 3.11+.

```bash
cd energy-manager
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ems serve --demo --data-dir ./data-demo     # http://127.0.0.1:8080 (login demo/demo)
ems serve --lab --data-dir ./data-lab       # Testomgeving: virtuele apparaten op 127.0.0.1:15502-15599
pytest -q                                   # 363 tests
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
│   ├── devices/       driver-interface, plugin-registry, DeviceManager, discovery, capabilityrapport,
│   │                  library/ (profielschema, catalogus, profielen, merk/model-lijst)
│   ├── gridmeter/     GridMeter-abstractie en selectie van de primaire netmeter
│   ├── integrations/  homewizard/, dsmr/, modbus/ (pymodbus-transport, codec, profieldriver), sunspec/,
│   │                  shelly/ (schakelaar, SG Ready), homeassistant/, generic/, mock/ (Demo Mode)
│   ├── nodes/         node-identiteit, koppelen, regelrecht (lease), gateway, node.remote-driver
│   ├── control/       controllers, zero-export, overrides, CommandGate (inbedrijfstelling)
│   ├── tariffs/ prices/ forecasting/ optimizer/ automations/
│   ├── services/      financieel, backtest/Auto-Tune, back-up
│   ├── server/        runtime (alles samen), historie, meldingen, Demo Mode
│   ├── api/           FastAPI REST + WebSocket
│   ├── database/ security/
│   ├── web/           webinterface (ook gebruikt door de Windows-app)
│   └── simulator/     fysiek woningmodel; lab.py = virtuele apparaten van de Testomgeving
├── windows-app/       Tauri 2-app (verbindingsscherm)
├── windows/           PyInstaller-server + Inno Setup-installer
├── deploy/            systemd-unit
├── Dockerfile, docker-compose.yml, install.sh, .env.example
└── tests/
```

## Documentatie

* [ARCHITECTURE.md](ARCHITECTURE.md) — technisch ontwerp en keuzes
* [DEVELOPMENT.md](DEVELOPMENT.md) — status per onderdeel, teststatus, besluiten, beperkingen
* [TEST_MORGEN_WINDOWS.md](TEST_MORGEN_WINDOWS.md) — stappenplan om 0.6.0 op Windows te testen
* [SUPPORTED_DEVICES.md](SUPPORTED_DEVICES.md) — wat werkt met welk apparaat
* [DEVICE_INTEGRATION_AUDIT.md](DEVICE_INTEGRATION_AUDIT.md) — audit van de apparaatintegratie (status per bevinding/integratie)
* [DEVICE_CATALOG.md](DEVICE_CATALOG.md), [DEVICE_PROFILE_SCHEMA.md](DEVICE_PROFILE_SCHEMA.md), [DRIVER_SDK.md](DRIVER_SDK.md)
* [COMMISSIONING_GUIDE.md](COMMISSIONING_GUIDE.md), [SAFETY_RULES.md](SAFETY_RULES.md)
* [DEVICE_INTEGRATION_GUIDE.md](DEVICE_INTEGRATION_GUIDE.md) — apparaten en drivers
* [AUDIT_REMEDIATION.md](AUDIT_REMEDIATION.md) — status per auditbevinding
* [TEST_RESULTS.md](TEST_RESULTS.md) — testuitslagen van deze release
* [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) — bekende beperkingen
* [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md), [INSTALL_LINUX.md](INSTALL_LINUX.md), [INSTALL_RASPBERRY_PI.md](INSTALL_RASPBERRY_PI.md)
* [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) — controle vóór een release
* [cloud/DEPLOYMENT.md](cloud/DEPLOYMENT.md) — Energy Manager Cloud installeren, wat nog ontbreekt
* [PRIVACY_COMPLIANCE.md](PRIVACY_COMPLIANCE.md) — AVG-maatregelen en punten voor juridische toetsing
* [CHANGELOG.md](CHANGELOG.md)
