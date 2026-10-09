# Installeren op Windows

Windows 10 of 11, 64-bit. Geen Raspberry Pi nodig: de installer bevat de app én de EMS-server.

## Downloaden

Nieuwste geteste build (rolling pre-release):
https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation/releases/tag/energy-manager-windows

* `EnergyManagerSetup.exe` — altijd de nieuwste versie
* `EnergyManagerSetup-<versie>.exe` — dezelfde installer met versienummer
* `SHA256SUMS.txt` — controlegetallen; vergelijk met `Get-FileHash .\EnergyManagerSetup.exe -Algorithm SHA256`
* `acceptance-results.txt` — uitslag van de automatische test op een schone Windows-machine

De installer is een **UNSIGNED TEST BUILD** (geen code-signing-certificaat). Windows SmartScreen meldt
"Onbekende uitgever": kies *Meer informatie → Toch uitvoeren*, alleen als het SHA-256-controlegetal klopt.

## Installeren

1. Start de installer. Installatie is per gebruiker (geen beheerdersrechten nodig) in
   `%LOCALAPPDATA%\Programs\Energy Manager`.
2. Kies *Alles op deze computer* (aanbevolen) en laat "EMS automatisch starten bij aanmelden" aangevinkt.
3. Open de app en kies:
   * **Alles op deze computer** → *Demo* (gesimuleerde woning, login `demo`/`demo`) of *Mijn eigen installatie*
     (eerste keer: beheerdersaccount aanmaken) → **Starten**;
   * **Verbinden met een bestaand systeem** (Raspberry Pi/Linux op het netwerk);
   * **Raspberry Pi / Linux-node toevoegen**.
4. Zet de slaapstand van de pc uit; de app waarschuwt als de pc in slaap kan vallen.

Het EMS draait daarna op de achtergrond (zonder venster) en blijft regelen als de app dicht is.

## Bijwerken, terugzetten, verwijderen

* **Bijwerken:** nieuwe installer starten. Het EMS wordt eerst netjes gestopt (apparaten vrijgegeven);
  instellingen en historie blijven bewaard.
* **Downgrade wordt geweigerd:** een oudere installer over een nieuwere versie stopt met een melding, omdat
  een oudere versie de database van een nieuwere niet veilig kan gebruiken. Teruggaan: oude versie installeren
  op een schone gebruiker en een back-up van die oude versie herstellen.
* **Verwijderen:** via Windows *Apps*; gegevens in `%LOCALAPPDATA%\EnergyManager` blijven staan.

## Gegevens en logboek

* Gegevens: `%LOCALAPPDATA%\EnergyManager` (configuratie, database, versleutelde geheimen, back-ups).
* Logboek: `%LOCALAPPDATA%\EnergyManager\logs\server.log`.
* EMS stoppen: Startmenu → *Energy Manager → Hulpmiddelen → EMS stoppen*.

## Zelf bouwen

Python 3.12, Node 20+, Rust, Inno Setup 6: `.\windows\build.ps1`; acceptatietest:
`.\windows\acceptance.ps1 -Installer dist\EnergyManagerSetup-<versie>.exe`.

## Prijzen en cloud (0.5.0)

* Prijsbron: *Instellingen → Marktgegevens & prognoses*. EnergyZero werkt zonder account of token
  ("Verbinding testen" laat zien of de prijzen binnenkomen). Wat u zelf betaalt stelt u in bij
  *Energiecontract*.
* Energy Manager Cloud is optioneel (*Instellingen → Cloud*, niveau Uitgebreid): alleen een uitgaande
  HTTPS-verbinding, geen open poorten. Lokaal werkt alles ook zonder cloud.
