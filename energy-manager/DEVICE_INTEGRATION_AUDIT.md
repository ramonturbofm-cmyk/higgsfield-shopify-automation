# Device-integratie-audit — Energy Manager 0.6.0

Inspectie van de code (stand 0.5.0) op de 12 eerdere bevindingen, wat er in 0.6.0 aan gedaan is, en de status
per integratie. Niets in deze release is op echte hardware getest.

**Statussen**: COMPLETE · PARTIAL · READ ONLY · SIMULATOR TESTED · HARDWARE TESTED · NOT IMPLEMENTED ·
UNVERIFIED · BROKEN.

## 1. De 12 bevindingen

| # | Bevinding (0.5.0) | Gevonden in de code | Wat er in 0.6.0 is gedaan | Status |
|---|---|---|---|---|
| 1 | Generieke drivers domineren | De wizard toonde per soort vooral `generic.modbus_tcp/http_json/mqtt`; merkdrivers alleen HomeWizard/DSMR | Nieuwe apparatenbibliotheek (merk → model → profiel); generieke drivers alleen onder "Handmatig (expert)", met waarschuwing "alleen uitlezen" | COMPLETE |
| 2 | Wizard niet merk/model-gericht | `addWizard` koos "soort → driver" | Nieuwe wizard: 15 soorten → automatisch zoeken / merk en model / gateway / handmatig → gegevens → test + functierapport → opslaan (`web/js/views/addwizard.js`) | COMPLETE (SIMULATOR TESTED, browsertest 9/9) |
| 3 | Catalogus onbruikbaar voor consumenten | Lijst van drivers met technische namen | `manufacturers.json`: 34 merk/model-regels met eerlijke status (ondersteund, alleen uitlezen, via SG Ready, via Home Assistant, nog niet ondersteund + alternatief); zoekveld | COMPLETE |
| 4 | Generieke protocollen alleen-lezen | Klopt, en terecht (geen documentatie = geen schrijven) | Zo gelaten. Schrijven kan alleen via een gevalideerd profiel met gedocumenteerde schrijfregisters | COMPLETE (bewust READ ONLY) |
| 5 | Fabrikantfuncties onduidelijk | Capabilities per driver, niet per apparaat | Capabilityrapport per apparaat (`devices/capreport.py`): SUPPORTED_READ / SUPPORTED_WRITE / REQUIRES_CONFIGURATION / REQUIRES_COMMISSIONING / NOT_SUPPORTED / UNVERIFIED, met bereik/standen; in de wizard en op de apparaatpagina | COMPLETE |
| 6 | Bewerkformulier verdwijnt door live-updates | `editDevice` werd in de container gezet die bij elke live-update opnieuw getekend werd (`box.replaceChildren`) | Formulier in een eigen `edit-slot` buiten het hertekende deel; browsertest: tekst blijft 7 s staan en wordt opgeslagen | COMPLETE |
| 7 | EV-pagina gaat uit van SOC | `ev.js` toonde altijd "Auto-SOC" en "Gewenste SOC"; optimizer gebruikte al kWh als SOC ontbrak, maar de uitleg noemde "%" | SOC alleen als een bron die meldt; anders "Gewenste energie (kWh)" en "Geladen deze sessie"; optimizer trekt al geladen energie af; uitleg toont kWh | COMPLETE |
| 8 | Fasebewaking geclaimd | Vaste tekst "Fasebewaking begrenst altijd …" | Tekst volgt `features.phase_balancing` (alleen met actuele fasestromen van de primaire netmeter); backend begrensde al alleen dan | COMPLETE |
| 9 | Geheime velden op naam herkend | `_is_secret_key` (regex op pass/token/secret) in API en UI | Driver-metadata `"secret": True`; API (`secret_keys`) en UI (`deviceform.js`) gebruiken alleen die; versleuteld in de SecretStore, gemaskeerd in de API | COMPLETE |
| 10 | Warmtepompuitleg afgeleid uit vermogen | Uitleg "voorverwarmen/zuiniger" uit gepland vs. referentievermogen, gepresenteerd als feit | Uitleg labelt planning als planning ("Planning (schatting)"), toont gemeten vermogen apart; controller vraagt nooit een stand die het apparaat niet toestaat (bijv. SG Ready zonder "blokkeren"); SG Ready-rapport: "geen vermogensregeling" | COMPLETE |
| 11 | Capability-filtering en automatiseringsfixes behouden | Aanwezig sinds 0.4.0 (`can_execute`, type-schema ∩ driver) | Ongewijzigd; geldt ook voor de nieuwe drivers (profielcapabilities ∩ type-schema) | COMPLETE |
| 12 | Inbedrijfstelling/veiligheid moet met nieuwe drivers werken | Volledige regeling eiste `manifest.verified` | Per apparaat: `write_documented()`, `hardware_verified()`, `simulated_target()`; volledige regeling alleen voor HARDWARE_TESTED-profielen of virtuele apparaten van de Testomgeving; de simulatie-poort blokkeert echte hardware in Demo/Testomgeving | COMPLETE (SIMULATOR TESTED) |

Extra gevonden en opgelost: een warmtepomp op de Modbus-bibliotheekdriver kon als **primaire netmeter**
worden aangeboden (driver had `grid_meter_kind`); nu alleen apparaten van de soort slimme meter / energiemeter.

## 2. Status per integratie

| Integratie | Uitlezen | Bedienen | Status |
|---|---|---|---|
| HomeWizard P1 (API v1/v2) | ja | — | COMPLETE, SIMULATOR TESTED (v1 tegen de virtuele P1; v2 tegen de nagebootste API in de tests), niet HARDWARE TESTED |
| DSMR P1-kabel | ja | — | COMPLETE (getest met DSMR-telegrammen), niet HARDWARE TESTED |
| SunSpec (omvormer/meter/opslag) | ja | vermogensbegrenzing (model 123, met terugvaltijd) | COMPLETE, SIMULATOR TESTED (virtuele SunSpec-omvormer met de officiële modellen); UNVERIFIED op echte merken |
| Fronius / SMA / SolarEdge / Kostal | via SunSpec | begrenzing alleen als model 123 er is; volledige regeling geblokkeerd | READ ONLY / UNVERIFIED |
| Victron GX | ja (community-registers) | nee | READ ONLY, UNVERIFIED (COMMUNITY) |
| Modbus-profieldriver (TCP/RTU) | ja | volgens profiel | COMPLETE, SIMULATOR TESTED; RTU niet getest (geen seriële hardware) |
| Shelly Gen2+ (schakelen) | ja | aan/uit met minimale tijden | COMPLETE, SIMULATOR TESTED (virtuele Shelly-RPC) |
| SG Ready via relais | stand | bedrijfsverzoek 1–4 (standaard 2/3) | COMPLETE, SIMULATOR TESTED |
| Home Assistant-brug | ja | na bevestiging exclusieve regeling | PARTIAL: alleen getest met een nagebootste API, niet tegen een echte Home Assistant |
| Generiek Modbus TCP / HTTP-JSON / MQTT | ja (eigen mapping) | nee | READ ONLY (bewust) |
| OCPP 1.6J | — | — | NOT IMPLEMENTED |
| WebSocket (generiek) | — | — | NOT IMPLEMENTED |
| NIBE S-serie, Daikin Altherma, Solis, GoodWe, Sungrow, SolaX, Huawei | — | — | NOT IMPLEMENTED (geen geverifieerde documentatie in deze release; alternatief SG Ready / Home Assistant) |
| Weheat | — | — | NOT IMPLEMENTED (alleen cloud-API bekend; niets lokaal geclaimd) |
| Demo-drivers (`mock.*`) | ja | ja | SIMULATOR TESTED (alleen Demo) |
| Echte hardware (welke dan ook) | — | — | **niet HARDWARE TESTED** |

## 3. Bewuste keuzes

* **Geen verzonnen registers, endpoints, topics of commando's.** Merken zonder bron die wij konden
  verifiëren staan als "nog niet ondersteund" in de lijst, met een alternatief.
* **Community-/eigen profielen** zijn leesbaar maar zetten nooit schrijven of volledige regeling aan; een
  claim "HARDWARE_TESTED" in een geïmporteerd bestand wordt teruggezet naar UNVERIFIED.
* **Discovery** doet geen poortscan: alleen mDNS en identiteitsvragen op adressen die de gebruiker opgeeft
  (poorten 80 en 502).
* **Nodes**: zoeken op andere nodes is nog niet gebouwd (alleen lokaal).
