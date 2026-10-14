# Inbedrijfstelling — Energy Manager 0.6.0

Elk apparaat dat iets kan bedienen doorloopt vijf niveaus. Een nieuw apparaat staat altijd op **alleen lezen**;
het EMS stuurt pas iets als de installateur het bewust hoger zet. Te vinden op de apparaatpagina, kaart
*Inbedrijfstelling*.

| Niveau | Wat het EMS doet | Voorwaarde |
|---|---|---|
| 1. Verbindingstest | niets; test bereikbaarheid | — |
| 2. Alleen lezen | gebruikt meetwaarden | geslaagde verbindingstest (de wizard doet die automatisch) |
| 3. Schaduwmodus | rekent volledig mee en toont "EMS zou …" — stuurt niets | bestuurbare functies aanwezig |
| 4. Beperkte regeling | stuurt met een begrensd deel van het gevraagde vermogen | driver schrijft volgens documentatie; veiligheidsinstellingen ingevuld; schaduwmodus heeft een beslissing getoond |
| 5. Volledige regeling | stuurt volledig | geslaagde schrijftest; apparaatnaam getypt ter bevestiging; **driver/profiel op echte hardware getest** (of virtueel apparaat in de Testomgeving) |

## Procedure

1. **Toevoegen** via de wizard; de test toont identiteit en het functierapport.
2. **Gegevens invullen** die nodig zijn voor regeling (bijv. capaciteit en maximaal laad-/ontlaadvermogen van
   een batterij, maximale stroom van een laadpaal, piekvermogen van de omvormer). Profielen vullen
   datablad-waarden vooraf in; controleer ze.
3. **Schaduwmodus**: laat het EMS minstens één beslissing tonen. Controleer of die logisch is.
4. **Schrijftest**: de minst ingrijpende echte schrijfactie — het apparaat wordt teruggezet naar zijn eigen
   regeling en daarna opnieuw uitgelezen.
5. **Beperkte regeling**: kijk of het apparaat doet wat het EMS zegt ("EMS doet nu" + terugmelding).
6. **Volledige regeling**: alleen als de koppeling op hardware is bewezen. In 0.6.0 is dat voor geen enkel
   echt apparaat het geval; in de Testomgeving kan het met de virtuele apparaten.

## Wat altijd geldt

* Terug naar een lager niveau kan altijd; het apparaat krijgt dan zijn eigen regeling terug.
* Krijgt een profiel nieuwe schrijffuncties (update/import), dan gaat het apparaat terug naar schaduwmodus.
* Eén regelaar per apparaat: bedien een apparaat niet tegelijk rechtstreeks en via Home Assistant.
* Bij onbetrouwbare netmeting, verouderde apparaatdata of een fout schakelt het EMS naar de **veilige stand**
  (apparaten op eigen regeling).

## Per soort

| Soort | Let op |
|---|---|
| Warmtepomp (SG Ready) | kies welke standen zijn toegestaan (standaard normaal + aanbevolen aan); "blokkeren" alleen als de installateur dat toestaat; minimale tijd per stand; relais-watchdog |
| Warmtepomp (Modbus-profiel) | setpointgrenzen uit het profiel; modus is een verzoek, de warmtepomp bewaakt zelf compressor en vorst |
| Thuisbatterij | capaciteit en maximale vermogens zijn verplicht; reserve-SOC uit de instellingen; één regelaar (geen tweede EMS) |
| Omvormer (SunSpec) | begrenzing via model 123 met terugvaltijd: stopt het EMS, dan heft de omvormer de begrenzing zelf op |
| Laadpaal | maximale stroom van de installatie is verplicht; zonder SOC van de auto wordt in kWh gepland |
| Relais / stekker | minimale aan- en uittijd; controleer of het relais de belasting aankan |
