# Apparatencatalogus — Energy Manager 0.6.1

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

`manufacturers.json` (schema 2) bevat **merken met modellen**, per model: status, functies, opmerking en — als het
model niet (volledig) ondersteund is — **alternatieve routes**. Verbindingsmethoden zijn géén merk:

| Route | Driver | Soorten |
|---|---|---|
| `sunspec` — Apparaat met SunSpec (Modbus) | `library.sunspec` | omvormer, hybride, slimme meter, energiemeter |
| `sgready` — SG Ready via netwerkrelais | `sgready.relay` | warmtepomp, warmtepompboiler |
| `shelly` — Netwerkrelais / slimme stekker | `shelly.switch` | boiler, stekker, relais, flexibele verbruiker |
| `homeassistant` — Via Home Assistant | `homeassistant.entities` | alle |

| Status | Merken/modellen (voorbeelden) |
|---|---|
| Ondersteund | HomeWizard P1 Meter, P1-kabel (DSMR), Shelly Gen2+ |
| Alleen uitlezen (SunSpec; begrenzing alleen met model 123) | Fronius Symo/Primo/Tauro/GEN24 + Smart Meter, SMA Sunny Boy/Tripower, SolarEdge-omvormers + meter, Kostal Plenticore/Piko IQ |
| Alleen uitlezen, community | Victron GX |
| Nog niet ondersteund + alternatief | omvormers/batterijen: Solis, GoodWe, Sungrow, SolaX, Huawei, Growatt, Deye, Enphase, APsystems, Hoymiles, Tesla Powerwall, BYD (via omvormer), Sessy, Zendure, SolarEdge Home Battery; warmtepompen: NIBE, Daikin, Vaillant, Viessmann, Panasonic, Mitsubishi Electric, Bosch/Nefit, Remeha, Itho Daalderop, Alpha Innotec, Atlantic, Intergas, Quatt, Weheat (alleen HA); warmtepompboilers en boilers: Itho Daalderop, Atlantic, Ariston, Inventum; laadpalen: Alfen, Easee, Zaptec, Wallbox, Tesla, Peblar, ABB, Smappee, Etrel, overige OCPP; auto's, airco's, ventilatie, Eastron, HomeWizard kWh-meter/Energy Socket, TP-Link, Tasmota |

De catalogus wordt bij het laden gecontroleerd: elke verwijzing naar een profiel of route moet bestaan
(`tests/test_device_library.py::test_catalog_lists_real_brands_not_protocols`).

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
| 15583 | Home Assistant (virtueel; token `testomgeving`) | Home Assistant REST API |

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
