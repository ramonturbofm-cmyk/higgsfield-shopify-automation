# Apparatencatalogus — Energy Manager 0.6.0

Technisch overzicht van de lokale apparatenbibliotheek: welke merk/model-regels er zijn, welk profiel en
welke driver ze gebruiken, en de bron. Voor gebruikers: zie `SUPPORTED_DEVICES.md`.

Bronbestanden: `backend/ems/devices/library/manufacturers.json` (merk/model-lijst in de wizard) en
`backend/ems/devices/library/profiles/*.json` (profielen, zie `DEVICE_PROFILE_SCHEMA.md`).

## Profielen

| profile_id | Driver | Soort | Teststatus | Bron |
|---|---|---|---|---|
| `sunspec.inverter` | `library.sunspec` | omvormer (+ hybride) | UNVERIFIED | SunSpec Information Model (officiële JSON-modellen, Apache-2.0, `integrations/sunspec/models/`) |
| `sunspec.meter` | `library.sunspec` | slimme meter / energiemeter | UNVERIFIED | idem (modellen 201–204) |
| `victron.gx` | `library.modbus` | hybride/batterij/PV/meter | COMMUNITY (alleen lezen) | evcc-template Victron (MIT), registers 808–843 en 820–822 |
| `energymanager.lab.sunspec_inverter` | `library.sunspec` | omvormer | SIMULATOR_TESTED | virtueel apparaat, officiële SunSpec-modellen 1/103/120/123 |
| `energymanager.lab.heat_pump` | `library.modbus` | warmtepomp | SIMULATOR_TESTED | registerkaart hieronder |
| `energymanager.lab.battery` | `library.modbus` | thuisbatterij | SIMULATOR_TESTED | registerkaart hieronder |
| `energymanager.lab.ev_charger` | `library.modbus` | laadpaal | SIMULATOR_TESTED | registerkaart hieronder |

Overige koppelingen zonder profiel (eigen driver): `homewizard.p1` (HomeWizard Local API v1/v2),
`dsmr.p1` (DSMR 4/5 P1-poort), `shelly.switch` (Shelly Gen2+ RPC), `sgready.relay` (SG Ready via 2 Shelly-relais),
`homeassistant.entities` (Home Assistant REST API).

## Merk/model-lijst (wizard)

| Merk | Model | Status in de wizard | Koppeling |
|---|---|---|---|
| HomeWizard | P1 Meter (HWE-P1) | Ondersteund | `homewizard.p1` |
| Slimme meter (DSMR) | P1-kabel (USB) | Ondersteund | `dsmr.p1` |
| Shelly | Gen2+ relais/stekker | Ondersteund | `shelly.switch` |
| Shelly | Gen2+ met 2 relais als SG Ready-schakelaar | Via SG Ready | `sgready.relay` |
| Shelly | Gen1 | Nog niet ondersteund | — |
| SG Ready (alle merken) | Warmtepomp met SG Ready-ingang + netwerkrelais | Via SG Ready | `sgready.relay` |
| Fronius | Symo / Primo / Tauro / GEN24; Smart Meter | Alleen uitlezen (SunSpec) | `sunspec.inverter` / `sunspec.meter` |
| SMA | Sunny Boy / Tripower (SunSpec) | Alleen uitlezen | `sunspec.inverter` |
| SolarEdge | Omvormers; meter via omvormer | Alleen uitlezen | `sunspec.inverter` / `sunspec.meter` |
| Kostal | Plenticore / Piko IQ | Alleen uitlezen | `sunspec.inverter` |
| SunSpec-compatibel | Omvormer / meter | Alleen uitlezen | `sunspec.*` |
| Victron Energy | GX (Cerbo, Venus, Ekrano) | Alleen uitlezen (community) | `victron.gx` |
| Home Assistant | Entiteiten uit uw eigen HA | Via Home Assistant | `homeassistant.entities` |
| Solis, GoodWe, Sungrow, SolaX, Huawei | alle | Nog niet ondersteund | alternatief: Home Assistant |
| NIBE | S-serie | Nog niet ondersteund | alternatief: SG Ready / HA |
| Daikin | Altherma | Nog niet ondersteund | alternatief: SG Ready / HA |
| Weheat | Blackbird / Sparrow / Flint | Nog niet ondersteund (alleen cloud) | — |
| Laadpalen (OCPP) | OCPP 1.6J | Nog niet ondersteund | alternatief: HA |

"Alleen uitlezen" voor SunSpec-omvormers: als het apparaat model 123 aanbiedt kan vermogensbegrenzing na
inbedrijfstelling tot **beperkte regeling**; volledige regeling blijft geblokkeerd tot een hardwaretest.

## Licenties

* SunSpec-modellen: Apache-2.0 (`backend/ems/integrations/sunspec/models/LICENSE`), ongewijzigd overgenomen.
* Victron-registers: uit evcc (MIT); alleen registernummers en betekenis, geen code.
* HomeWizard, Shelly, Home Assistant: openbare API-documentatie van de fabrikant/het project; geen code overgenomen.

## Testomgeving — registerkaart virtuele apparaten

Deze apparaten bestaan alleen op 127.0.0.1 in de startmodus Testomgeving (`ems serve --lab`). Poorten zijn
vast (verschuifbaar met `EMS_LAB_PORT_OFFSET`). Unit-ID 1. Identiteit (alle Modbus-apparaten): holding 0–7
fabrikant, 8–15 model, 16–23 serienummer, 24–27 firmware (ASCII, 2 tekens per register).

| Poort | Apparaat | Protocol |
|---|---|---|
| 15502 | SunSpec-omvormer 5 kW (modellen 1, 103, 120, 123; basis 40000) | Modbus TCP |
| 15503 | Warmtepomp | Modbus TCP |
| 15504 | Thuisbatterij 10 kWh | Modbus TCP |
| 15505 | Laadpaal 11 kW | Modbus TCP |
| 15599 | Onbekend Modbus-apparaat (geen identiteit) | Modbus TCP |
| 15580 | P1-meter | HomeWizard API v1 (`/api`, `/api/v1/data`) |
| 15581 | 2-relaismodule voor SG Ready | Shelly Gen2 RPC |
| 15582 | 1-relaismodule boiler 2 kW | Shelly Gen2 RPC |

**Warmtepomp (15503)** — input: 0 elektrisch vermogen W · 1 thermisch W · 2 binnen ×0,1 °C (int16) · 3 buiten
×0,1 · 4 aanvoer ×0,1 · 5 retour ×0,1 · 6 tapwater ×0,1 · 7 COP ×0,01 · 8 compressor aan · 9 frequentie Hz ·
10 ontdooien · 11 foutcode. Holding: 100 modus (1 normaal, 2 boost, 3 eco) · 101 setpoint ×0,1 °C
(int16, 15–24) · 110 watchdog s (standaard 900; daarna terug naar normaal).

**Thuisbatterij (15504)** — input: 0 vermogen W (int16, + laden) · 1 SOC ×0,1 % · 2 temperatuur ×0,1 °C ·
3 max. SOC % · 4–5 capaciteit Wh (uint32) · 6 BMS-status. Holding: 100 modus (0 automatisch, 1 stand-by) ·
101 vermogen W (int16; + laden, − ontladen, max. 5000) · 102 max. SOC (50–100) · 110 watchdog s.

**Laadpaal (15505)** — input: 0 vermogen W · 1 auto aangesloten · 2 sessie-energie ×0,01 kWh. Holding:
100 stroomlimiet ×0,1 A (0–16) · 110 watchdog s. Geen SOC van de auto.

**SG Ready-relais (15581)** — kanaal 0 = contact 1, kanaal 1 = contact 2; stand 1 (1,0) blokkeren, 2 (0,0)
normaal, 3 (0,1) aanbevolen aan, 4 (1,1) geforceerd; `toggle_after` als watchdog.

**Storingen** — per apparaat via de pagina Testomgeving of `PUT /api/v1/lab/faults/{key}`:
`offline`, `exception` (Modbus-foutcode), `delay_s` (vertraging).
