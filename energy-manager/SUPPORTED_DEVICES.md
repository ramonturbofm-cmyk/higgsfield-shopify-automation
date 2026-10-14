# Ondersteunde apparaten — Energy Manager 0.6.0

Eerlijk overzicht. **Geen enkel apparaat is door ons op echte hardware getest.** "Volledige regeling" van een
echt apparaat blijft daarom geblokkeerd; uitlezen, schaduwmodus en (waar gedocumenteerd) beperkte regeling
kunnen wel.

## Kan uitlezen en (na inbedrijfstelling) bedienen

| Apparaat | Wat | Opmerking |
|---|---|---|
| Warmtepomp met **SG Ready**-ingang + Shelly Gen2+ met 2 relais | bedrijfsverzoek normaal / aanbevolen aan (en optioneel blokkeren / geforceerd) | geen vermogensregeling; bedrading door installateur volgens handleiding van de warmtepomp |
| **Shelly Gen2+** relais / stekker | aan/uit + vermogen | minimale aan-/uittijd instelbaar; let op de belastbaarheid |
| **SunSpec-omvormer** met model 123 (bijv. Fronius, SMA, Kostal — afhankelijk van model/firmware) | PV-vermogen; vermogensbegrenzing met terugvaltijd | alleen als het apparaat model 123 aanbiedt (bij SolarEdge loopt begrenzing niet altijd via model 123); niet op hardware geverifieerd |
| **Home Assistant**-entiteiten | wat uw Home Assistant aanbiedt | bediening alleen na bevestiging dat het apparaat niet ook rechtstreeks wordt bestuurd |

## Alleen uitlezen

| Apparaat | Wat |
|---|---|
| HomeWizard P1 Meter | netvermogen, fasen, meterstanden (API v1 en v2) |
| Slimme meter via P1-kabel (DSMR 4/5) | idem |
| SunSpec-omvormers en -meters (Fronius, SMA, SolarEdge, Kostal, overige SunSpec) | vermogen, energie, fasen |
| Victron GX | net, PV, batterij, SOC (community-registers, niet geverifieerd) |
| Willekeurig Modbus/HTTP/MQTT-apparaat (Expert) | wat u zelf uit de handleiding opgeeft |

## Nog niet ondersteund (met alternatief)

| Merk | Alternatief |
|---|---|
| Solis, GoodWe, Sungrow, SolaX, Huawei | via Home Assistant als die het apparaat kent |
| NIBE S-serie, Daikin Altherma | SG Ready via relais (als het model een SG Ready-ingang heeft) of Home Assistant |
| Weheat | — (alleen cloud-API; niets lokaal geclaimd) |
| OCPP-laadpalen | via Home Assistant |
| Shelly Gen1 | — |

## Alleen in de Testomgeving (virtueel)

SunSpec-omvormer, thuisbatterij, warmtepomp, laadpaal, P1-meter, SG Ready-relais, boilerrelais — volledig
bedienbaar, maar **alleen** als virtueel apparaat op deze computer. Zie `TEST_MORGEN_WINDOWS.md`.

## Extra hardware nodig

* SG Ready: een Shelly Gen2+ met twee potentiaalvrije contacten (bijv. Plus 2PM / Pro 2) en aansluiting door
  een installateur.
* DSMR: een P1-kabel (USB) of HomeWizard P1 Meter.
* Modbus RTU-apparaten: een USB-RS485-adapter of een Modbus-gateway (RS485 → netwerk).
