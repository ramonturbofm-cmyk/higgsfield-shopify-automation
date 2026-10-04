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

Sneltoetsen: spatie = start/volgende, N = volgende, P = pauze, F = fade,
A = auto aan/uit.

Tip: houd de studio in een eigen browservenster open. Instellingen, playlist en
jingle paneel worden per gebruiker op de server bewaard; de keuze van
geluidskaarten per computer.

## Online zetten op Render (aanbevolen)

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
