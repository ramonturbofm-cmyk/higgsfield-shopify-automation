# Energy Manager 0.6.1 testen op Windows — stappenplan

Dit is een **testversie** (UNSIGNED TEST BUILD). U hoeft niets met Python of de opdrachtprompt te doen.
Alles wat u in de **Testomgeving** ziet is virtueel: er wordt geen echt apparaat aangestuurd.

Reken op ongeveer 45 minuten. Noteer bij elke stap **gelukt / niet gelukt** en maak bij een probleem een
screenshot.

---

## Voorbereiding

1. Download `EnergyManagerSetup-0.6.1.exe` van de GitHub-release **energy-manager-windows**
   (Releases → "Energy Manager (Windows)").
2. Controleer eventueel de controlesom: rechtsklik in de map → *Terminal openen* →
   `Get-FileHash .\EnergyManagerSetup-0.6.1.exe` — de uitkomst moet gelijk zijn aan de SHA-256 in
   `SHA256SUMS.txt` bij de release.

---

## De 23 stappen

| # | Wat u doet | Wat u moet zien |
|---|---|---|
| 1 | Dubbelklik op `EnergyManagerSetup-0.6.1.exe`. Windows SmartScreen meldt "Onbekende uitgever": kies **Meer informatie → Toch uitvoeren**. Volg de installatie. | De installatie eindigt zonder foutmelding. In het Startmenu staat "Energy Manager". Een eerdere versie wordt bijgewerkt; uw gegevens blijven. |
| 2 | Open **Energy Manager** uit het Startmenu. | Het startscherm met "Alles op deze computer" en drie keuzes: *Mijn eigen installatie*, *Demo*, *Testomgeving*. |
| 3 | Kies **Testomgeving** en klik **Starten**. | Binnen ongeveer een minuut opent het EMS. Inloggen: **demo / demo** (al ingevuld). |
| 4 | Kijk bovenaan, en onderin het menu links. | Een oranje label **TESTOMGEVING**. Links in het menu een pagina **Testomgeving**. Onderin het menu: **Versie 0.6.1 · build …** (staat er "App … ≠ server …" of een rode melding "Versieverschil", sluit de app dan helemaal en open hem opnieuw). |
| 5 | Open **Testomgeving** in het menu. | "Virtuele woning" met netmeting en een lijst van 8 virtuele apparaten met hun adres (127.0.0.1:…). |
| 6 | Ga naar **Apparaten → + Apparaat toevoegen**. | Bovenaan de stappen **Soort → Methode → Gegevens → Testen** en 15 tegels: Slimme meter, Energiemeter, Zonnepanelen, Hybride omvormer, Thuisbatterij, Warmtepomp, … Klik **Zonnepanelen / omvormer**: u ziet vier keuzes: *Automatisch zoeken*, *Kies merk en model*, *Via een gateway*, *Handmatig (Expert)*. (Ziet u "Merk en model" als tweede stap, dan draait er een oude versie: meld dit.) |
| 7 | **Automatisch zoeken**: kies *Thuisbatterij* → *Automatisch zoeken*. | Na enkele seconden: "Virtuele thuisbatterij 10 kWh" met **Exact model herkend** en het label *virtueel*. |
| 8 | Klik **Kiezen** → **Verbinding testen**. | "✓ Verbonden", "Apparaat meldt: EM Testomgeving EM-VBAT-10 · serienummer EMLAB-BAT-0001", en een tabel *Wat kan het EMS met dit apparaat?* (uitlezen: groen; bedienen: *Na inbedrijfstelling*). Klik **Opslaan**. |
| 9 | **Merk en model**: *+ Apparaat toevoegen* → *Slimme meter / P1* → *Kies merk en model* → onder "Energy Manager Testomgeving" **Virtuele P1-meter** → *Kiezen*. Laat "Gebruiken als primaire netmeter" aangevinkt → **Verbinding testen** → **Opslaan**. | De meter is toegevoegd. Bovenaan staat nu "Netmeter online". |
| 10 | Bekijk de pagina van de P1-meter. | Netvermogen, per fase vermogen/stroom/spanning, meterstanden. |
| 10b | **Merk en model bekijken**: *+ Apparaat toevoegen* → *Zonnepanelen / omvormer* → *Kies merk en model*. | Echte merken (Fronius, SMA, SolarEdge, Kostal, …) met per model de functies (bijv. "PV-vermogen en opbrengst", "Vermogensbegrenzing — alleen als …"). Merken zonder koppeling (Growatt, Enphase, …) staan er eerlijk als "Nog niet ondersteund", met knoppen "Koppelen via …". Home Assistant en SunSpec staan niet als merk, maar onderaan bij "Staat uw merk of model er niet bij?". |
| 11 | **Warmtepomp**: *+ Apparaat toevoegen* → *Warmtepomp* → *Kies merk en model*. | U ziet ook echte merken met een eerlijke status, bijv. Daikin/NIBE/Weheat "Nog niet ondersteund" (zonder knop) en "SG Ready" als alternatief. Kies **Virtuele warmtepomp (Modbus TCP)** → testen → opslaan. |
| 12 | **Omvormer**: voeg *Zonnepanelen / omvormer* → **Virtuele SunSpec-omvormer 5 kW** toe (testen, opslaan). | Rapport: PV-vermogen uitleesbaar; vermogensbegrenzing *Na inbedrijfstelling*. |
| 13 | (Optioneel) voeg ook de **Virtuele laadpaal** en **Virtuele boiler via slim relais** toe. | Bij de laadpaal staat géén "SOC van de auto": die is onbekend; u stelt een doel in kWh in. |
| 13b | **Home Assistant (virtueel)**: *+ Apparaat toevoegen* → *Elektrische boiler* → *Via een gateway* → **Via Home Assistant** → *Kiezen*. Adres en token (`testomgeving`) zijn al ingevuld. Klik **Verbinden en entiteiten ophalen**. Kies bij *Vermogen* "Testwoning boiler vermogen" en bij *aan/uit schakelen* "Testwoning boiler" → **Verbinding testen** → **Opslaan**. | U kiest alles uit lijsten; er is geen JSON nodig. |
| 14 | **Bewerken**: open de thuisbatterij → **Bewerken**, typ een nieuwe naam en wacht 10 seconden zonder op te slaan. | Wat u typte blijft staan (wordt niet gewist door live-updates). Klik **Opslaan**: de nieuwe naam staat bovenaan. |
| 15 | **Realtime**: ga naar het **Dashboard**. | De getallen veranderen elke paar seconden. |
| 16 | **Optimizer**: open **Planning**. | Een planning voor de komende uren met uitleg per kwartier. |
| 17 | **Schaduwmodus**: open de thuisbatterij → kaart *Inbedrijfstelling* → **→ 3. Schaduwmodus**. Wacht ± 1 minuut. | Onder "EMS zou doen" verschijnt een beslissing; onder "EMS doet nu": niets. De batterij wordt niet aangestuurd. |
| 18 | Klik **Schrijftest uitvoeren**. | "Schrijftest geslaagd". |
| 19 | **Beperkte regeling**: klik **→ 4. Beperkte regeling**. | De regeling-status wordt "Beperkte regeling". (Voor volledige regeling typt u de apparaatnaam in het vak en klikt u *→ 5. Volledige regeling*; dat mag alleen bij virtuele apparaten.) |
| 20 | **Effect zien**: open het menu **Batterij** of het Dashboard en kijk een paar minuten. Of geef op de batterijpagina handmatig een opdracht (bijv. ontladen 2 kW). | Het vermogen van de virtuele batterij verandert; op de pagina Testomgeving ziet u bij *battery* de nieuwe modus. |
| 21 | **Storing testen**: pagina **Testomgeving** → bij de thuisbatterij **offline** aanvinken → **Toepassen**. Wacht ± 15 s en open **Apparaten**. | De batterij staat op *offline* / *verouderd*. Zet het vinkje weer uit en klik **Toepassen**: de batterij komt terug. |
| 22 | **Instellingen opslaan, sluiten en heropenen**: wijzig onder *Instellingen* de naam van de woning, sla op. Sluit het venster van de app. Open de app opnieuw en kies weer **Testomgeving** → **Starten**. | Het EMS draaide door op de achtergrond; alles is er nog: apparaten, inbedrijfstelling, de nieuwe naam. |
| 23 | (Optioneel) Herstart Windows en open de app. | Het EMS start vanzelf weer; uw apparaten en instellingen zijn bewaard. |

---

## Wat u **niet** moet verwachten

* **Echte apparaten**: in de Testomgeving ziet u ook echte merken, maar die zijn nog **niet op echte hardware
  getest**. Volledige regeling van een echt apparaat blijft geblokkeerd tot er een hardwaretest is gedaan.
* **Merken met "Nog niet ondersteund"** (Solis, GoodWe, Sungrow, SolaX, Huawei, NIBE, Daikin, Weheat,
  OCPP-laadpalen) kunnen niet gekozen worden. Er staat bij wat wel kan (SG Ready of Home Assistant).
* De Demo is nog steeds beschikbaar als volledig gesimuleerd huis zonder wizard.

## Terug naar uw eigen installatie

Startscherm → **Mijn eigen installatie** → *Starten*. De gegevens van Demo, Testomgeving en uw eigen
installatie worden apart bewaard (`%LOCALAPPDATA%\EnergyManager\demo`, `…\lab`, `…\server`).

## Als iets niet werkt

* "Het EMS start niet": open uit het Startmenu **Energy Manager Server (met venster)** en maak een screenshot
  van de foutmelding. Het logbestand staat in `%LOCALAPPDATA%\EnergyManager\logs\server.log`.
* Virtuele apparaten ontbreken op de pagina Testomgeving: een ander programma gebruikt de poorten
  15502–15599. Sluit dat programma of stuur het logbestand mee.
