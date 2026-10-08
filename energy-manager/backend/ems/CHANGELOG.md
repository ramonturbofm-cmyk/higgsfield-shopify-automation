# Changelog

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
