# Release checklist

Werk deze lijst af vóór elke release. Vink alleen af wat echt is gecontroleerd.

## 1. Code en tests

- [ ] `ruff check backend tests windows` foutloos
- [ ] `pytest -q` volledig groen (Linux) — incl. E2E-scenario's 12:00 en 19:00 en MQTT tegen Mosquitto
- [ ] CI *Energy Manager — tests and Raspberry Pi image* groen (tests, selftest, Docker amd64 + arm64, healthcheck)
- [ ] CI *Energy Manager — Windows installer* groen (tests op Windows, server-selftest, Tauri, installer)
- [ ] Versie verhoogd in `backend/ems/__init__.py` en `pyproject.toml`; `backend/ems/CHANGELOG.md` bijgewerkt
- [ ] Databasemigraties: nieuwe migratie toegevoegd én getest op een database van de vorige versie

## 2. Veiligheid

- [ ] Geen geheimen in de repository (`git grep -i -E "password|token|secret"` nagelopen; `.env` niet gecommit)
- [ ] Opgeslagen YAML bevat alleen `${VAR}`- of `secret:`-verwijzingen (test `test_config` / `test_generic_device_password_goes_to_secret_store`)
- [ ] Geen nieuwe schrijvende driver zonder officiële documentatie; `verified=True` alleen na hardwaretest
- [ ] CommandGate: SIMULATION_MODE / DRY_RUN / inbedrijfstellingsniveaus ongewijzigd of getest
- [ ] Fail-safe getest: netmeter weg → apparaten vrijgegeven (test `test_engine`)
- [ ] CORS alleen voor de app-origin; CSP ongewijzigd; geen poorten naar internet in compose
- [ ] Afhankelijkheden: geen bekende kwetsbaarheden (`pip-audit` of vergelijkbaar)

## 3. Raspberry Pi

- [ ] Schone installatie op Raspberry Pi OS 64-bit: `./install.sh` → `http://energy-manager.local:8080` bereikbaar
- [ ] Eerste account aanmaken, inloggen, uitloggen
- [ ] `./install.sh update` vanaf de vorige versie: gegevens en instellingen behouden; rollback getest met een kapotte build
- [ ] Back-up maken en terugzetten (UI) op een tweede installatie
- [ ] Herstart van de Pi: service komt vanzelf terug; watchdog actief
- [ ] Productiemodus zonder apparaten: geen nepwaarden, melding "Geen primaire netmeter ingesteld"

## 4. Apparaten (per beschikbare hardware — leg model/firmware vast in DEVELOPMENT.md)

- [ ] HomeWizard P1: ontdekken, koppelen (knop), live waarden, herverbinden na stroomonderbreking
- [ ] DSMR P1: USB-kabel en/of TCP-bridge, CRC-fouten = 0 over 1 uur
- [ ] Generieke drivers: mapping uit de handleiding, waarden en tekens gecontroleerd tegen het display van het apparaat
- [ ] Schaduwmodus 24 uur: "EMS zou…"-beslissingen plausibel
- [ ] Alleen bij schrijvende drivers: LIMITED-modus, release_control bij stoppen/fail-safe gecontroleerd op het apparaat

## 5. Windows

- [ ] Installer (`EnergyManagerSetup-<versie>.exe`) op een schone Windows 10 en 11 installeren
- [ ] App vindt de Pi automatisch (mDNS) én via handmatig IP-adres
- [ ] Opnieuw starten → automatisch verbinden; "Andere server" werkt
- [ ] Alles-in-één: "Op deze computer" → Starten (eigen installatie én Demo), app sluiten → EMS blijft draaien, herstart Windows → EMS start vanzelf (autostart), "EMS stoppen" werkt
- [ ] Bijwerken terwijl het EMS op de achtergrond draait: installer stopt het eerst; daarna start de app het weer
- [ ] Bijwerken over de vorige versie heen behoudt instellingen; verwijderen werkt
- [ ] (Indien beschikbaar) installer en exe ondertekend

## 6. Webinterface

- [ ] Alle pagina's openen zonder consolefouten (licht, donker, mobiel)
- [ ] Live-updates via WebSocket (dashboard beweegt), herverbinden na serverherstart
- [ ] Rollen: viewer kan niets wijzigen, operator alleen overrides, installer apparaten

## 7. Demo Mode

- [ ] `./install.sh demo` of `ems serve --demo`: Demo Home compleet, planning berekend, beslissingen zichtbaar
- [ ] Duidelijk gemarkeerd als Demo Mode in de interface

## 8. Documentatie

- [ ] README (installatie, Windows, apparaten) klopt met deze versie
- [ ] DEVELOPMENT.md: status en teststatus (UNIT / SIMULATOR / HARDWARE TESTED) bijgewerkt
- [ ] DEVICE_INTEGRATION_GUIDE.md: integratietabel bijgewerkt

## 9. Publiceren

- [ ] Git-tag `v<versie>` op de geteste commit
- [ ] Installer + releasenotes als GitHub-release
