# Audio OnAir Turbo

Playout-studio in de browser (in de geest van mAirList) met daarachter een eigen
audio-database: **Audio Vault**.


Je eigen online omgeving voor audiobestanden. Jij beheert de bestanden, nodigt
mensen uit en bepaalt per persoon welke collecties ze mogen gebruiken. Wie
toegang heeft, kan de bestanden direct koppelen aan **mAirList** of een ander
audioprogramma. Daarachter draait een PostgreSQL-database.

```
              ┌──────────────── Audio Vault (deze server) ────────────────┐
 Browser ───► │  website: uploaden, collecties, gebruikers, rechten       │
              │                                                            │
 mAirList ──► │  /dav/        netwerkschijf (WebDAV, alleen-lezen)         │──► PostgreSQL
 VLC, ... ──► │  /m/<token>/  M3U-playlists + stream-links + JSON          │    (gebruikers, rechten,
              └────────────────────────────────────────────────────────────┘     metadata, logboek)
                                     │
                                     ▼
                         schijf met de audiobestanden
```

## Wat kan het

- **Collecties** (bijv. *Jingles*, *Muziek*, *Reclames*) met uploaden via slepen;
  titel, artiest en duur worden automatisch uit de tags gelezen.
- **Mensen uitnodigen**: je krijgt een link die je doorstuurt; de ontvanger kiest
  zelf een wachtwoord. Rollen:
  - *Eigenaar* – jij; alles.
  - *Beheerder* – alles behalve de eigenaar aanpassen.
  - *Lid* – alleen de collecties die je aanvinkt, per collectie *gebruiken*
    en/of *uploaden*.
- **Koppelen** – iedere gebruiker maakt een eigen koppel-token aan:
  1. **Netwerkschijf (WebDAV)** – de bibliotheek verschijnt in Windows als
     stationsletter met een map per collectie. In mAirList voeg je die map toe
     aan je database. Inloggen met e-mail + token.
  2. **M3U-playlists** per collectie – te openen in mAirList, VLC, RadioDJ,
     foobar2000, enz. De nummers streamen vanaf de server (met doorspoelen).
  3. **library.json** – catalogus met stream-URL's voor eigen scripts/software.
- **Direct intrekken**: iemand blokkeren of zijn token vernieuwen sluit meteen
  alle koppelingen af.
- **Activiteit**: wie wat heeft afgespeeld, gestreamd of gedownload.

## De studio (Audio OnAir Turbo)

Open via **▶ Open studio** na het inloggen, of ga naar `/studio.html`.

- **Playlist** met twee spelers (A/B), net als mAirList: terwijl het ene nummer
  speelt, staat het volgende al volledig geladen klaar op zijn startpunt.
- **AUTO**: start het volgende item precies op het mixpunt, zonder gat.
  Uit = *assist*: na elk item wacht hij op START.
- **Auto-cue**: stilte aan begin en eind wordt automatisch gevonden, en ook het
  punt waar het einde zacht genoeg wordt om door te starten. Eén keer per
  bestand berekend en in de database opgeslagen.
- Knoppen: **START**, **PAUZE**, **STOP**, **FADE**, **VOLGENDE**, **AUTO**,
  per item *stop na dit item*, slepen om te sorteren, verwachte starttijden,
  aftellen met waarschuwing in de laatste 15 seconden.
- **Jingle paneel**: 4 pagina's (A–D) met 12 tot 24 knoppen, eigen kleuren,
  sneltoetsen 1–9 en 0, meerdere jingles tegelijk.
- **Meerdere geluidskaarten**: kies een eigen uitgang voor Player A, Player B,
  het jingle paneel en voorbeluisteren (PFL). Werkt in Chrome en Edge.
- **Kleuren**: achtergrond zwart (standaard), antraciet, nachtblauw of licht,
  en een eigen accentkleur.
- **Nu op de radio**: de studio meldt elk gestart nummer. Openbaar te tonen op
  `/nu.html` (scherm in de studio of op je website; kleur via `?kleur=30d158`)
  of op te halen als JSON via `/api/now-playing`.

### Status, dashboard en logboek

- **Statusbalk** (onderaan): *SERVER* (gemeten responstijd, elke 15 s), *DATABASE*
  (de server controleert de verbinding), *AUDIO* (uitgang van de players: samplerate,
  geluidskaart, en of er een apparaatfout is) en voor beheerders *CLIENTS* (wie de
  server de laatste 2 minuten gebruikte). Alleen echte metingen; wat niet gemeten kan
  worden (bijv. buffer of CPU in de browser), staat er niet.
- **Players**: statuslampje per player — groen = on air, blauw = klaar, oranje =
  pauze, rood = fout. Kan een bestand niet laden (weg, beschadigd), dan wordt het
  overgeslagen, gaat de volgende door en staat er 20 s *⚠ Overgeslagen: …*.
- **▦ Dashboard** (of toets **D**): nu op de radio, hierna, playlist, server,
  database, audio en waarschuwingen in één overzicht. Alleen kijken, verandert niets.
- **LOG** (statusbalk): logboek met INFO / WARNING / ERROR (players, fouten,
  verbinding). Bewaart de laatste 500 regels in deze browser, groeit dus niet onbeperkt.
- **Database**: snelfilters *Alles*, *★ Favorieten*, *Nieuw* en genre (genres komen
  vanzelf uit de tags). Klik één nummer aan voor details uit het bestand zelf (album,
  jaar, formaat, samplerate, bitdiepte, bitrate, kanalen); wat het bestand niet weet,
  wordt niet getoond. Favoriet maken: rechtsklik → ★ of de knop in het infovak.
- **Jingle-knoppen** tonen naam, categorie (collectie of eigen tekst) en lengte.
  Rechtsklik: kleur, *Vervangen door selectie*, *Categorie…*, *Eigenschappen*,
  *Leegmaken…* (met bevestiging; het bestand zelf blijft in de database).

### Uurklokken (◔)

Leg één keer vast hoe een uur is opgebouwd, bijvoorbeeld *Top of the hour →
Nieuws → muziek → jingle → muziek → muziek → weer → …*. Elk blok is:

- **Muziek** – willekeurig uit een collectie, met rotatie: niet hetzelfde
  nummer binnen 3 uur (`ROTATION_HOURS`), niet dezelfde artiest binnen 4
  nummers, langst niet gedraaid eerst.
- **Jingle** – willekeurig uit een collectie.
- **Vast nummer** – altijd hetzelfde bestand.

De klok toont het uur als ring, met de geschatte starttijd van elk blok. In
de **weekplanning** kies je per uur welke klok draait (klik of sleep). In de
studio vult **◔ Plan uur** het volgende uur, of zet in Instellingen
*Automatisch bijplannen* aan: dan wordt de playlist steeds aangevuld en draait
het station 24/7 zelfstandig (AUTO moet aan staan).

**Database doorbladeren en "als volgende"**: de lijst in de studio laadt vanzelf
verder als je naar beneden scrolt, door je hele database heen (sorteer op
artiest, titel of nieuwste). Per nummer: **⤴** = als volgende afspelen (komt
direct na wat nu speelt en wordt meteen klaargezet), **+** = achteraan.
Rechtsklik geeft ook **Direct afspelen** (neemt over met een korte overvloei)
en **Voorbeluisteren**.

Sneltoetsen: spatie = start/volgende, N = volgende, P = pauze, F = fade,
A = auto aan/uit.

Tip: houd de studio in een eigen browservenster open. Instellingen, playlist en
jingle paneel worden per gebruiker op de server bewaard; de keuze van
geluidskaarten per computer.

## Windows-pc + Synology zonder Docker (bijv. DS218play)

Kleine Synology-modellen (de *j*- en *play*-series) kunnen geen Docker draaien.
Dan draait het programma op een pc en blijft de muziek op de NAS.

1. **NAS**: maak in DSM een gebruiker (bijv. `onair-lezen`) met alleen
   *leesrechten* op de gedeelde map met muziek. Noteer het IP-adres van de NAS
   (Configuratiescherm → Netwerk → Netwerkinterface).
2. **Pc**: installeer [Docker Desktop](https://www.docker.com/products/docker-desktop/)
   en zet in de instellingen *Start Docker Desktop when you sign in* aan.
3. Zet de map `audio-vault` op de pc (bijv. `C:\AudioOnAir`) en dubbelklik
   **`start-windows.bat`**. De eerste keer opent Kladblok met `.env`: vul de
   wachtwoorden en `NAS_HOST`, `NAS_SHARE`, `NAS_USER`, `NAS_PASSWORD` in,
   sla op en dubbelklik opnieuw. De browser opent `http://localhost:3000`.
4. Dubbelklik **`importeren-windows.bat`** om de muziek van de NAS in te
   lezen (WAV wordt FLAC; op de NAS verandert niets).
5. **Van buitenaf bereikbaar** via de NAS als voordeur:
   - DSM → Configuratiescherm → Externe toegang → **DDNS**: gratis adres,
     bijv. `ramonturbofm.synology.me`, met Let's Encrypt-certificaat.
   - Aanmeldingsportal → Geavanceerd → **Reverse proxy**: bron
     `https://radio.ramonturbofm.synology.me:443` (of je eigen domein),
     bestemming `http://<IP van de pc>:3000`.
   - Zet in de router poort 443 door naar de NAS, en vul `PUBLIC_URL` in `.env`.
   - Geef de pc een vast IP-adres in je router.

QuickConnect werkt alleen voor de eigen apps van Synology, niet voor dit
programma; daarvoor is de reverse proxy nodig.

De pc moet aan blijven staan zolang het station draait of anderen afspelen.
Voor 24/7 is een zuinige mini-pc (bijv. Intel N100, 16 GB, SSD van 1 TB,
± €200–300, ± 10 watt) de nette oplossing; dezelfde stappen gelden dan.

## Mini-pc als hoofdserver (Synology alleen voor back-up)

De mini-pc doet alles: studio, database, muziek (FLAC op de eigen schijf) en de
voordeur voor klanten via internet. De Synology bewaart alleen de back-up en je
oude WAV-archief.

```
Klant ──https──► router (443/80) ──► mini-pc: Caddy ──► Audio OnAir Turbo + database
                                         │              muziek (FLAC) op deze pc
                                         └── elke dag back-up ──► Synology
```

1. **Installeren** met de installatiehulp. Muziek: kies eenmalig *Op mijn
   Synology* om je WAV-archief in te lezen (wordt FLAC op deze pc), back-up:
   *Naar mijn Synology*.
2. **Eigenaarsaccount** aanmaken in de studio (het eerste account wordt eigenaar).
3. **Muziek loskoppelen van de Synology** zodra alles is ingelezen: maak in
   *Server beheren → Instellingen* de NAS-muziekvelden leeg en kies bij *muziek uit
   een map op deze pc* bijv. `D:\Nieuwe muziek`. Nieuwe nummers zet je daar neer
   of upload je vanuit de studio (☰ Bibliotheek).
4. **Gratis internetadres**: maak op [duckdns.org](https://www.duckdns.org) een
   naam aan, bijv. `turbofm` → `turbofm.duckdns.org`.
5. **Router**: geef de mini-pc een vast IP-adres en zet poort **80** en **443**
   door naar de mini-pc. Nooit poort 3000 of 3389 (extern bureaublad).
6. **Server beheren → Instellingen → Bereikbaar via internet**: vul
   `turbofm.duckdns.org` en de DuckDNS-token in, *Opslaan*, dan *Herstarten /
   bijwerken*. Caddy haalt zelf een gratis HTTPS-certificaat (Let's Encrypt) en
   verlengt het; de status staat bij *Via internet*.
7. Klanten gebruiken `https://turbofm.duckdns.org` (website of app).

Het internetadres kan pas aan als er een eigenaarsaccount is, zodat niemand van
buitenaf als eerste het station kan claimen. Er kan per pc maar één database via
internet bereikbaar zijn (poort 443). Een eigen domein (bijv. `radio.turbofm.nl`)
werkt ook: zet een A-record naar je internetadres en laat de token leeg.

## Windows-app (Audio OnAir Turbo.exe)

In `desktop/` zit de Windows-app: de studio in een eigen venster met logo,
installatieprogramma, snelkoppeling op het bureaublad en in het startmenu.

- Bij de eerste start vraagt de app het **adres van je server** (bijv.
  `localhost:3000` op de server-pc zelf, of `radio.ramonturbofm.synology.me`).
  Daarna log je in zoals op de website.
- Speelt door als het venster op de achtergrond staat en houdt de pc wakker.
- Vraagt om bevestiging als je afsluit terwijl er iets on air is.
- Menu (Alt): Studio (Ctrl+1), Uurklokken (Ctrl+2), Bibliotheek (Ctrl+3),
  Nu op de radio (Ctrl+4), volledig scherm, andere server kiezen.
- Links naar andere websites openen in je gewone browser.

**Server beheren vanuit de app** (menu → *Server beheren*, Ctrl+5, of de link
op het startscherm): op de pc die de server is, regelt de app alles zelf via
Docker Desktop, zonder `.bat`-bestanden:

- de eerste keer leidt een **installatiehulp** je in 4 stappen door alles (Docker
  Desktop installeren met één knop, waar je muziek staat, waar de back-up heen gaat)
  en zet hij met **Installeren en starten** de server klaar en leest je muziek in;
- eenmalig instellen: map op de pc (standaard `C:\AudioOnAir`), muziek op de
  NAS of in een map, back-up naar de NAS;
- knoppen: **Server starten**, **Stoppen**, **Muziek importeren**,
  **Back-up maken**, **Back-up terugzetten**;
- status van Docker, database, website en de laatste back-up, met logboek.

Na een update van de app klik je op *Herstarten / bijwerken*; de nieuwe
serverversie wordt dan geïnstalleerd (database, muziek en `.env` blijven).
De `.bat`-bestanden werken nog steeds, voor wie liever zonder app werkt.

**Meerdere databases op één pc**: bovenaan *Server beheren* kies je de database,
of maak je met **+ Nieuwe database** een tweede, volledig losse database aan
(bijv. *Mijn eigen* naast de station-database). Elke database heeft een eigen
map (`C:\AudioOnAir-<naam>`), poort (3001, 3002, …), muziek, gebruikers,
uurklokken en back-up, en draait naast de andere. Wisselen gaat via het menu
**Database**; je blijft in elke database apart ingelogd en de venstertitel
toont waar je bent. *Uit de lijst halen* laat de map met gegevens staan.

**Bijwerken met één klik**: in de studio staat rechtsboven een ⬆-icoon. Is er een
nieuwe versie, dan wordt het rood met een stip. Klik erop → *Nu bijwerken*: de app
downloadt de update, installeert hem, start opnieuw en werkt daarna vanzelf ook de
server bij (Server beheren toont de voortgang en gaat daarna terug naar de studio).
De uitzending stopt daarbij 1 à 2 minuten. Hetzelfde kan via Help → *Bijwerken /
controleren op updates*.

**Updates**: elke nieuwe versie staat op de updatepagina
<https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation/releases>
(zonder inloggen te downloaden). De app kijkt daar bij het starten en elke zes uur
zelf en meldt *Update beschikbaar* met een knop **Downloaden**; ook via Help →
*Controleren op updates*. Installeer de nieuwe Setup over de oude heen en klik in
*Server beheren* op *Herstarten / bijwerken*.

**Installatieprogramma maken**: bij elke wijziging bouwt GitHub het automatisch op
een Windows-machine en zet het op de updatepagina. Zelf bouwen op een Windows-pc:
`cd desktop && npm install && npm run dist` → `desktop/dist/`.

Het installatieprogramma is niet digitaal ondertekend; Windows toont daarom de
eerste keer "Windows heeft uw pc beschermd" → *Meer info* → *Toch uitvoeren*.
Een code-signing-certificaat (± €100–300 per jaar) haalt die melding weg.

## Op je Synology draaien (aanbevolen voor een grote muziekbibliotheek)

Werkt op een Synology met Container Manager (meestal de "+"-modellen, liefst
4 GB RAM of meer). Je muziek blijft op de NAS; de server leest hem alleen.

1. Maak een map, bijv. `/volume1/docker/audio-onair`, en zet de inhoud van
   `audio-vault/` daarin.
2. Kopieer `docker.env.example` naar `.env` en vul in: wachtwoorden,
   `MUSIC_DIR` (bijv. `/volume1/music`) en later `PUBLIC_URL`.
3. Container Manager → **Project → Maken** → kies die map (hij vindt
   `docker-compose.yml`) → bouwen en starten. Daarna draait hij op poort 3000.
4. Bestaande muziek importeren (WAV wordt FLAC, originelen blijven staan):

   ```bash
   docker compose exec -u node app node src/import.js /import --per-folder
   ```

   `--per-folder`: elke hoofdmap (bijv. `Jingles`, `Muziek`) wordt een collectie.
   Opnieuw draaien slaat al geïmporteerde bestanden over.
5. Bereikbaar maken voor anderen, veilig via https:
   DSM → Configuratiescherm → **Aanmeldingsportal → Geavanceerd → Reverse
   proxy**: `https://radio.jouwdomein.nl` → `http://localhost:3000`, en een
   gratis Let's Encrypt-certificaat via **Beveiliging → Certificaat**. Zet
   poort 443 door in je router. Zet poort 3000 zelf **niet** open naar internet.

## Backup (database + muziek)

De dienst `backup` draait automatisch mee. Elke 24 uur dat de server aanstaat,
maakt hij een backup: een complete kopie van de database (`database/`, 30
dagen bewaard) en alle audiobestanden (`audio/`, alleen nieuwe bestanden). Staat
de pc 's nachts uit, dan gebeurt het gewoon zodra hij weer aan is.

- **Naar de NAS**: maak op de Synology een gedeelde map `backup` en een
  gebruiker (bijv. `onair-backup`) met schrijfrechten op alleen die map. Vul
  `NAS_BACKUP_SHARE`, `NAS_BACKUP_USER` en `NAS_BACKUP_PASSWORD` in `.env` in
  en start opnieuw met `start-windows.bat`.
- Zonder NAS-gegevens komt de backup in `data/backup` op de server-pc.
- **`backup-nu-windows.bat`**: direct een backup maken.
- **`herstellen-windows.bat`**: een backup terugzetten (toont de lijst, vraagt
  om bevestiging, zet database en ontbrekende muziek terug).
- In `backup.log` in de backupmap zie je wanneer de laatste backup gelukt is.

## Opslag

Reken voor FLAC op ± 6 MB per minuut (± 25 MB per nummer van 4 minuten; 10.000
nummers ≈ 250 GB). Advies voor een eigen server-pc: SSD 1 (1 TB NVMe) voor
Windows, Docker en de database, en SSD 2 (2–4 TB) voor de muziek. Kies in de
installatiehulp bij stap 4 met **Andere schijf…** de muziekschijf (bijv.
`D:\AudioOnAir`); de app toont hoeveel ruimte er vrij is. De back-up op de NAS
heeft minstens evenveel vrije ruimte nodig als je FLAC-bibliotheek.

## Archief omzetten (WAV → FLAC op de NAS)

In *Server beheren* → **Archief omzetten** zet je je hele muziekarchief om naar
FLAC, in een **nieuwe** map met dezelfde mappenstructuur (bijv. een nieuwe
gedeelde map `music-flac` op de Synology, met een gebruiker die daar mag
schrijven). Je oude WAV-map wordt alleen gelezen.

- Elk nummer wordt **bit-voor-bit** gecontroleerd: de audio in de FLAC moet
  exact gelijk zijn aan het origineel. Wat FLAC niet exact kan bewaren
  (32-bit of float-WAV) wordt ongewijzigd meegekopieerd; andere bestanden
  (MP3, hoesjes) ook, zodat de nieuwe map compleet is.
- **Pauzeren** en later **verdergaan** kan altijd; het draait op de achtergrond
  door, ook als de app dicht is.
- Aan het eind staat `_omzetrapport.txt` in de nieuwe map: aantallen, mislukte
  bestanden en hoeveel ruimte je bespaart.
- Pas als je tevreden bent, verwijder je zelf de oude WAV-map.

Zonder app: `docker compose --profile archief up -d archive` (doel: `ARCHIVE_DIR`
of de `NAS_ARCHIVE_`-instellingen met `docker-compose.nas-archive.yml`).

## Audio OnAir Turbo Omzetter (los programma)

`converter/` is een los Windows-programma om een WAV-archief om te zetten naar
FLAC, zonder Docker of server; ffmpeg zit erin. Kies de map met WAV-bestanden
(ook op de Synology, bijv. `\\DS218play\music` of een netwerkschijf), kies een
nieuwe map en klik **START**. Het toont vooraf hoeveel bestanden en ruimte het
gaat om, en tijdens het omzetten een voortgangsbalk (ook op de taakbalk), de
resterende tijd en de bespaarde ruimte. **Pauzeren** en later **verdergaan**
kan altijd; de pc gaat tijdens het omzetten niet in slaapstand. Het gebruikt
dezelfde bit-voor-bit-controle als de server (`src/convert-archive.js`).

GitHub bouwt het automatisch (Actions → artifact *Audio-OnAir-Turbo-Omzetter*):
een installatieprogramma en een losse `.exe` die je zonder installeren start.

## Nieuwe muziek automatisch toevoegen

Zet nieuwe nummers gewoon in je muziekmap (op de Synology of op de pc): de server
kijkt standaard elke 10 minuten en voegt ze toe (WAV wordt FLAC, elke hoofdmap
een collectie). Instellen of uitzetten in *Server beheren* → *Nieuwe muziek
automatisch toevoegen* (of `AUTO_IMPORT_MINUTES`, 0 = uit).

- Een bestand dat nog gekopieerd wordt (minder dan 2 minuten oud), wacht tot de
  volgende ronde, zodat er nooit een half bestand in komt.
- Er loopt nooit meer dan één import tegelijk (handmatig of automatisch).
- Een bestand dat niet lukt, wordt pas opnieuw geprobeerd als het verandert.
- De studio meldt "🎵 3 nieuwe nummers in de database" en ververst de lijst;
  nieuwe nummers doen meteen mee in de uurklok (nooit gedraaid gaat voor).
- Nummers die je uit de map weghaalt, blijven in de database staan, zodat er
  nooit per ongeluk iets uit je uitzending verdwijnt.

## NONSTOP-knop (alles automatisch verder)

Is het afspelen gestopt, of wil je de zender aan zichzelf overlaten? Druk op
**NONSTOP** (of de toets **O**):

- automatisch doorstarten (AUTO) en automatisch bijplannen gaan aan;
- is de playlist (bijna) leeg, dan wordt hij meteen gevuld vanaf het huidige uur,
  en de muziek start direct;
- uren met een uurklok volgen de weekplanning. Uren **zonder** uurklok krijgen
  muziek uit de *nonstop-collecties* (⚙ Instellingen → Uurklok), met dezelfde
  rotatieregels en zonder wat in je nonstop-filter staat. Standaard zijn dat alle
  collecties behalve jingles, reclames, sweepers en dergelijke;
- nog een keer drukken zet nonstop uit: wat in de playlist staat, speelt af, maar
  er wordt niets meer bijgepland.

## Naadloos aansluiten (live-opnames, mixen, medleys)

Nummers die op de opname in elkaar overlopen, zoals een live-cd of een mix, kun
je **naadloos** zetten: rechtsklik → *⇥ Naadloos aansluiten*. Met Ctrl/Shift kun
je een heel album tegelijk selecteren.

- Zo'n nummer speelt van het allereerste tot het allerlaatste moment: geen stilte
  overgeslagen, geen fade en geen overlap.
- Het volgende item start precies waar het nummer eindigt (getest: binnen
  ±0,03 s).
- In de lijst staat een ⇥ achter de titel.
- Naadloze nummers die achter elkaar spelen (een plaatkant, een live-set) houden
  het volume van het eerste nummer. *Gelijk volume* maakt zo geen sprong op de
  overgang; de player toont dan *⇥ +x dB*.
- Ideaal voor vinyl-opnames die per nummer zijn geknipt: de plaat loopt door zoals
  hij is opgenomen, met het geknisper ertussen.
- Aanzetten mag de beheerder, of iemand met uploadrechten op die collectie. Het
  geldt voor iedereen die het nummer draait.

## Probleem melden (feedback op nummers)

Klanten en dj's kunnen in de studio een nummer dat niet goed is **doorgeven aan de
beheerder**: sleep het (uit de database of de playlist) naar het vak **⚑ Probleem
melden**, of rechtsklik → *Probleem melden…*. Ze kiezen wat er mis is (slechte
kwaliteit, verkeerde titel/artiest, begint of stopt verkeerd, te zacht/hard, verkeerd
nummer, anders) en kunnen een toelichting typen.

De eigenaar en beheerders zien rechtsboven in de studio een oranje **⚑** met het aantal
open meldingen (en een Windows-melding bij een nieuwe). Klik erop → *Meldingen* in de
bibliotheek: beluister het nummer, pas titel/artiest aan, of **vervang het bestand**
door een goede versie. Het blijft hetzelfde nummer, dus playlists, jingle-knoppen en
uurklokken blijven werken; cue-punten, volume en genre worden opnieuw gemeten. Zet de
melding daarna op *Opgelost*.

## Muziek wensen

Mis je een nummer in de database? In de studio rechtsboven op **♪** (of in het menu:
*Muziek wensen…*): vul artiest, titel en eventueel een toelichting in en klik op
*Wens doorgeven*. Onder *Mijn wensen* zie je wat ermee gebeurd is: *aangevraagd*,
*✓ toegevoegd* of *niet mogelijk*, met het bericht van de beheerder. Een open wens kun
je intrekken met ✕.

De beheerder ziet de wensen bij *⚑ Meldingen & wensen* in de bibliotheek (en telt ze
mee in de oranje ⚑ in de studio) en zet ze op *Toegevoegd* of *Niet mogelijk*.

## Nonstop-filter (wat je nooit in de uurklok wilt)

Per persoon stel je in wat de automatische planning (uurklok / 24/7) nooit kiest.
Zelf toevoegen aan de playlist blijft altijd mogelijk.

- **Rechtsklik** op een nummer in de database of de playlist → 🚫 *Dit nummer niet
  in de nonstop*, *Artiest niet in de nonstop* of *Genre niet in de nonstop*.
  Met Ctrl/Shift meerdere nummers selecteren kan ook.
- ⚙ Instellingen → *Nonstop-filter*: regels toevoegen op **genre**, **artiest** of
  **map / bestandsnaam** (werkt als "bevat": *kerst* filtert ook de map
  *Kerst 2024*), en met ✕ weer toestaan.
- Gefilterde nummers hebben een 🚫 in de database; *🚫 Niet in nonstop* in de
  collectiekeuze toont ze allemaal.
- Genres komen uit de tags van de bestanden (ook voor muziek die al in de database
  stond; dat wordt op de achtergrond ingelezen). WAV-rips hebben vaak geen genre:
  filter dan op map of artiest.
- Ieder heeft zijn eigen filter; het filter van een klant geldt niet voor jou.

## Geluidsmeter

Bovenin de studio staat een stereo meter (L/R, −48 tot 0 dBFS). Hij meet wat er
echt naar buiten gaat: beide players en het jingle paneel samen, ná gelijk volume
en fades (voorbeluisteren telt niet mee). Groen is goed, geel vanaf −12, rood vanaf
−3; het witte streepje houdt de hoogste piek 1,5 seconde vast. Kleuren L en R rood,
dan raakt het signaal de 0 dB (vervorming).

## Gelijk volume (loudness-normalisatie)

Elk nummer en elke jingle klinkt even hard, ook als de ene opname veel zachter
gemasterd is dan de andere. De server meet elk bestand één keer volgens de
omroepnorm EBU R128 (luidheid in LUFS en de echte piek). De studio past daarna
per nummer het volume aan tijdens het afspelen; **de bestanden zelf veranderen
nooit**.

- Aan/uit en het doelvolume staan in de studio onder ⚙ Instellingen → *Afspelen*.
  −16 LUFS is standaard en past bij een webstream; kies −23 LUFS als er nog een
  eigen audioprocessor achter zit.
- Zachte nummers gaan maximaal 6 dB omhoog en nooit verder dan −1 dB onder de
  maximale piek, dus er gaat niets vervormen. Harde nummers gaan gewoon omlaag.
- Op elke player staat hoeveel er gecorrigeerd wordt (bijv. *+3,2 dB*).
- Nieuwe muziek is binnen een minuut gemeten. Een bestaande bibliotheek wordt op
  de achtergrond gemeten (ca. 2 seconden per nummer, één processorkern: 200.000
  nummers duurt een paar dagen). Bij Instellingen zie je hoe ver het is; nummers
  die nog niet gemeten zijn, spelen ongewijzigd af.

## Zuinige modus (MP3 320)

In de studio onder ⚙ Instellingen → *Geluidskwaliteit*:

- **Automatisch** (standaard): in je eigen netwerk het verliesvrije origineel,
  via internet een MP3 320-versie.
- **Altijd origineel** of **altijd zuinig**.

MP3 320 is ongeveer 1,5–3× kleiner dan FLAC (hoe drukker de muziek, hoe meer
winst), dus evenveel meer klanten tegelijk op dezelfde uploadsnelheid. De
server zet elk nummer één keer om en bewaart de MP3 in `data/audio/cache`
(standaard maximaal 20 GB, `CACHE_MAX_GB`; wat het langst niet gevraagd is,
gaat er eerst uit). Downloads, M3U-links en de netwerkschijf leveren altijd het
origineel. De tussenopslag hoort niet bij de back-up; hij vult zich vanzelf.

## Grote bibliotheken (200.000+ nummers)

Getest met 200.000 nummers en 150.000 gedraaide items: de studio laadt in
± 0,3 s, zoeken duurt ± 0,2 s en een uur plannen met de uurklok ± 0,2 s. De
studio en de klok-editor laden nooit de hele bibliotheek: zoeken gebeurt op de
server (300 resultaten per keer, verfijn door verder te typen) en alleen de
nummers in je playlist en jingle paneel worden opgehaald.

Reken bij 200.000 nummers op ± 5 TB aan FLAC (± 8 TB als WAV). De eerste
import leest alles van de NAS en zet WAV om naar FLAC; dat kan enkele dagen
duren. Hij gebruikt meerdere processorkernen, je kunt gewoon doorwerken, en
na een onderbreking gaat hij verder waar hij was.

## WAV of FLAC?

FLAC. Het is verliesvrij (bit-voor-bit dezelfde audio als de WAV), ongeveer
40–50% kleiner, en titel/artiest worden betrouwbaar in het bestand bewaard.
Alle moderne browsers en mAirList spelen FLAC af. Uploads en de import zetten
WAV/AIFF daarom automatisch om (uit te zetten met `CONVERT_TO_FLAC=false`).
Lukt de omzetting niet (bijv. 32-bit float WAV), dan wordt het origineel bewaard.

## Pakketten en proefweek (klanten)

Geef elke klant een **pakket** (Basis, Standaard of Pro) en bepaal per collectie
vanaf welk pakket hij zichtbaar is. Dan hoef je per klant niets meer aan te vinken.

1. **Collectie** (Bibliotheek → collectie openen) → *Zichtbaar voor pakket*:
   - *Basis, Standaard en Pro* — bijv. Piratenmuziek, Nederlandstalig
   - *Standaard en Pro* — bijv. Polka, Instrumentaal, Nieuw deze maand
   - *Alleen Pro*
   - *Alleen wie je aanvinkt* (standaard) — bijv. de eigen jinglemap van één klant
2. **Uitnodigen** (Gebruikers): kies het pakket en *1 week gratis proberen*. De
   proefweek telt vanaf het moment van uitnodigen.
3. Na de einddatum kan de klant niet meer inloggen (melding: proefperiode of
   abonnement afgelopen) en stoppen ook koppelingen. Bij de klant staat in rood
   *Verlopen op …*.
4. **Betaald?** Klik *+1 maand* of *+1 jaar*: telt vanaf vandaag (of vanaf de
   huidige einddatum als die nog niet voorbij is). *Geen einddatum* = onbeperkt.
5. Extra collecties bovenop het pakket vink je nog steeds per klant aan, bijv.
   de eigen jinglemap met *Uploaden*.

Elke maand nieuwe muziek alleen voor Standaard en Pro: zet nieuwe nummers eerst in
een collectie *Nieuw* (*Standaard en Pro*) en verplaats ze na een maand naar de
vaste collecties.

## Beveiliging

- Alles via https (op de mini-pc via Caddy, zie hierboven); wachtwoorden worden
  gehasht (scrypt) opgeslagen. Zet nooit poort 3000 open in de router.
- Mensen komen alleen binnen via een persoonlijke uitnodigingslink (7 dagen
  geldig) en zien alleen de collecties die je aanvinkt.
- Standaard mag een lid **alleen afspelen in de studio**. Downloaden,
  M3U-links, de netwerkschijf en koppelen aan eigen software (mAirList) staan
  uit tot je per persoon *Mag downloaden en koppelen* aanvinkt.
- Blokkeren werkt meteen: sessie en koppelingen stoppen direct.
- **Privacy van klanten**: wat klanten afspelen is van henzelf. In *Activiteit*
  zie je alleen downloads, koppelingen en uploads, niet wat klanten draaien.
- Elke klant heeft een eigen **"Nu op de radio"**-pagina (privélink in de
  studio-instellingen) en een eigen uurklok-rotatie; jouw pagina is `/nu.html`.

Eerlijk is eerlijk: wat iemand in zijn browser hoort, kan hij met moeite altijd
opnemen. Geen enkel systeem voorkomt dat volledig; deze opzet maakt het
kopiëren lastig en laat zien wie wat gebruikt.

## Online zetten op Render (alternatief)

1. Render → **New → Blueprint** → kies deze repository en zet *Blueprint path*
   op `audio-vault/render.yaml`.
2. Render maakt de webservice, de PostgreSQL-database en een schijf van 10 GB
   voor de audio. (Een schijf vereist een betaald *Starter*-abonnement.)
3. Vul na de eerste deploy `PUBLIC_URL` in met het adres van je service, bijv.
   `https://audio-vault.onrender.com`, of je eigen domein.
4. Open het adres: je maakt eerst het **eigenaarsaccount** aan.

Elders hosten kan ook (VPS, Railway, Fly.io): je hebt Node 18+, een
PostgreSQL-database en een blijvende map voor de bestanden nodig.

## Lokaal draaien

```bash
cd audio-vault
npm install
cp .env.example .env      # vul DATABASE_URL en SESSION_SECRET in
npm start                 # http://localhost:3000
```

De tabellen worden bij het opstarten automatisch aangemaakt (`src/schema.sql`).

## mAirList koppelen (netwerkschijf)

1. Ga in Audio Vault naar **Koppelen** → *Token aanmaken*.
2. Windows Verkenner → *Deze pc* → **Netwerkverbinding maken** → kies een
   letter (bijv. `V:`) en vul het adres `https://<jouw-adres>/dav/` in.
3. Vink *Verbinding maken met andere referenties* aan en log in met je e-mail
   en de token als wachtwoord.
4. mAirList → Configuratie → database/bibliotheek → map `V:\` (of een
   submap) toevoegen en laten scannen.

De schijf is alleen-lezen; uploaden en verwijderen gaat via de website zodat de
database altijd klopt. Bestandsnamen zien er zo uit:
`Artiest - Titel [12].mp3` — het nummer tussen haken is het vaste ID.

Tip: Windows staat standaard downloads via WebDAV tot ±50 MB per bestand toe.
Heb je grotere bestanden (lange mixen in WAV), verhoog dan
`FileSizeLimitInBytes` onder
`HKLM\SYSTEM\CurrentControlSet\Services\WebClient\Parameters` en herstart de
*WebClient*-service.

## API-overzicht

| Route | Wat |
| --- | --- |
| `POST /api/setup` | eerste account (eigenaar) |
| `POST /api/login`, `/api/logout`, `GET /api/me` | inloggen |
| `GET/POST /api/users`, `PATCH/DELETE /api/users/:id` | gebruikers (beheer) |
| `POST /api/users/:id/invite` | nieuwe uitnodigings-/resetlink |
| `PUT /api/users/:id/access` | collectierechten van een lid |
| `GET/POST/PATCH/DELETE /api/collections` | collecties |
| `GET/POST /api/files`, `PATCH/DELETE /api/files/:id` | bestanden |
| `GET /api/files/:id/stream` | afspelen/downloaden in de browser |
| `POST/DELETE /api/me/token` | koppel-token maken/intrekken |
| `GET /m/:token/all.m3u8`, `/m/:token/collections/:id.m3u8` | playlists |
| `GET /m/:token/files/:id/:naam` | stream (met Range-ondersteuning) |
| `GET /m/:token/library.json` | catalogus |
| `/dav/` | WebDAV (PROPFIND/GET/HEAD) |

## Tests

```bash
TEST_DATABASE_URL=postgres://user@localhost:5432/vault_test npm test
```

De test doorloopt het hele pad: eigenaar aanmaken, uploaden, iemand uitnodigen,
rechten geven, M3U/stream/WebDAV gebruiken en blokkeren.

Let op: de test wist de tabellen in de opgegeven database.
