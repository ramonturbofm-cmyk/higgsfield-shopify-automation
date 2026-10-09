# Bekende beperkingen — Energy Manager 0.4.0

Eerlijk overzicht van wat (nog) niet kan of niet bewezen is. Zie ook [AUDIT_REMEDIATION.md](AUDIT_REMEDIATION.md).

## Hardware

* **Niets is met echte hardware getest.** HomeWizard P1, DSMR P1 en de generieke Modbus/HTTP/MQTT-drivers
  zijn gebouwd volgens officiële documentatie en getest tegen nep-apparaten en een echte MQTT-broker; niet
  tegen een fysiek apparaat.
* **Er zijn geen schrijvende drivers voor echte apparaten.** Zonder zo'n driver blijft een echt apparaat op
  Alleen lezen of Schaduwmodus. Volledige regeling vereist bovendien een driver die op echte hardware is
  bewezen (`verified`); dat geldt voor geen enkele driver.
* Raspberry Pi: getest als Docker-image voor arm64 **onder QEMU-emulatie** in CI, niet op een fysieke Pi.

## Regeling en optimizer

* **"Verwacht voordeel" kan licht negatief zijn.** De vergelijking "zonder EMS" waardeert de lading die
  aan het eind van de planningshorizon in de batterij zit niet hetzelfde als de optimizer (die een
  eindwaarde gebruikt). Het getal komt wel uit de optimizer, maar is een indicatie, geen garantie.
* Geen automatische controller-failover (bewust: veiligheid boven snelle overname).
* Warmtepomp: geen ontdooi-, tapwater- of legionellamodel; de modus wordt afgeleid uit het geplande
  vermogen t.o.v. de eigen regeling (één regel, gedeeld door planning en controller).
* V2H/V2G niet geïmplementeerd.
* Uitvoeringsbevestiging ("bevestigd door apparaat") werkt alleen als het apparaat de relevante waarde
  terugmeldt; anders "verzonden (geen terugmelding)".

## Tarieven en financiën

* Energiebelasting NL 2026: eerste schijf € 0,09161 (Belastingdienst). De hogere schijven komen uit een
  gepubliceerd overzicht en zijn gemarkeerd als "nog controleren"; boven 10 miljoen kWh: handmatig invoeren.
* Schijven worden geteld vanaf het begin van de gekozen periode (verbruik eerder in het jaar is onbekend).
* De afrekening is een **schatting**, geen leveranciersfactuur; de saldeermethode hangt af van het contract
  en moet door de gebruiker worden gekozen als "auto" niet klopt.
* Salderen 2026 vs. 2027 is een ceteris-paribus-vergelijking (zelfde gedrag in beide scenario's).
* Financiële baselines nemen warmtepomp- en EV-verbruik zoals gemeten; besparing door verschuiven in de
  tijd wordt niet apart toegekend (voorzichtig).

## Platformen en nodes

* Windows: het EMS start bij aanmelden van de gebruiker (geen Windows-service vóór aanmelden); de pc moet
  aan blijven en mag niet slapen (de app waarschuwt).
* De Windows-installer is **niet ondertekend** (UNSIGNED TEST BUILD): SmartScreen waarschuwt.
* Nodes zijn getest in één proces (gateway en controller via HTTP), niet op meerdere fysieke machines;
  geen test op een verliesgevend netwerk (lossy LAN).
* Klokken van nodes moeten gelijk lopen (NTP): bij meer dan 10 s verschil worden geen opdrachten verstuurd.
* Eén site per installatie (geen multi-site).
* mDNS kan door gast-wifi/VLAN/firewall geblokkeerd worden; koppelen met adres + code werkt altijd.

## Beveiliging

* De server spreekt HTTP. Gebruik op het LAN, nooit open naar internet; voor toegang van buiten een VPN, en
  voor HTTPS een reverse proxy (zie INSTALL_LINUX.md). Cookies krijgen `Secure` zodra de server via HTTPS
  (of `X-Forwarded-Proto: https`) wordt benaderd.
* Ingetrokken sessies (uitloggen) worden in het geheugen bijgehouden; na een herstart van de server
  verlopen oude sessies alleen nog op hun vervaltijd (max. 8 uur).

## Interface

* Niveaus Eenvoudig/Uitgebreid/Expert werken app-breed (menu, wizard, apparaat-, node-pagina), maar niet
  iedere tekst op ieder scherm is per niveau herschreven.
* Geen volledige WCAG-contrastaudit uitgevoerd.
* Automatische apparaatdetectie alleen voor HomeWizard (mDNS); andere merken kiest u in de wizard.
