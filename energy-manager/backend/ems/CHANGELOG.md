# Changelog

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
