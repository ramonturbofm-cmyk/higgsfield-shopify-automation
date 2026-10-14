# Apparaatprofiel-schema (versie 1) — Energy Manager 0.6.0

Een apparaatprofiel beschrijft één merk/model voor een bestaande driver: welke registers of velden worden
gelezen, welke functies (eventueel) beschreven mogen worden, met welke grenzen, en **waar dat vandaan komt**.
Profielen zijn JSON, bevatten **geen code** en worden bij het laden volledig gevalideerd
(`backend/ems/devices/library/schema.py`, Pydantic, onbekende velden worden geweigerd).

Ingebouwde profielen: `backend/ems/devices/library/profiles/*.json`.
Eigen/community-profielen: `<datamap>/profiles/*.json` (importeren via `POST /api/v1/library/profiles/import`).

## Drie lagen

1. **Transport** — Modbus TCP / RTU (pymodbus), HTTP.
2. **Driver** — `library.modbus` (registers uit het profiel), `library.sunspec` (SunSpec-modelketen),
   `homewizard.p1`, `shelly.switch`, `sgready.relay`, `homeassistant.entities`, …
3. **Modelprofiel** — dit document.

## Velden

| Veld | Verplicht | Betekenis |
|---|---|---|
| `schema_version` | nee (1) | nieuwere schema's dan de software worden geweigerd |
| `profile_id` | ja | `^[a-z0-9][a-z0-9_.-]{2,80}$`, bijv. `fronius.gen24` |
| `driver_id` | ja | driver die het profiel uitvoert |
| `manufacturer`, `model`, `model_aliases` | ja / ja / nee | zoals de gebruiker het kent |
| `hardware_revision`, `firmware_min`, `firmware_max` | nee | geteste/gedocumenteerde reeks |
| `device_category`, `supported_roles` | ja / nee | bepaalt welke capabilities mogen (type-schema) |
| `protocol` | ja | `modbus_tcp`, `modbus_rtu`, `sunspec`, `http`, … |
| `connection_parameters` | nee | standaardpoort, unit-ID, … (de gebruiker kan ze overschrijven) |
| `discovery_signatures` | nee | `modbus_string` (register + regex), `sunspec` (Mn/Md-regex), `http_json`, `mdns` |
| `points` | nee | uit te lezen waarden (zie hieronder) |
| `capabilities` | nee | extra gedeclareerde leesfuncties; moeten bij de soort passen |
| `writable_controls` | nee | schrijfbare functies (zie hieronder) |
| `release_writes` | nee | schrijfacties die het apparaat teruggeven aan zijn eigen regeling |
| `safety_constraints` | nee | bijv. minimale tijd tussen opdrachten |
| `command_timeouts`, `reconnect_policy`, `readback_verification` | nee | standaard 5 s, backoff 2/10/30 s, terugleescontrole aan |
| `documentation_sources` | **ja** | lijst `{title, url, license, retrieved}` — zonder bron geen profiel |
| `integration_version` | nee | versie van dit profiel |
| `test_status` | ja | `HARDWARE_TESTED`, `SIMULATOR_TESTED`, `UNVERIFIED`, `COMMUNITY`, `EXPERIMENTAL` |
| `known_limitations`, `requirements` | nee | getoond in de wizard |
| `default_params` | nee | datablad-waarden die de wizard vooraf invult (capaciteit, maximale stroom, …) |
| `simulator_only` | nee | alleen voor de Testomgeving |
| `source` | nee | `builtin`, `community`, `user` (gezet door de import) |

### Point (lezen)

`metric` (EMS-metriek) of `name` (diagnose), `table` (`holding`, `input`, `coil`, `discrete`), `address`,
`type` (`uint16`, `int16`, `enum16`, `bitfield16`, `uint32`, `int32`, `float32`, `bitfield32`, `acc32`,
`uint64`, `int64`, `float64`, `acc64`, `string`), `size` (strings), `byte_order`/`word_order` (`big`/`little`),
`scale`, `sf` (macht van 10), `offset`, `invert`, `enum`, `unit`, `combine` (`replace`/`sum`), of `path` (HTTP).
NaN en "niet geïmplementeerd" worden **geen** getal.

### ControlSpec (schrijven)

`action` (bijv. `hp_mode`, `battery_charge`, `ev_current`, `pv_limit`), `table`, `address`, `type`,
`scale`/`sf`/`offset`, `min`/`max`, `values` (waarde → register, bijv. `{"normal": 1, "boost": 2}`),
`fixed_value`, `readback` (Point; zonder `readback` wordt hetzelfde register teruggelezen), `expires_s` + `expiry_register` (apparaatwatchdog), `note`, en
`status`:

| status | schrijven? |
|---|---|
| `OFFICIAL_DOCUMENTATION` | ja, na inbedrijfstelling |
| `SIMULATOR` | alleen in `simulator_only`-profielen (Testomgeving) |
| `COMMUNITY`, `UNVERIFIED` | **nee** — getoond als "niet geverifieerd" |

## Validatieregels

* `documentation_sources` mag niet leeg zijn.
* Capabilities en controls moeten passen bij `device_category` + `supported_roles`.
* `SIMULATOR`-status alleen bij `simulator_only: true`.
* Profielen met `source` `community` of `user`: alle schrijfbare controls worden `UNVERIFIED` en een
  claim `HARDWARE_TESTED` wordt `UNVERIFIED`. Een bestand kan zichzelf dus nooit vrijgeven.
* Een ingebouwd profiel kan niet worden overschreven of verwijderd.

## Versiebeheer

Importeren van een profiel met een bestaand `profile_id` bewaart de vorige versie in `profiles/.history/`;
`POST /library/profiles/{id}/rollback` zet die terug. Krijgt een profiel **nieuwe** schrijffuncties, dan gaan
apparaten die het gebruiken en verder dan schaduwmodus stonden terug naar **schaduwmodus** (opnieuw in
bedrijf stellen).

## Voorbeeld (ingekort)

```json
{
  "profile_id": "energymanager.lab.heat_pump",
  "driver_id": "library.modbus",
  "manufacturer": "Energy Manager Testomgeving",
  "model": "Virtuele warmtepomp (Modbus TCP)",
  "device_category": "heat_pump",
  "protocol": "modbus_tcp",
  "connection_parameters": {"port": 15503, "unit_id": 1},
  "points": [
    {"metric": "hp_power_w", "table": "input", "address": 0, "type": "uint16"},
    {"metric": "indoor_temp_c", "table": "input", "address": 2, "type": "int16", "scale": 0.1}
  ],
  "writable_controls": [
    {"action": "hp_mode", "address": 100, "type": "uint16",
     "values": {"normal": 1, "boost": 2, "eco": 3}, "status": "SIMULATOR",
     "expires_s": 900, "expiry_register": 110}
  ],
  "release_writes": [{"address": 100, "value": 1}],
  "documentation_sources": [{"title": "Registerkaart virtuele apparaten (DEVICE_CATALOG.md, sectie Testomgeving)",
                             "license": "eigen documentatie"}],
  "test_status": "SIMULATOR_TESTED",
  "simulator_only": true
}
```
