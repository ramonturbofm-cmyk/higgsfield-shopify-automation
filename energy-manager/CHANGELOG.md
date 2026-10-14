# Changelog — Energy Manager

(Zelfde inhoud als `backend/ems/CHANGELOG.md`, dat met het pakket wordt meegeleverd.)

## 0.6.1 — na een update altijd de nieuwe interface; merk/model-catalogus en Home Assistant zonder JSON (UNSIGNED TEST BUILD)

**Reparatie: na een upgrade werd de oude apparatenwizard getoond**
- Oorzaak (nagebootst): de webbestanden werden zonder cache-instructies geserveerd; de Windows-app (WebView2) en
  browsers bewaarden de oude JavaScript-modules urenlang en vroegen de server niet om de nieuwe. Zo draaide de
  0.5.0-wizard tegen een 0.6.0-server.
- Centraal build-ID (versie + commit + bouwtijd) en een hash over alle webbestanden; alle scripts en stijlen worden
  geladen via `/static/v/<hash>/…` (onveranderlijk), `index.html` wordt nooit gecachet, API-antwoorden ook niet
- De pagina vergelijkt haar eigen build met die van de server (bij start, bij herverbinden en elke 2 minuten);
  bij een verschil laadt hij zichzelf één keer opnieuw, anders volgt een duidelijke melding
- Versie van app en server zichtbaar onderin het menu en op de pagina Systeem; `/healthz` en `/system/info` geven
  het build-ID; elke respons heeft `X-EMS-Version` / `X-EMS-Build`
- Windows-app: start de EMS-server opnieuw als er nog een andere versie draait; toont de app-versie
- Installer: wist bij installeren/bijwerken de HTTP-cache van de app (niet de instellingen) en beëindigt
  achtergebleven serverprocessen; het build-ID wordt bij het bouwen vastgelegd
- Acceptatietest in CI bedient nu de **geïnstalleerde app zelf** (WebView2) en test ook een upgrade vanaf de
  gepubliceerde 0.5.0-installer met de oude interface in de cache

**Apparaatcatalogus**
- Merk/model-lijsten tonen echte merken met hun modellen en de functies per model; Home Assistant, SunSpec en
  SG Ready zijn geen "merk" meer maar verbindingsmethoden (via een gateway / "Staat uw merk er niet bij?")
- Veel meer merken per categorie (o.a. Growatt, Enphase, Deye, Vaillant, Viessmann, Panasonic, Alfen, Easee,
  Zaptec, Tesla, Sessy …): eerlijk "nog niet ondersteund", met een knop naar een geschikte alternatieve koppeling
- Bij "Gegevens" staan apparaat en verbinding apart; Modbus TCP toont geen RS485-velden; SunSpec-instellingen
  alleen waar ze bij het soort apparaat horen
- Alle vier de methoden zijn altijd zichtbaar, bij elke categorie

**Home Assistant zonder JSON**
- Verbinden, daarna per meetwaarde en per bediening een entiteit kiezen uit een lijst
- De Testomgeving heeft een virtuele Home Assistant (token `testomgeving`) om dit te proberen

## 0.6.0 — apparatenbibliotheek, echte protocollen en Testomgeving (UNSIGNED TEST BUILD)

**Apparaat toevoegen: kies uw apparaat, niet een protocol**
- Nieuwe wizard: 15 soorten apparaten → automatisch zoeken · merk en model · via een gateway
  (SG Ready-relais, Home Assistant, Shelly, Modbus-gateway) · handmatig (Expert) → gegevens → testen
- Na de verbindingstest: identiteit van het apparaat (merk, model, serienummer, firmware) en een eerlijk
  functierapport per functie (uitleesbaar / bestuurbaar / na inbedrijfstelling / configuratie nodig /
  niet ondersteund / niet geverifieerd)
- Velden per niveau (Eenvoudig / Uitgebreid / Expert); velden die alleen bij RTU of TCP horen worden verborgen;
  geheime velden (wachtwoord, token) volgen uit de driver-metadata, niet uit de veldnaam
- Datablad-standaarden van het gekozen profiel worden vooraf ingevuld (bijv. capaciteit, maximale stroom)
- Herkend apparaat met nieuw IP-adres: "Adres bijwerken" (zelfde apparaat, historie en inbedrijfstelling blijven)

**Apparatenbibliotheek (lokaal, gevalideerd)**
- Drie lagen: transport (Modbus TCP/RTU via pymodbus, HTTP) → driver → modelprofiel (JSON, gevalideerd schema)
- Profielen met bron (documentatie verplicht), teststatus, bekende beperkingen; schrijffuncties alleen met
  status "officiële documentatie" of "simulator"; community- en eigen profielen zetten nooit schrijven aan
- Importeren, versiebeheer en terugdraaien van eigen profielen (geen code, alleen JSON); nieuwe
  schrijffuncties in een profiel zetten een apparaat terug naar schaduwmodus (opnieuw in bedrijf stellen)
- Merk-/modellijst met eerlijke status: ondersteund, alleen uitlezen, via SG Ready, via Home Assistant,
  nog niet ondersteund (met alternatief)

**Nieuwe koppelingen**
- SunSpec (officiële modellen 1, 101–103, 111–113, 120–124, 160, 201–204, 802): automatische modelketen,
  uitlezen omvormer/meter/opslag; vermogensbegrenzing via model 123 met terugvaltijd (alleen als het apparaat
  het aanbiedt)
- Modbus-profieldriver met alle gangbare datatypes, byte-/woordvolgorde, blokuitlezing, terugleescontrole na
  schrijven, geen herhaling van schrijfopdrachten bij onbekende uitkomst
- Shelly Gen2+ (RPC): schakelen met minimale aan-/uittijd en terugmelding
- SG Ready via twee netwerkrelais: veilige standaardstand 2, door de gebruiker gekozen toegestane standen,
  watchdog op het relais, minimale tijd per stand; een verzoek, geen vermogensregeling
- Home Assistant-brug (optioneel): entiteiten ophalen, meetwaarden; bediening alleen na bevestiging van
  exclusieve regeling, via de gewone validatie
- Zoeken op het netwerk: mDNS (HomeWizard, Shelly, Home Assistant), identiteit op een opgegeven adres
  (HTTP, Modbus/SunSpec poort 502); geen poortscan; herkenning exact model / fabrikant / alleen protocol

**Testomgeving (nieuwe startmodus naast Demo en Eigen installatie)**
- Virtuele woning op deze computer met virtuele apparaten die echte protocollen spreken: SunSpec-omvormer,
  thuisbatterij, warmtepomp en laadpaal (Modbus TCP), P1-meter (HomeWizard API v1), SG Ready-relais en
  boilerrelais (Shelly RPC), plus een onbekend Modbus-apparaat
- Zelf toevoegen via de wizard, inbedrijfstelling doorlopen tot volledige regeling (alleen voor virtuele
  apparaten), storingen inschakelen (offline, Modbus-foutcode, vertraging) op de pagina Testomgeving
- Duidelijk gemarkeerd ("Testomgeving") in de app en op het startscherm van de Windows-app

**Verbeteringen en reparaties**
- Bewerkformulier van een apparaat wordt niet meer gewist door live-updates
- Laadpaal: laadtoestand (SOC) van de auto alleen als een bron die meldt; anders een doel in kWh per sessie
- Fasebewakingstekst alleen als er echt actuele fasestromen zijn
- Warmtepomp: uitleg toont planning als planning en meting als meting; nooit een stand vragen die het apparaat
  (bijv. SG Ready zonder "blokkeren") niet toestaat
- Alleen echte meters kunnen primaire netmeter worden (niet een warmtepomp op een Modbus-driver)
- Volledige regeling op echte hardware blijft geblokkeerd zolang een profiel niet op hardware is getest

## 0.5.0 — prijzen, prognoses, accounts en cloudplatform (UNSIGNED TEST BUILD)

**Marktgegevens, contract en prognoses gescheiden**
- Prijsinstellingen per bron: bij EnergyZero geen ENTSO-E-token; ENTSO-E toont het token; "Eigen API" (Expert)
  met eigen velden; optionele reserveprijsbron (toont dan ook de velden van die bron)
- EnergyZero via het officiële endpoint `public.api.energyzero.nl/v1/prices` (`date`, `interval`,
  `energy_type`); ander adres alleen met "Aangepaste API-adressen toestaan"
- Veilige uitgaande verzoeken: alleen https, geen inloggegevens in de URL, geen interne/gereserveerde
  adressen (SSRF), geen redirects, maximale antwoordgrootte, retries bij netwerkfouten/429/5xx
- Echte verbindingstest (dekking per dag, interval, morgen beschikbaar, duur, verworpen waarden)
- Controle van prijzen (bereik, uitlijning, dubbelen), dekking per lokale dag (92/96/100 kwartieren),
  publicatiebewaking na 13:00 met herhaalde controles en melding bij vertraging (15:30)
- Statussen per kwartier: beursprijs, prognose, geschat (gat), verouderd, ontbreekt; grafieken met
  doorgetrokken/gestippelde/grijze lijn en onzekerheidsband
- Prijsprognose na de laatste beursprijs (instelbaar: aan/uit, horizon, model, betrouwbaarheidsdrempel,
  gebruik door optimizer, batterijhandel); beginners zien alleen "Prijzen na morgen voorspellen"
- Optimizer plant alleen op toegestane prijzen en laadt op prognoses niet uit het net (tenzij bewust aan)
- Energiecontract: veld "Overige voorwaarden"; laatste synchronisatie per bron blijft bewaard na herstart

**Energy Manager Cloud (nieuw, optioneel, `cloud/`)**
- Accounts: registratie met e-mailverificatie, wachtwoord of wachtwoordloos (e-maillink), reset, TOTP-MFA
  (verplicht voor platformbeheer, instelbaar per organisatie), sessies bekijken/intrekken, export, verwijderen
- Organisaties, locaties, installaties; rollen (eigenaar, beheerder, installateur, bediener, kijker,
  tijdelijke support) met fijnmazige rechten; tenant-isolatie; auditlog
- Klantportaal (Mijn installaties online/offline, koppelen, bediening op afstand, leden, privacy,
  abonnement) en platformbeheerportaal (totalen, versies, licenties, registraties, klant toevoegen, prijzen)
- Licenties en abonnementen (BASIC/PRO/BUSINESS/ENTERPRISE, prijzen niet in de code), proef, upgrade,
  downgrade, opzeggen, achterstand; verlopen licentie zet alleen cloudfuncties uit
- Koppelen met eenmalige code, uitgaande HTTPS-verbinding, node-token met rotatie, intrekken
- Opdrachten op afstand alleen met recht + locatie-instelling + licentie + lokale toestemming, en altijd
  via de lokale veiligheidscontrole
- Deployment: Dockerfile, docker-compose (Caddy TLS + PostgreSQL), DEPLOYMENT.md; PRIVACY_COMPLIANCE.md

**Lokaal**
- Instellingen → Cloud (Uitgebreid): "Inloggen bij Energy Manager Cloud" en "Deze installatie aan mijn
  account koppelen"; lokale schakelaar voor bediening op afstand

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
