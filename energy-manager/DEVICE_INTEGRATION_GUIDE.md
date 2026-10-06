# Device Integration Guide

Hoe voeg je een nieuw apparaat (driver) toe aan Energy Manager?

## 0. De gouden regel: verzin niets

Een driver bevat **uitsluitend** registers, endpoints, MQTT-topics en commando's uit
**officiële documentatie** van de fabrikant of een gepubliceerde standaard (bijv. SunSpec,
DSMR P1 Companion Standard, OCPP, EEBus, SG Ready).

* Leg de bron vast in `manifest.documentation` (titel, versie, URL/document-id).
* Zet `verified=True` pas nadat de driver tegen echte hardware is getest (model + firmware noteren).
* Onbekend of niet gedocumenteerd? Dan **geen** driver gokken. Gebruik `mock.*`, of (vanaf fase 4)
  een generieke driver (`generic_modbus`, `generic_mqtt`, `generic_rest`) waarin de *gebruiker*
  zelf de mapping invult vanuit zijn eigen documentatie.
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
tests/integrations/test_<naam>.py
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

| Integratie | Status | Nodig om te bouwen |
|---|---|---|
| `mock.*` (meter, PV, batterij, warmtepomp, laadpaal) | ✅ fase 1 | — |
| P1 / DSMR slimme meter | gepland fase 4 | publieke DSMR P1 Companion Standard; type aansluiting (USB-kabel, P1-dongle met lokale API) |
| `generic_modbus` / `generic_mqtt` / `generic_rest` | gepland fase 4/7 | gebruiker levert mapping uit eigen documentatie |
| Omvormers/batterijen (bijv. Solis, Growatt, Deye, Sungrow, Victron, SMA, Fronius, GoodWe, Enphase) | niet geïmplementeerd | per apparaat: exact model, firmware, gekozen interface en de officiële protocol­documentatie |
| Warmtepompen (SG Ready, Modbus, fabrikant-API, Home Assistant) | niet geïmplementeerd | idem; SG Ready vereist geschikte schakelhardware (relais) |
| Laadpalen (OCPP, Modbus, lokale API) | niet geïmplementeerd | idem |

Zodra we een echte driver gaan bouwen, vraag ik per apparaat: merk, exact model, firmwareversie,
beschikbare lokale interface(s) en de officiële documentatie.
