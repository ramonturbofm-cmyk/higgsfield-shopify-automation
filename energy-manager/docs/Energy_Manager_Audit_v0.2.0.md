# Energy Manager — technische audit van EnergyManagerSetup (2).exe

**Auditdatum:** 9 oktober 2026  
**Scope:** statische vergelijking met EnergyManagerSetup (1).exe; Inno Setup-payloads uitgelezen, 22 frontend-JavaScriptbestanden en CSS bekeken, ingebedde Python-module-index en een aantal Python-codeobjecten inspecteerbaar gemaakt. De Windows-GUI is **niet werkelijk gestart** en er is **geen echte hardware gekoppeld**. Dit is geen penetratietest of formele softwarecertificering.

## 1. Samenvatting

De nieuwe versie is een werkelijke ontwikkelstap, maar niet klaar voor automatische besturing van kostbare apparatuur. De frontend telt 22 JavaScriptbestanden: **5 gewijzigd, 2 nieuw en 15 ongewijzigd**. Nieuw zijn `nodes.js` en `installation.js`; gewijzigd zijn `app.js`, `dashboard.js`, `finance.js`, `planning.js`, `settings.js`. De CSS is bijgewerkt. De bestanden `devices.js`, `devicepage.js`, `automations.js`, `common.js`, `lib.js` en `prices.js` zijn ongewijzigd. Zowel changelog als versie-aanduiding blijven 0.2.0. De ingebedde Python-backend bevat **101 modules** binnen `ems`, versus **87** in de vorige versie. Nieuwe modules omvatten nodemodules, safety/priority, settlement en taxes. Dat bewijst implementatiewerk, maar **niet** dat de functies succesvol op Windows, Linux, Raspberry Pi en echte hardware zijn getest.

De twee installers zijn niet Authenticode-ondertekend (PE Security Directory 0, 0). De nieuwe installer is circa 69,67 miljoen bytes. Het SHA-256 van de nieuwe installer is `3ede35521ddfe2d1515224e6ecc6e3b4850d6c04b68c0d976bd9b30746cc5d33`.

### Wat positief is

- Eigen nodes-interface met discovery, pairing, apparaatimport en controlelease-informatie, plus corresponderende backendmodules.
- Nieuw scherm Mijn installatie en een gezondheids-/statusoverzicht.
- Dashboard heeft nu zes KPI's, een uitlegbare EMS-actie, status en energiebalansmelding.
- Planning toont meer informatie en een uitklapbare *Waarom?*-uitleg.
- Simuleer wijziging voor EMS-profielkeuze.
- Nieuwe settlement- en belastingmodule voor Nederland; financieel model breder geworden.
- Python-modules voor command safety en een control-prioriteitslaag, maar de daadwerkelijke beveiliging moet met tests bevestigd worden.

## 2. Kritieke / P0-bevindingen

**P0-01. Capability-lijst niet intrinsiek op apparaattype gefilterd.** `web/js/views/devices.js`, regels 54–60: driverlijst wordt op categorie geselecteerd, maar vervolgens worden alle `d.capabilities` weergegeven. Daardoor blijft de widget afhankelijk van de backendkwaliteit. Maak type- en profielspecifieke capability-schema's; publiceer alleen werkelijk ondersteunde functies. Koppel dezelfde schema's aan wizard, details, commissioning, optimizer en de backend-validatie.

**P0-02. Handmatige acties worden niet per concrete actie gevalideerd in de UI.** `views/devicepage.js`, circa regel 21: de volledige bediening verschijnt zodra een driver *enige* `control_`-capability bezit. Dit is niet hetzelfde als `battery_charge`, `battery_discharge`, `pv_limit` of `hp_mode` afzonderlijk ondersteunen. Buttons alleen tonen bij concrete, gevalideerde actiecapabilities. Backend moet onafhankelijk blijven blokkeren.

**P0-03. Automatiseringen staan verkeerde apparaat-actiekoppelingen toe in de editor.** `views/automations.js`, regels 54–69: apparaat en actie zijn onafhankelijke dropdowns; acties komen uit een globale `cat.device_actions`. Filter acties per gekozen apparaat en actieve capabilities; valideer server-side.

**P0-04. Control-state slechts gedeeltelijk zichtbaar.** `views/common.js`, regels 46–50: zonder actieve override staat er 'AUTO — het EMS regelt', ook wanneer een apparaat read-only of shadow kan zijn. Maak één gezaghebbende control-state: READ_ONLY, SHADOW, LIMITED, FULL, MANUAL, SAFE, OFFLINE. Geen misleidende labels.

**P0-05. Sleutelhoudende back-ups staan standaard aan.** `views/system.js`, regels 34–40: `checked: true` bij 'Inclusief sleutels'. Standaard uit; bij export van secrets versleutelde back-up met wachtwoord en een duidelijke waarschuwing.

**P0-06. Tokens in localStorage en WebSocket-URL.** `web/js/lib.js`, regels 18–20, 80–92. Bearer token leeft in localStorage en als `?token=...` in WebSocket-adres. Dat is gevoelig bij XSS, logging en diagnostiek. Gebruik waar mogelijk veilige sessies/OS-credential storage en een kortlevend WS-ticket; stel duidelijke LAN/TLS-eisen.

**P0-07. Geen zichtbaar bewijs van end-to-end commissioning/hardwareveiligheid.** Nieuwe backendmodules `ems.control.safety` en `ems.nodes.lease` bestaan; dat is positief. Zonder broncode en werkende integratietests is echter niet bewezen dat ieder commando via capabilitycheck, commissioning, leases, stroom-/SOC-grenzen en fail-safe loopt. Verplicht tests per commandotype en storingssituatie voordat Full Control wordt vrijgegeven.

**P0-08. Onbekende netmeter- en netwerkfouten mogen geen besturing op historische data opleveren.** Concrete regelaarvalidatie op stale netmetingen, node-partities en herstart moet door hardware-in-the-loop/simulatie worden aangetoond. Laat Full Control standaard geblokkeerd zolang dit onbewezen is.

## 3. Tarieven, belastingen en financiële correctheid

**P1-01. Onjuist NL-belastingtarief 2026.** In de ingebedde module `ems.tariffs.taxes` staat **€0,09157/kWh** voor NL 2026. De Belastingdienst vermeldt **€0,09161/kWh** voor de eerste elektriciteitsschijven. Maak de gegevens correct, valideer bronnen en dateer tarieven.

**P1-02. Belastingtabel beperkt tot één eerste schijftarief.** De ingebedde bronbeschrijving zegt 'first consumption bracket'. Een algemene woning/zakelijke EMS-engine moet staffels, contract-/locatietype, btw en tariefmomenten correct modelleren, of expliciet tonen dat berekening slechts eerste-schijftarief gebruikt.

**P1-03. Settlement 2026 is een *benadering*.** De ingebouwde `ems.services.settlement` vermeldt expliciet dat salderen wordt benaderd met de gemiddelde afnameprijs van de periode. Dit is niet zonder meer gelijk aan een jaarafrekening voor verschillende dynamische contracten. Scheid legal/billing-settlement, indicatieve vergelijking en daadwerkelijke optimizer-marginale prijzen.

**P1-04. Jaarvergelijking 2026/2027 houdt gedrag gelijk.** De settlementvergelijking gebruikt dezelfde gemeten import/export onder beide regels. Prima voor een *ceteris-paribus*-vergelijking, maar geen betrouwbare simulatie van gedrag met andere batterijstrategie. Toon deze beperking duidelijk.

**P1-05. Handmatige prijzen staan hard-coded op 60 minuten.** `views/settings.js`, regels 110–115: één regel per uur en `resolution_min: 60`. Maak 15/60-minuteninvoer, tijdzonevalidatie, ontbrekende intervallen, DST en CSV-import mogelijk.

**P1-06. Prijspagina gebruikt oude indexlogica bij verlopen prijslijst.** `views/prices.js`, regels 8–11: wanneer geen toekomstig prijspunt bestaat wordt `findIndex()` -1 en kan de eerste historische prijs als 'Nu' worden gepresenteerd. Vereis daadwerkelijk tijdintervaloverlap; anders 'prijs niet beschikbaar/verouderd'.

**P1-07. Intervallen en voorspellingen transparant tonen.** Markeer voor ieder prijsinterval bevestigde prijs, voorspelling en verouderd. Laat werkelijke import-, export-, marktprijs en leveranciercomponenten naast elkaar zien; gebruik nooit voorspellingen alsof ze bekend zijn.

**P1-08. Vaste kosten scheiden van marginale beslissingen.** Vaste abonnementskosten alleen voor factuurberekening, niet voor acculadingsbeslissing. Verifieer dit end-to-end tegen backendbroncode.

**P1-09. Financiële besparing moet een verklaarde baseline hebben.** `views/finance.js` toont 'zonder EMS' versus 'met EMS'. Definieer wat batterij, PV, warmtepomp en EV doen zonder EMS en onderbouw deze hypothetische baseline; toon datadekking prominent.

**P1-10. Besparingsposten mogen niet dubbeltellen.** PV, batterij, negatieve prijzen en warmte kunnen financiële waarde overlappen. Reconcileer de uitgesplitste posten tot één netto-auditbaar totaal.

**P1-11. Salderingsregeling en vergoeding vanaf 2027 verder specificeren.** De wettelijke saldering eindigt op 1 januari 2027. De regels voor terugleververgoeding en leverancierskosten zijn contractspecifiek; ontwerp dit niet als een simpele globale aan/uit-vlag.

**P1-12. Contractprijsresolutie apart van marktprijs.** Day-ahead kan 15 min zijn, terwijl contractprijzen per uur kunnen gelden. Maak onafhankelijke resoluties voor markt, factuur, optimizer en hardwarecontrole.

## 4. Nieuwe planning en verklaringen

**P1-13. Planning blijft hard-coded op 36 uur.** `views/planning.js`, regel 10. UI moet gebruikers-/site-instelling volgen, inclusief 24, 36 of 48 uur en beschikbare prijshorizon.

**P1-14. 'Per uur' selecteert nog steeds slechts elk vierde slot.** `views/planning.js`, regel 19. Dit is geen uurgemiddelde; bij 5-minutenslots wordt het zelfs een 20-minutenstap. Bouw correcte tijdaggregatie, som van kWh en gewogen gemiddelden.

**P1-15. Uitleg veronderstelt 15-minutenslots.** `views/planning.js`, regel 60: `i+49` wordt 'komende 12 uur' genoemd. Bij andere resoluties is dit fout; bepaal de horizon via timestamps.

**P1-16. De knop 'Waarom?' verzint een economisch narratief op basis van enkele veldwaarden.** `views/planning.js`, regels 64–79: uit `battery_w`, `hp_w` en prijsbewegingen wordt een oorzaak afgeleid. Een echt EMS moet redenen en actieve constraints direct uit dezelfde optimizer/run-ID krijgen. Verzin niet dat er batterijslijtage is afgewogen als die berekening niet traceerbaar is.

**P1-17. Geen bewijs dat aangekondigde winst correspondeert met het specifieke tijdslot.** Een prijsverschil is geen netto voordeel na efficiëntie, capaciteit, belastingen, slijtage en operationele beperkingen. Toon alleen een gekwantificeerde winst als het optimizerobject dit werkelijk berekent.

**P1-18. Verwarring rond energievelden `pv_w` en `pv_forecast_w`.** De planninggrafiek gebruikt `pv_w`, de tabel `pv_forecast_w`. Controleer of beide altijd in het API-schema bestaan en zo niet één contract hanteren.

**P1-19. Geplande warmtepompmodus wordt geschat uit vermogen.** `views/common.js`, circa regel 59, en nieuwe planninguitleg. Gebruik expliciete `heatpump_action`, `setpoint`, `mode` vanuit planning; niet `> referentie +300 W = boost`.

**P1-20. Geen toekomstig prijsinterval geeft misleidende extremen.** `Math.max(...[])` resulteert in `-Infinity`; de `Waarom`-uitleg kan dan een zin over komende 12 uur tonen met onzinnige prijs. Filter ontbrekende data en toon 'onvoldoende prijsinformatie'.

## 5. Apparaten, wizard, control en automatisering

**P1-21. Generieke Modbus/REST/MQTT drivers presenteren algemene functies.** Zonder geldig apparaatprofiel kan een generieke protocoladapter geen inhoudelijke warmtepomp-/batterijcapabilities claimen. Splits transportondersteuning van apparaatprofiel/mapping.

**P1-22. Laat Simple Mode merken/modellen tonen; protocols pas in Expert.** `views/devices.js`, regel 54 toont direct drivers/technische protocoltermen. Gebruik apparaatdetectie, merk, model en fallback 'Mijn apparaat staat er niet tussen'.

**P1-23. Er ontbreekt een volwaardige 'Bewerken'-flow op detailpagina.** `views/devices.js`, regels 219–228: test, identificatie, meterrol, enable/disable, verwijderen; geen zichtbaar algemeen edit van IP, poort, naam, mapping, owner, fase. Device UUID en historie behouden bij wijziging.

**P1-24. Fasekeuze voor alle apparaatsoorten.** Wizard toont onvoorwaardelijk `3P/L1/L2/L3`. Laat alleen bij elektrisch relevante aansluitingen zien en ondersteun meerdere geselecteerde fasen waar nodig.

**P1-25. 'Toch toevoegen' bij mislukte test.** Hernoem naar 'Opslaan als niet-verbonden'; vermeld dat regelen niet is toegestaan totdat test/commissioning slaagt.

**P1-26. HomeWizard standaard als primary aanvinken.** `views/devices.js`, circa regel 155. Controleer of een primaire netmeter al bestaat en vraag expliciet bevestiging van rolwissel.

**P1-27. Commissioningdetail gebruikt een brede, vaste set batterij-, PV-, EV- en HP-velden.** Maak per apparaatprofiel een relevante, inzichtelijke inbedrijfstelling. Zonder echte role/schema-data blijft het verwarrend.

**P1-28. Volledige controle vrijgeven met simpele browser-confirm.** Maak commissioning-rapport, limits, testuitkomsten, hardwareverificatie en expliciete gebruikersbevestiging verplicht.

**P1-29. Expertinstellingen worden te letterlijk getoond.** Onbekende JSON-keys, `hp_power_w`, `battery_soc_pct`, raw device diagnostics horen niet op de gewone pagina. Metadata: gebruikersnaam, eenheid, toelichting, bereik en actuele status.

**P1-30. Acties en limieten niet als inputrange doorgezet.** `views/common.js`, regels 32–38: numerieke overrides zonder apparaatspecifieke min/max. Maak veilige UI-ranges en server-side check; niet alleen frontend.

**P1-31. EV-SOC niet veronderstellen.** Laadpaal kan vaak wel stroom meten maar niet voertuig-SOC. Geef alternatief: gewenste kWh/vertrektijd als auto-SOC niet beschikbaar is.

## 6. Windows/Linux/Raspberry Pi en nodes

**P1-32. Nieuwe Nodes UI is een sterke stap.** `views/nodes.js` biedt device import, pairing code, discover, ownership/lease-UI. Backend bevat `ems.nodes.*` en `ems.api.routes_nodes`. Functioneel moet dit getest worden met echte processen op meerdere machines.

**P1-33. Niet aantoonbaar dat Linux/Raspberry Pi-only installer volledig werkt.** Windows-installer bevat Python Windows-exes; er is geen Linux/Pi-deployment als daadwerkelijk gestarte omgeving getest. Eist aparte Linux ARM64/x86-64 builds, autonome browserbediening, systemd/Docker met persistente volumes.

**P1-34. Gedeelde site maar geen zichtbare multi-site-UI.** Nodes worden nu voor één installatie getoond. Later meerdere sites/locaties: zelfstandige contracten, primary controller, devices, historie en gebruikersrechten.

**P1-35. Pairing en node import vereisen robuuste beveiliging.** UI biedt adres en 6-cijferige code. Backend heeft PairingManager (10 minuten, vijf pogingen) en LeaseManager (epoch/TTL), positief. Voer netwerkpartition-, race-, replay-, spoofing-, lossy-LAN- en duplicate-commandtests uit.

**P1-36. Remote device capabilities zijn ruwe labels.** `views/nodes.js`, regel 54: `d.capabilities.join(', ')`. Maak per categorie een menselijke samenvatting en gedetailleerd overzicht onder Expert.

**P1-37. Een remote node kan primair worden zonder zichtbaar conflictbericht.** `views/nodes.js`, regels 55–59: 'Als netmeter' import met `primary_grid_meter:true`. Gebruiker expliciet laten kiezen wanneer oude primaire meter aanwezig is.

**P1-38. Rollen en controller ownership uitleg verbeteren.** 'gateway (deze node regelt)' kan verwarring oproepen; onderscheid meet-/commandogateway van centrale beslisser. Begrippen 'controller lease', 'epoch' alleen Expert.

**P1-39. Schakelen tussen nodes/sites is niet hetzelfde als meerdere zelfstandige controllers.** Toon altijd welke site op welke node primair draait, welke owner een device heeft, welke controller rechten bezit en wat gebeurt bij uitval.

## 7. Dashboard, UX, grafieken en toegankelijkheid

**P2-01. Nieuwe zes KPI's zijn nuttig, maar batterij/PV kaarten zijn niet capability-dynamisch.** Ook als batterij ontbreekt kan vaste kaart zichtbaar blijven. Verschijn alleen indien geïnstalleerd of duidelijk 'nog niet gekoppeld'.

**P2-02. Energieflow blijft hard-coded.** `views/dashboard.js` kent alleen PV/Net/Huis/Batterij/HP/EV. Voor universeel systeem ook boiler, airco, meerdere batterijen/omvormers en submeter, zonder dubbele telling.

**P2-03. Energiebalans krijgt een waarschuwing; verifieer de bron.** Positief is de foutmelding bij `balance.ok===false`. Leg uit welke gemeten en welke berekende waarden in de balans zitten, en hoe groot onzekerheid/marge is.

**P2-04. Status is beter, maar kwaliteit 'STALE' wordt nog als numerieke waarde getoond.** `views/dashboard.js` sluit STALE niet uit van `na` en toont het slechts als annotatie. Voor grid/security kan dat te subtiel zijn. Maak tijdigheid centraal zichtbaar.

**P2-05. 'Wat doet het EMS nu?' is beter, maar status en actie moet eenduidig zijn.** `outcome` kan rejected/shadow/dry_run/not_commissioned zijn; een 'wat doet' tekst mag niet de indruk wekken dat een commando is uitgevoerd als dat niet zo is.

**P2-06. Simple/Advanced/Expert nog niet globaal.** `state.level` wordt hoofdzakelijk in `settings.js` benut. Apperaatwizard, sidebar, notifications, commissioning, nodes tonen nog Expert-termen.

**P2-07. Navigatie en dashboard dynamisch aan gebruiker aanpassen.** Alleen schermen tonen die relevant zijn voor gekoppelde apparaten, voor Simple met toegankelijke taal.

**P2-08. Tijdzone/historiegrafieken.** `views/prices.js` gebruikt `toLocaleString('nl-NL')` zonder expliciete site-timezone, en front-end `Date.now()` kan afwijken van serverklok; DST-tests essentieel.

**P2-09. Grafiekaggregratie en pieken.** Niet ieder vierde punt of iedere n-de meting tonen; aggregeer W/kWh correct over echte intervalduur en bewaar min/max/avg per bucket.

**P2-10. Foutmeldingen en loadingstates.** REST `fetch()` zonder eigen timeout (`lib.js`, regel 61), diagnostiek beperkt; voeg cancel, retries waar veilig, offline-state en uniforme foutmeldingen toe.

**P2-11. Toegankelijkheid.** Niet enkel kleuren, voldoende contrast/aria-teksten, toetsenbordbediening, kleine schermen, reduced-motion en prijs-/energiegegevens ook als leesbare tabel.

**P2-12. Update/version hygiene.** Beide changelogs tonen 0.2.0; nieuw project heeft aantoonbaar extra functionaliteit. Uniek versienummer + release notes + migraties + installer-upgrade-/rollbacktests.

**P2-13. Windows installer is nog unsigned.** Onderteken pas wanneer geschikt code-signingcertificaat beschikbaar is; presenteer tot die tijd als unsigned testbuild en leg waarschuwingen uit.

## 8. Noodzakelijke acceptatietests voor Claude Code

### A. Apparaten (P0)
1. Maak een Mock HeatPump-driver met alleen `read_flow_temperature` en `set_room_setpoint`; controleer dat nergens batterij/EV-functies verschijnen en dat een PV_LIMIT-actie bij deze driver server-side wordt geweigerd.
2. Maak een Mock Battery-driver met alleen SOC en een maximum-SOC-setter; de UI mag géén directe Laden/Ontladen-knoppen aanbieden. De backend weigert die commando's.
3. Onbekend Modbus-apparaat zonder registermapping heeft géén geclaimde toepassingsspecifieke capabilities.
4. Automatisering kan geen onverenigbare device-action-combinatie opslaan, ook niet via directe API-call.
5. Shadow/read-only apparaten kunnen geen write uitvoeren, ongeacht handmatige override of node-import.
6. Bij ontbrekende gridmeter en fasegegevens worden zero-export en faseloadbalancing geweigerd.

### B. Financieel en tijd (P0/P1)
7. Unit tests voor 2026 belastingstaffels, correct eerste tarief €0,09161 en contractafhankelijkheid.
8. Test 2026/2027 saldering rond jaargrens en negatieve terugleververgoedingen met controleerbare contractgevallen.
9. Test 92, 96 en 100 kwartieren, DST-overgangen en 60-minutencontract op 15-minutenmarkt.
10. Test dat de `Nu`-prijs niet uit verouderde data komt wanneer laatste interval verlopen is.
11. Test import/export/BTW/fixed-costs en break-even spread van batterij inclusief rendementsverlies en slijtage.
12. Backtest-baseline reproduceerbaar, met datadekking en zonder dubbeltellen.

### C. Nodes en 24/7 werking (P0/P1)
13. Windows-only: service draait na sluiten GUI en na herstart.
14. Linux-only en Raspberry-Pi-only: hele installatie werkt met browser, persistentie en automatische herstart.
15. Gedistribueerd: Linux controller, Pi device gateway, Windows dashboard; read, command, ack, auditlog werken end-to-end.
16. WAN/LAN-partitie, mislukte pairing, dubbele controller, verlopen lease, oude epoch en node-herstart leiden niet tot double control.
17. HomeWizard P1 lokaal verbinden, als primaire netmeter instellen en pas daarna shadow mode/zero-export-procedure activeren.
18. Werkelijke hardwarecommando's pas na expliciete commissioning en documentatievalidatie.

### D. UI-releasetest (P1/P2)
19. Controleer alle categorieën op irrelevante capabilities en ongeldige actieknoppen.
20. Controleer alle instellingenvelden op schema-min/max, eenheden, hulptekst en veilige defaults.
21. Simuleer langzame/offline API en observeer duidelijke timeout/foutmelding in alle schermen.
22. Test plannen met optimizer-intervallen van 5, 15 en 60 minuten; 'Per uur' moet echte aggregaties tonen.
23. Test elk drukbaar scherm op 1366×768, 1920×1080, touch en keyboard.
24. Maak installer voor schone Windows-VM; test installatie, update, downgradepreventie, uninstall, config behoud en demo mode.

## 9. Wat ik Claude eerst zou laten uitvoeren

**Sprint 1 — noodzakelijk vóór echte hardwarebesturing:** herbouw typegebonden device/action/capabilityschema's van backend tot frontend; beperk generieke drivers tot geconfigureerde capabilities; per-command checks en commissioning; schrijf automatische fouten- en misbruiktests; backup secrets standaard uit; verbeter tokenopslag en WS-auth.

**Sprint 2 — geld en voorspellen correct:** corrigeer belastingen, maak fiscale staffels; settlement contractafhankelijk en transparant; kwartierprijzen incl. DST, correcte uurgemiddelden; echte optimizer-run-redenen in plaats van verklaringen die frontend gokt.

**Sprint 3 — standalone/distributed harden:** Windows-only, Pi-only, Linux-only en combinaties fysiek/VM testen; leases, ownership, health en fallback; node-koppeling toegankelijker maken.

**Sprint 4 — productervaring:** apparaat toevoegen via merk/model/discovery, UI Simple echt app-breed; dashboard dynamic; financiële baseline expliciet; afgeschermde expertinstellingen, validatie, hulpteksten, update- en installerbeheer.

### Release-gate
De app mag pas als **hardware-control-testrelease** worden beschouwd wanneer Sprint 1 en de veiligheids-/netwerkfall-backtests groen zijn. Tot die tijd: Demo, Read-only of Shadow Mode. Een installerbestand of succesvol compileren is geen bewijs van veilige controle van apparaten.

---

### Bewijsbasis
Front-endbestanden: `server/_internal/ems/web/js/` met views. Backend: modulelijst uit de PyInstaller `PYZ` van `EnergyManagerServer.exe`, plus inspectie van marshal-codeobjectnamen en constanten voor `ems.services.settlement`, `ems.tariffs.taxes`, `ems.control.safety`, `ems.nodes.lease`, `ems.nodes.pairing`. Referentie voor 2026-belasting: Belastingdienst, officiële energiebelastingtabel. Referentie salderen: ACM ConsuWijzer en Rijksoverheid.
