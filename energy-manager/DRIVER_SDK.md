# Driver SDK — Energy Manager 0.6.0

Hoe een nieuwe koppeling wordt toegevoegd. Lees eerst `DEVICE_INTEGRATION_GUIDE.md` (de regels) en
`DEVICE_PROFILE_SCHEMA.md` (profielen).

## Kies de goedkoopste route

1. **Alleen een profiel** (geen code) — het apparaat spreekt Modbus en de registers staan in officiële
   documentatie → schrijf een JSON-profiel voor `library.modbus`. Spreekt het SunSpec → vaak is alleen een
   regel in `manufacturers.json` nodig (bestaand profiel `sunspec.inverter` / `sunspec.meter`).
2. **Bestaande driver + gateway** — SG Ready via relais, Home Assistant-brug, Shelly-schakelaar.
3. **Nieuwe driver** — eigen protocol (HTTP-API, WebSocket, OCPP …). Alleen met een gepubliceerde,
   verifieerbare bron.

## De driver-interface

```python
from ems.devices.base import DeviceDriver, DriverManifest
from ems.devices.registry import register_driver

@register_driver
class MijnDriver(DeviceDriver):
    manifest = DriverManifest(
        driver_id="vendor.model",                  # uniek
        display_name="Merk Model",
        vendor="Merk",
        categories=(DeviceCategory.BATTERY,),
        capabilities=frozenset({...}),             # wat de driver ALTIJD kan; leeg als het per apparaat verschilt
        connection_types=("http",),
        documentation="titel + URL + datum van de bron",
        write_capable=True,
        connection_schema={
            "host": {"type": "string", "label_nl": "IP-adres", "required": True},
            "password": {"type": "string", "label_nl": "Wachtwoord", "secret": True},   # versleuteld opgeslagen
            "port": {"type": "integer", "default": 80, "label_nl": "Poort", "level": "expert"},
            "serial_port": {"type": "string", "show_if": ["transport", "rtu"]},
        },
    )

    async def connect(self) -> None: ...
    async def read(self) -> dict[Metric, Any]: ...          # alleen gemeten waarden, nooit verzonnen
    async def apply(self, command: Command) -> None: ...    # schrijven + terugleescontrole; fout = exception
    async def release_control(self) -> None: ...            # apparaat terug naar eigen regeling
    async def identify(self) -> dict: ...                   # optioneel: manufacturer/model/serial_number/firmware_version
    def capabilities(self) -> frozenset[Capability]: ...    # optioneel: per apparaat (na scan/profiel)
    def write_documented(self) -> bool: ...                 # optioneel: schrijven volgens documentatie?
    def hardware_verified(self) -> bool: ...                # optioneel: echt op hardware getest?
    def simulated_target(self) -> bool: ...                 # optioneel: virtueel apparaat van de Testomgeving?
    def modes(self) -> list[str]: ...                       # optioneel: toegestane warmtepompstanden
    def diagnostics(self) -> dict: ...
```

`connection_schema`-metadata: `label_nl`, `help_nl`, `default`, `enum`, `type`
(`string`/`integer`/`number`/`boolean`/`array`), `required`, `readonly`, `secret`, `level`
(`advanced`/`expert`), `show_if` (`[veld, waarde]`). De wizard bouwt het formulier hieruit.

## Wat de kern voor u regelt

* Een opdracht komt **altijd** via: optimizer/gebruiker → planner → regelbevoegdheid → capability-controle
  (`can_execute`) → SafetyValidator → poort (Demo/Testomgeving blokkeert echte hardware) → **driver** →
  resultaatcontrole. Een driver wordt nooit rechtstreeks door een API-route aangeroepen.
* Inbedrijfstelling (alleen lezen → schaduw → beperkt → volledig) en de blokkade van volledige regeling zolang
  `hardware_verified()` niet waar is.
* Gegevenskwaliteit (verouderd, ongeldig, bevroren waarden), offline-detectie, herverbinden.

## Modbus-hulpmiddelen

`ems.integrations.modbus.transport.ModbusTransport` (TCP/RTU, lock, rate-limit, time-out, lees-retries, **geen**
herhaling van schrijfopdrachten) en `ems.integrations.modbus.codec` (`decode`, `encode`, `scaled`,
`unscaled`, `register_count`).

## Testen (verplicht)

* Unit-tests met de codec en een nagebootste API (`httpx.MockTransport`) of een virtueel apparaat in
  `ems/simulator/lab.py` (Modbus via pymodbus, HTTP via `MiniHTTPServer`).
* Zie `tests/test_device_library.py` voor voorbeelden: schrijven + terugleescontrole, buiten bereik,
  Modbus-foutcode, time-out zonder herhaling, offline, watchdog.
* Zet `test_status` op `HARDWARE_TESTED` **alleen** na een echte test op hardware (met model en firmware in de
  documentatie van het profiel).
