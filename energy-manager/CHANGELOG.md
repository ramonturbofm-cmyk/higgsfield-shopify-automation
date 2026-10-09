# Changelog — Energy Manager

(Zelfde inhoud als `backend/ems/CHANGELOG.md`, dat met het pakket wordt meegeleverd.)

## 0.4.0 — audit-remediatie (UNSIGNED TEST BUILD)

Alle P0-bevindingen uit de audit van 0.2.0 opgelost of (P0-08) in simulatie aangetoond; zie
AUDIT_REMEDIATION.md voor de volledige lijst.

**Veiligheid**
- Typegebonden capabilities (één bron) en centrale `can_execute` per actie; `/devices/{id}/actions`
- Eén control state per apparaat; "EMS zou doen" naast "EMS doet nu" met bevestiging door het apparaat
- Automatiseringen alleen met acties die het apparaat ondersteunt (UI en server)
- Inbedrijfstelling per apparaat: procedure, schrijftest, getypte apparaatnaam; volledige regeling alleen met
  een op hardware bewezen driver; nieuwe/onbekende echte apparaten standaard alleen-lezen (ook in YAML)
- Strengere SafetyValidator: geen vermogens-/stroomopdracht zonder ingestelde apparaatlimiet, setpoint- en
  SOC-limietbereik, nominale laadpaalstroom
- Browsersessie met HttpOnly-cookie + CSRF, rotatie, uitloggen trekt sessie in; WebSocket met eenmalig
  ticket; geen tokens meer in localStorage of URL's
- Back-ups standaard zonder sleutels; met sleutels verplicht wachtwoord en volledig versleuteld
- Node-opdrachten met opdracht-ID (replay geweigerd) en klokverschilcontrole; primaire netmeter nooit stil
  vervangen
- Testmatrix commandotype × storingssituatie (75 tests)

**Tarieven, prijzen, financiën**
- Energiebelasting NL 2026 € 0,09161 met schijvenmodel
- Contractafhankelijke afrekening (saldeermethode), splitsing per jaar, negatieve terugleverprijs
- Handmatige prijzen per 15 of 60 minuten met validatie en gatenrapport; contractprijs per uur mogelijk
- "Nu"-prijs alleen uit het lopende interval; status per interval (bevestigd/schatting/ontbreekt)
- Financiën met verklaarde baselines (B0–B3) die exact optellen; datadekking zichtbaar
- Backtest over een eigen periode, zomer-/wintertijdcorrect, met dekking en vingerafdruk (reproduceerbaar)

**Planning**
- Redencodes uit de optimizerrun (run-id), expliciete warmtepompactie, horizon uit instellingen,
  echte uur-aggregatie; bugfix in gerapporteerd netladen

**Interface**
- Wizard met merk/model en "Mijn apparaat staat er niet tussen"; schema-gestuurde velden met bereiken;
  bewerken van apparaten; fase alleen waar relevant
- Niveaus Eenvoudig/Uitgebreid/Expert app-breed; menu alleen met aanwezige apparaten
- Dashboard met tijdigheidslabels per waarde; grafieken met tijdemmers (gemiddelde + min/max)
- Time-outs en "Server offline"; toetsenbord, focus, reduced motion; getest op 1366×768, 1920×1080, touch

**Platformen en release**
- Linux/Raspberry Pi-acceptatietest in CI (Docker amd64, Docker arm64 via QEMU, systemd)
- Eén versiebron (`tools/set_version.py`); downgradebescherming in installer en database
- Windows-release met versienummer en SHA-256, gemarkeerd als UNSIGNED TEST BUILD

## 0.3.0
- Energy Manager-nodes: Windows, Raspberry Pi en Linux standalone of samen als één EMS
  (rollen all-in-one / controller / gateway, mDNS-ontdekking, koppelen met 6-cijferige code)
- Regelrecht met epoch-fencing: nooit twee controllers op één apparaat; netwerkuitval geeft apparaten
  terug aan hun eigen regeling; historie wordt na herstel gesynchroniseerd
- Veiligheidscontrole op de eigenaar-node vóór iedere schrijfactie; prioriteitenscheduler; netbeveiliging
- Datakwaliteit en energiebalans; EMS-status, gezondheidsscore, "Mijn installatie"
- Prijsbron EnergyZero (kwartier/uur, geen token); kwartierdekking rond zomer-/wintertijd
- Energiebelasting per jaar; settlement NL 2026 (salderen) vs. 2027
- Dashboard "wat doet het EMS nu / waarom / tot wanneer / voordeel"; planning met "Waarom?";
  strategie "Simuleer wijziging"
- Windows: alles op deze computer (EMS op de achtergrond, autostart), slaapstandwaarschuwing,
  acceptatietest van de installer in CI

## 0.2.0
- EMS-server met database, REST API v1 en WebSocket live-updates
- Webinterface (ook gebruikt door de Windows-app)
- GridMeter-abstractie; HomeWizard P1 (API v1/v2) en directe DSMR P1
- Generieke alleen-lezen drivers: Modbus TCP, HTTP/JSON, MQTT (mapping uit eigen documentatie)
- Tariefengine, prijsbronnen (ENTSO-E, handmatig, demo)
- Rolling-horizon optimizer (MILP), zero-export, piekbegrenzing, warmtepomp- en EV-planning
- Prognoses, automatiseringen, inbedrijfstelling met schaduwmodus
- Backtesting, Auto-Tune, financieel overzicht, back-up/restore, meldingen
- Demo Mode, Raspberry Pi-deployment (Docker, install.sh met rollback)
- Windows-app (Tauri) met serverdetectie + gecombineerde installer

## 0.1.0
- Architectuur, EMS-kern, pluginsysteem, simulator
