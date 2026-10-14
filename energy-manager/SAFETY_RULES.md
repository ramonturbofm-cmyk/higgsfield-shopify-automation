# Veiligheidsregels — Energy Manager 0.6.0

Veiligheid gaat boven financiële optimalisatie. Deze regels zijn in de code afgedwongen (niet alleen
beschreven); tussen haakjes waar.

## Commandopad (geen omweg)

Optimizer / gebruiker / automatisering / cloud → planner → regelbevoegdheid (`control/authority.py`) →
capability-controle `can_execute` (type-schema ∩ apparaat) → SafetyValidator (`control/safety.py`) → poort
(`control/gate.py`) → driver → hardware → resultaatcontrole (terugmelding). Geen API-route roept een driver
rechtstreeks aan; handmatige opdrachten zijn overrides die hetzelfde pad volgen.

## Regels

1. **Geen verzonnen protocollen**: geen registers, endpoints, topics, commando's of firmwarereeksen zonder
   bron. Profielen zonder `documentation_sources` worden geweigerd.
2. **Schrijven alleen gedocumenteerd**: schrijffuncties met status `OFFICIAL_DOCUMENTATION` (of `SIMULATOR`
   in de Testomgeving). Community- en eigen profielen kunnen schrijven niet aanzetten.
3. **Volledige regeling alleen na hardwaretest** (`hardware_verified()`), behalve virtuele apparaten van de
   Testomgeving (127.0.0.1 én actieve Testomgeving).
4. **Demo en Testomgeving sturen geen echte hardware aan** (poort: alleen `mock.*` en virtuele apparaten).
5. **Nieuw apparaat = alleen lezen**; hoger zetten is een handeling van de installateur (zie
   `COMMISSIONING_GUIDE.md`).
6. **Geen opdracht op oude of ongeldige data** (SafetyValidator: verouderd, offline, waarde buiten bereik,
   minimale tijd tussen opdrachten).
7. **Grenzen uit profiel én installatie**: bereik van het profiel en de door de gebruiker ingevulde maxima;
   de laagste wint (bijv. laadstroom ≤ maximale stroom van de installatie).
8. **Terugleescontrole** na elke schrijfactie; afwijking = fout, geen stille aanname.
9. **Schrijfopdrachten worden nooit automatisch herhaald** als de uitkomst onbekend is (time-out).
10. **Apparaatwatchdog**: waar het apparaat dat biedt (Modbus-verloopregister, SunSpec-terugvaltijd,
    Shelly `toggle_after`) vervalt een opdracht vanzelf als het EMS stopt.
11. **Veilige standaard**: bij fout of loslaten gaat SG Ready naar stand 2 (normaal), batterijen naar
    automatisch, begrenzing uit (`release_control`).
12. **Eén regelaar per apparaat** (Home Assistant-bediening vereist expliciete bevestiging).
13. **Warmtepomp**: SG Ready is een verzoek, geen vermogensregeling; het EMS vraagt nooit een stand die de
    gebruiker niet heeft toegestaan; minimale tijd per stand.
14. **Fasebewaking** alleen met actuele fasestromen van de primaire netmeter; anders uit en zo getoond.
15. **Alleen echte meters** kunnen primaire netmeter zijn.
16. **Geheimen** (wachtwoorden, tokens) versleuteld opgeslagen, nooit getoond of gelogd; welke velden geheim
    zijn volgt uit de driver-metadata.
17. **Geen poortscans**; netwerkverkeer naar apparaten blijft lokaal.
18. **Fail-safe**: bij onbetrouwbare netmeting of interne fouten laat het EMS alle apparaten los (eigen regeling).
