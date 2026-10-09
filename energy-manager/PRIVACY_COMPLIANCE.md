# Privacy en compliance (AVG/GDPR) — Energy Manager 0.5.0

Dit document beschrijft wat de software doet om persoonsgegevens te beschermen en welke punten **juridisch
moeten worden beoordeeld** voordat Energy Manager Cloud met echte klanten wordt gebruikt. Het is geen
juridisch advies.

## Uitgangspunten

* **Lokaal eerst.** Meetgegevens, historie, apparaatgegevens, contract en planning blijven op de lokale
  installatie. Zonder cloudkoppeling verlaat niets het huis, behalve de verzoeken naar de gekozen
  prijs- en weerbron (EnergyZero, ENTSO-E, Open-Meteo); die bevatten geen persoonsgegevens (alleen
  datum, marktgebied, en voor het weer de coördinaten van de locatie).
* **Dataminimalisatie in de cloud.** Standaard alleen: account (e-mail, naam), organisatie, locatienaam,
  installatie (naam, versie, platform, online/offline, EMS-status) en apparaatnamen/-categorieën.
  Actuele waarden (netvermogen, zonne-energie, batterijlading) alleen als **zowel** de klant in de cloud
  toestemming geeft (`sync_consent`) **als** de installatie het lokaal toestaat (`cloud.share_summary`).
  Intrekken van de toestemming verwijdert de opgeslagen waarden.
* **Platformbeheer ≠ toegang tot klantgegevens.** Het beheerportaal toont alleen aantallen, versies,
  abonnementen en licenties, plus de naam van de organisatie en het e-mailadres van de eigenaar (nodig
  voor klantbeheer en facturatie). Locaties, apparaten, waarden en bediening zijn alleen toegankelijk met
  een lidmaatschap of met door de klant verleende, tijdelijke supporttoegang (max. 72 uur, intrekbaar,
  gelogd).

## Rechten van betrokkenen (in de software)

| Recht | Hoe |
|---|---|
| Inzage / dataportabiliteit | Account → "Gegevens exporteren (JSON)" (`GET /api/v1/auth/me/export`) |
| Verwijdering | Account → "Account verwijderen": sessies, tokens, lidmaatschappen en (als enig lid) de organisatie met locaties, installaties en waarden worden verwijderd; het account wordt geanonimiseerd. Gekoppelde installaties blijven lokaal werken |
| Rectificatie | Naam/e-mail via support (e-mail wijzigen in de portal: nog niet gebouwd) |
| Bezwaar / toestemming intrekken | `sync_consent` uitzetten; lokaal `share_summary` uitzetten; ontkoppelen |

## Beveiliging (art. 32)

Versleuteling in transport (TLS via de proxy; installaties alleen via HTTPS), wachtwoorden met Argon2id,
MFA (TOTP) met versleuteld opgeslagen geheimen, MFA verplicht voor platformbeheer en instelbaar per
organisatie, korte sessies, brute-forcebescherming, rolgebaseerde toegang (RBAC) met fijnmazige rechten,
tenant-isolatie (getest), auditlog per organisatie, back-ups (procedure in `cloud/DEPLOYMENT.md`).

## Bewaartermijnen (instelbaar)

| Gegevens | Termijn |
|---|---|
| Auditlog | 365 dagen (`audit_retention_days`) |
| Gesynchroniseerde waarden | 30 dagen (`telemetry_retention_days`) |
| Verlopen sessies, e-mailtokens, koppelcodes | 1 dag na verlopen |
| Accounts | tot verwijdering door de gebruiker |
| Facturen | wettelijke bewaarplicht (7 jaar) — **nog te regelen bij de boekhouding / betaalprovider** |

## Hosting

Voorkeur voor hosting en e-mail binnen de EU (`data_region = "eu"`). De keuze van hosting-, e-mail- en
betaalpartij bepaalt de subverwerkers.

## Ter beoordeling door een jurist (vóór livegang)

1. **Rolverdeling**: is de aanbieder van Energy Manager Cloud verwerker (voor de klant) of
   verwerkingsverantwoordelijke (voor accountgegevens)? Waarschijnlijk beide voor verschillende gegevens.
2. **Verwerkersovereenkomst (DPA)** met zakelijke klanten en installateurs; lijst van subverwerkers
   (hosting, e-mail, betaalprovider).
3. **Privacyverklaring** en **algemene voorwaarden** (inclusief beschikbaarheid, aansprakelijkheid bij
   bediening op afstand, en dat lokale veiligheid altijd voorgaat).
4. **Grondslag** per verwerking: overeenkomst (account, licentie), toestemming (synchronisatie van
   waarden), gerechtvaardigd belang (beveiligingslog, IP-adressen in het auditlog).
5. **Energiedata als persoonsgegevens**: verbruiksprofielen kunnen aanwezigheid en gedrag onthullen —
   DPIA (gegevensbeschermingseffectbeoordeling) overwegen, zeker bij bediening op afstand en zakelijke
   klanten met veel locaties.
6. **Supporttoegang**: procedure en logging zijn aanwezig; vastleggen wie support mag verlenen en hoe
   klanten worden geïnformeerd.
7. **Doorgifte buiten de EU**: vermijden; anders passende waarborgen.
8. **Cookies**: alleen functionele cookies (sessie, CSRF) — geen tracking; toets of een cookiemelding
   nodig is.
9. **Gegevens van derden**: prijsdata (EnergyZero, ENTSO-E) wordt niet door de cloud herverdeeld; elke
   installatie haalt zelf op. Een centrale prijsdienst is alleen ontworpen (ARCHITECTURE.md §21), niet
   gebouwd; vóór herdistributie zijn toestemming/licentievoorwaarden van de bron nodig.
10. **Meldplicht datalekken**: procedure (72 uur) en contactpersoon vastleggen.
11. **Bewaartermijn facturen** en boekhoudkundige verplichtingen.
12. **Minderjarigen**: n.v.t. verwacht (accounts voor woningeigenaren/bedrijven); in de voorwaarden opnemen.
