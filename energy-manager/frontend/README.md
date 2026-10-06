# Frontend (fase 3)

Eén webfrontend voor zowel de **Windows-app** als de **webinterface** op telefoon/tablet.

* React + TypeScript + Vite, PWA
* Windows-app: Tauri 2-shell rond dezelfde build (WebView2, ~10 MB installer)
* Light / dark / automatisch (`prefers-color-scheme`)
* Praat uitsluitend met de Pi via de REST/WebSocket-API uit fase 2
* Instellingenschermen worden gegenereerd uit het JSON-schema van de config
  (`ems.core.config.settings_schema()`), inclusief SIMPLE/ADVANCED/EXPERT-niveaus

Nog niet gebouwd — zie ../DEVELOPMENT.md.
