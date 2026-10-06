# Device Integration Guide

Hoe voeg je een nieuw apparaat (driver) toe aan Energy Manager?

## 0. De gouden regel: verzin niets

Een driver bevat **uitsluitend** registers, endpoints, MQTT-topics en commando's uit
**officiële documentatie** van de fabrikant of een gepubliceerde standaard (bijv. SunSpec,
DSMR P1 Companion Standard, OCPP, EEBus, SG Ready).

* Leg de bron vast in `manifest.documentation` (titel, versie, URL/document-id).
* Zet `verified=True` pas nadat de driver tegen echte hardware is getest (model + firmware noteren).
* Onbekend of niet gedocumenteerd? Dan **geen** driver gokken. Gebruik `mock.*` (alleen Demo Mode), of
  een generieke driver (`generic.modbus_tcp`, `generic.http_json`, `generic.mqtt`, §8) waarin de
  *installateur* zelf de mapping invult vanuit de eigen documentatie van het apparaat.
* Lokale interfaces hebben voorkeur boven cloud-API's.

## 1. Begrippen

| Begrip | Betekenis |
|---|---|
| `Metric` | gestandaardiseerde meetwaarde, bijv. `battery_soc_pct`, `grid_current_l1_a` |
| `Capability` | wat een driver kan: `read_battery_soc`, `control_battery_mode`, ... |
| `Command` | opdracht van het EMS: `BATTERY_CHARGE 3000`, `EV_CURRENT 10`, `HP_MODE "boost"`, ... |
| `DriverManifest` | beschrijving van de driver voor registry en apparaatwizard |
| `release_control()` | apparaat terug naar zijn **eigen veilige regeling** |

Tekenconventies (verplicht): netvermogen + = afname; batterij + = laden; PV/verbruik ≥ 0.
Eenheden: W, kWh, °C, %, A, V. Zie `backend/ems/core/models.py`.

## 2. Bestandsstructuur

```
backend/ems/integrations/<leverancier_of_protocol>/
├── __init__.py          # importeert de driver(s) zodat ze registreren
├── driver.py            # DeviceDriver-subklasse(n)
├── protocol.py          # register-/endpointdefinities MET bronvermelding
└── README.md            # ondersteunde modellen, firmware, documentatiebron, beperkingen
tests/test_<naam>.py
```

Alles onder `ems.integrations` wordt automatisch gevonden. Drivers van derden kunnen ook als los
Python-pakket worden geïnstalleerd met een entry point:

```toml
[project.entry-points."ems.drivers"]
mijn_merk = "mijn_pakket.driver"
```

## 3. Sjabloon

```python
from ems.core.models import Capability, Command, CommandAction, DeviceCategory, Metric
from ems.devices.base import (DeviceDriver, DeviceUnavailableError, DriverManifest,
                              UnsupportedCommandError)
from ems.devices.registry import register_driver


@register_driver
class ExampleBatteryDriver(DeviceDriver):
    manifest = DriverManifest(
        driver_id="example.battery_modbus",          # uniek, <leverancier>.<product/protocol>
        display_name="Voorbeeld batterij (Modbus TCP)",
        vendor="Voorbeeld",
        categories=(DeviceCategory.BATTERY,),
        capabilities=frozenset({
            Capability.READ_BATTERY_POWER,
            Capability.READ_BATTERY_SOC,
            Capability.CONTROL_BATTERY_MODE,
        }),
        connection_types=("modbus_tcp",),
        models=("Model X (firmware >= 1.2)",),
        documentation="<titel + versie van de officiële Modbus-documentatie>",
        verified=False,
        connection_schema={                           # velden voor de apparaatwizard
            "host": {"type": "string", "label_nl": "IP-adres"},
            "port": {"type": "integer", "default": 502, "label_nl": "Poort"},
            "unit_id": {"type": "integer", "default": 1, "label_nl": "Modbus unit-id"},
        },
    )

    async def connect(self) -> None:
        # Open de verbinding met self.config.connection["host"], ...
        # Gooi DeviceUnavailableError bij een netwerkfout.
        ...

    async def read(self) -> dict[Metric, object]:
        # Lees ALLEEN gedocumenteerde registers en reken om naar EMS-eenheden en -tekens.
        return {Metric.BATTERY_SOC_PCT: ..., Metric.BATTERY_POWER_W: ...}

    async def apply(self, command: Command) -> None:
        match command.action:
            case CommandAction.BATTERY_CHARGE: ...
            case CommandAction.BATTERY_DISCHARGE: ...
            case CommandAction.BATTERY_STANDBY: ...
            case CommandAction.BATTERY_AUTO: ...
            case _:
                raise UnsupportedCommandError(command.action)

    async def release_control(self) -> None:
        # Zet het apparaat terug in zijn eigen modus (bijv. remote control uit).
        ...
```

## 4. Eisen aan iedere driver

1. **Async en non-blocking.** Geen `time.sleep`, geen blokkerende sockets. De DeviceManager legt per
   aanroep een timeout op (`control.device_timeout_s`).
2. **Fouten als exceptions**: `DeviceUnavailableError` (bereikbaarheid), `UnsupportedCommandError`
   (niet mogelijk), `DriverError` (overig). Nooit stilletjes onzin teruggeven.
3. **Eenheden en tekens omrekenen** in de driver (schaalfactoren, signed/unsigned, W vs kW).
4. **`release_control()` is verplicht** en moet idempotent zijn.
5. **Schrijf zuinig.** Het EMS dedupliceert al, maar respecteer limieten van de fabrikant
   (bijv. EEPROM-schrijfcycli: gebruik waar mogelijk volatile/remote-control-registers).
6. **Gebruik ingebouwde time-outs van het apparaat** als die bestaan (remote control vervalt na X s),
   zodat het apparaat ook terugvalt als de Pi crasht.
7. **Geen geheimen in code of config**; wachtwoorden/tokens komen via `${VAR}` of de credential store.
8. **`capabilities()` eerlijk**: kan een model iets niet (bijv. geen SOC via deze interface), laat de
   capability weg — de wizard toont dan ✗.

## 5. Testen

* Unit-tests met een **nagebootste transportlaag** (fake Modbus-client/HTTP-server) op basis van
  voorbeeldwaarden uit de documentatie.
* Test omrekeningen (tekens, schaal), foutpaden (timeout, ongeldige respons), `release_control()`.
* `self_test()` moet zonder exceptions een rapport opleveren, ook bij een onbereikbaar apparaat.
* Pas na een test met echte hardware: `verified=True` + model/firmware in de README van de driver.

De bestaande `tests/test_drivers.py` controleert voor alle geregistreerde drivers o.a. dat een
`verified` driver een documentatiebron heeft.

## 6. Apparaatwizard

`DeviceDriver.self_test()` levert een `TestReport`:

```
✓ apparaat bereikbaar
✓ batterijvermogen uitleesbaar
✓ batterij-SOC uitleesbaar
✓ batterijbesturing beschikbaar — ondersteund door driver
✗ vermogensbegrenzing beschikbaar — niet ondersteund door deze driver
```

Leescapabilities worden echt uitgelezen; stuurcapabilities worden in de test **niet** uitgevoerd
(geen hardware bewegen zonder toestemming van de gebruiker).

## 7. Status van integraties

Legenda teststatus: UNIT = geautomatiseerde tests met nagebootst apparaat; SIMULATOR = in de woningsimulator;
HARDWARE = getest met een echt apparaat.

| Integratie | Driver-id | Lezen | Sturen | Teststatus |
|---|---|---|---|---|
| Mockapparaten (meter, PV, batterij, warmtepomp, laadpaal) | `mock.*` | ✓ | ✓ (simulatie) | UNIT, SIMULATOR — alleen in Demo Mode |
| HomeWizard P1-meter (API v1 en v2) | `homewizard.p1` | ✓ net, fasen, meterstanden | n.v.t. | UNIT (TLS-fake volgens de officiële API-docs) — **niet HARDWARE** |
| Slimme meter via P1-poort (DSMR 2.2–5) | `dsmr.p1` | ✓ net, fasen, meterstanden | n.v.t. | UNIT (telegrammen uit de standaard) — **niet HARDWARE** |
| Generiek Modbus TCP | `generic.modbus_tcp` | ✓ volgens mapping | ✗ (bewust) | UNIT (fake server volgens de Modbus-spec) — **niet HARDWARE** |
| Generiek HTTP/JSON | `generic.http_json` | ✓ volgens mapping | ✗ (bewust) | UNIT — **niet HARDWARE** |
| Generiek MQTT | `generic.mqtt` | ✓ volgens mapping | ✗ (bewust) | UNIT + echte Mosquitto-broker — **niet HARDWARE** |
| Omvormers/batterijen (bijv. Solis, Growatt, Deye, Sungrow, Victron, SMA, Fronius, GoodWe, Enphase) | — | via generieke driver | ✗ | niet geïmplementeerd: model, firmware, interface en officiële documentatie nodig |
| Warmtepompen (SG Ready, Modbus, fabrikant-API) | — | via generieke driver | ✗ | idem; SG Ready vereist geschikte schakelhardware (relais) |
| Laadpalen (OCPP, Modbus, lokale API) | — | via generieke driver | ✗ | idem |

Bronnen: HomeWizard — officiële API-documentatie (api-documentation.homewizard.com, API v1 en v2;
geraadpleegde versie in `integrations/homewizard/__init__.py`). Licentie: de HomeWizard-API is bedoeld
voor persoonlijk, niet-commercieel gebruik. DSMR — Netbeheer Nederland, P1 Companion Standard 5.0.2.
Modbus — Modbus Organization, Application Protocol V1.1b3 en Messaging on TCP/IP V1.0b.

Zodra we een echte (schrijvende) driver gaan bouwen, is per apparaat nodig: merk, exact model,
firmwareversie, beschikbare lokale interface(s) en de officiële documentatie.

## 8. Generieke drivers (zonder eigen code)

Voor apparaten met een gedocumenteerde Modbus-, HTTP- of MQTT-interface kunt u waarden uitlezen
zonder een driver te schrijven. In de wizard vult u een **waardetoewijzing** (JSON-lijst) in. Elke regel:

| Veld | Betekenis |
|---|---|
| `metric` | EMS-meetwaarde, bijv. `grid_power_w`, `grid_current_l1_a`, `pv_power_w`, `battery_soc_pct` (zie `Metric` in `core/models.py`) |
| `scale`, `offset` | omrekening: waarde = ruw × scale + offset (bijv. 0,1 voor "0,1 A per eenheid", 1000 voor kW → W) |
| `invert` | teken omdraaien als het apparaat een andere tekenconventie gebruikt (EMS: net + = afname, batterij + = laden) |

Als alleen `grid_import_power_w` en `grid_export_power_w` zijn ingesteld, berekent het EMS
`grid_power_w` = import − export.

**Modbus TCP** (`generic.modbus_tcp`): `host`, `port` (502), `unit_id`; per regel `address`
(protocoladres, 0-gebaseerd), `function` (`holding` = functie 3, `input` = functie 4), `type`
(`uint16`, `int16`, `uint32`, `int32`, `float32`, `uint64`, `int64`) en `word_order` (`big`/`little`).
Let op: handleidingen noemen vaak referenties als 40001/30001; protocoladres = referentie − 40001 (holding)
of − 30001 (input), tenzij de handleiding al 0-gebaseerde adressen geeft.

```json
[{"metric": "grid_power_w", "function": "holding", "address": 70, "type": "int32", "scale": 1},
 {"metric": "grid_voltage_l1_v", "function": "holding", "address": 72, "type": "float32"}]
```
*(adressen in dit voorbeeld zijn fictief — neem ze over uit uw eigen handleiding)*

**HTTP/JSON** (`generic.http_json`): `url`, optioneel `auth_header` + `auth_token`, `verify_tls`;
per regel `path` (punt-notatie, lijstindex als getal: `meters.0.power`).

**MQTT** (`generic.mqtt`): `host`, `port`, optioneel `username`/`password`/`tls`, `stale_after_s`;
per regel `topic` en optioneel `path` binnen een JSON-payload (zonder `path`: payload is de waarde).

Generieke drivers **schrijven nooit**: ze kunnen tot en met schaduwmodus worden ingezet. Een generieke
meter kan als primaire netmeter worden gekozen (wordt aangeboden, niet automatisch gekozen).
Wachtwoorden en tokens die u in de wizard invult, worden versleuteld opgeslagen en nooit in de YAML gezet.
