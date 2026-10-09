# Energy Manager Cloud — deployment

> **Status 0.5.0: niet productierijp verklaard.** De code, de tests (SQLite en PostgreSQL 16) en het
> Docker-image zijn gecontroleerd. Hosting, DNS, e-mail, back-ups, monitoring, betalingen, juridische
> documenten en een end-to-endtest op de echte omgeving zijn **nog niet** gedaan. Zie
> [Wat nog ontbreekt](#wat-nog-ontbreekt-externe-zaken).

De cloud is optioneel. Het lokale Energy Manager-systeem (Windows, Raspberry Pi, Linux, nodes) werkt
volledig zonder cloud; een cloudstoring, een verlopen licentie of een ingetrokken koppeling zet alleen
cloudfuncties uit.

## Architectuur in het kort

```
klant-browser ──HTTPS──► Caddy (TLS, 80/443) ──► API (uvicorn, 8800, intern) ──► PostgreSQL (intern)
lokale EMS ──uitgaand HTTPS──┘   (heartbeat, opdrachten ophalen, bevestigen; nooit inkomend bij de klant)
```

* Alleen de proxy is vanaf internet bereikbaar. API en database zitten op een intern Docker-netwerk.
* Bij klanten hoeven geen poorten open: de installatie maakt zelf de uitgaande verbinding.

## Installeren (één server)

Vereisten: een Linux-server (bij voorkeur in de EU) met Docker en Compose, een publiek IP-adres, een
DNS-naam (A/AAAA-record) naar die server, en een SMTP-dienst.

```bash
git clone https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation.git
cd higgsfield-shopify-automation/energy-manager/cloud
cp .env.example .env && chmod 600 .env
docker compose build
docker compose run --rm api emcloud gen-key        # -> EMC_ENCRYPTION_KEYS in .env
openssl rand -base64 32                            # -> EMC_DB_PASSWORD in .env
# vul EMC_DOMAIN, EMC_BASE_URL en de SMTP-gegevens in .env in
docker compose up -d
docker compose exec api emcloud create-admin beheer@uwdomein.nl   # platformbeheerder (MFA verplicht bij eerste login)
```

Caddy haalt automatisch een Let's Encrypt-certificaat op. In productie (`EMC_ENV=production`) weigert de
API te starten zonder encryptiesleutel, https-adres, beveiligde cookies, SMTP en PostgreSQL.

Daarna in het beheerportaal (`/#/admin`): prijzen per abonnement instellen (staan bewust niet in de code).

## Beheer

| Taak | Opdracht |
|---|---|
| Status | `docker compose ps`, `curl https://<domein>/api/v1/health` |
| Logboek | `docker compose logs -f api` |
| Bijwerken | `git pull && docker compose build && docker compose up -d` |
| Back-up database | `docker compose exec db pg_dump -U emcloud emcloud \| gzip > emcloud-$(date +%F).sql.gz` |
| Terugzetten | `gunzip -c backup.sql.gz \| docker compose exec -T db psql -U emcloud emcloud` |
| Sleutel roteren | nieuwe sleutel vooraan in `EMC_ENCRYPTION_KEYS` zetten (oude erachter laten staan), herstarten |
| Onderhoud (perioden, opschonen) | draait elke 10 min in de API; handmatig: `docker compose exec api emcloud maintenance` |

**Back-ups**: plan dagelijks `pg_dump` naar opslag buiten de server (versleuteld, EU) en test het
terugzetten. De encryptiesleutels horen in een aparte kluis: zonder sleutel zijn MFA-geheimen onbruikbaar
(gebruikers moeten dan MFA opnieuw instellen); met de sleutel naast de back-up is de versleuteling zinloos.

## Beveiliging (wat de software doet)

* Wachtwoorden: Argon2id (`argon2-cffi`), minimaal 10 tekens; MFA: TOTP (`pyotp`), geheimen versleuteld
  (Fernet, sleutelrotatie); geen eigen cryptografie.
* Sessies: willekeurige tokens, alleen de SHA-256 opgeslagen; HttpOnly + Secure + SameSite=Strict cookie
  met CSRF-token; inactief na 12 u, uiterlijk na 30 dagen; zelf in te zien en in te trekken.
* Brute force: blokkade na 5 mislukte pogingen (15 min) plus limieten per IP en per account; koppelcodes:
  10 pogingen per 10 min per IP, 15 min geldig, eenmalig.
* Tenant-isolatie: elk verzoek via `org_access` (lidmaatschap of tijdelijke supporttoegang); anders 404.
* Platformbeheer ziet geen klantlocaties, apparaten of verbruik en kan niets bedienen.
* Opdrachten op afstand: alleen als de locatie dat toestaat, de licentie dat omvat, de installatie het
  lokaal toestaat, en altijd via de lokale `can_execute` + SafetyValidator.
* Beveiligingsheaders (CSP zonder inline scripts, HSTS, X-Frame-Options, no-referrer), geen API-docs in
  productie, container als niet-root gebruiker.
* Rate limiting is per proces: draai één API-instantie, of voeg een gedeelde opslag (Redis) toe voordat u
  horizontaal schaalt.

## Wat nog ontbreekt (externe zaken)

Deze onderdelen kan de software niet zelf regelen; ze zijn nodig vóór echte klanten:

| Onderdeel | Wat er nodig is |
|---|---|
| Hosting | Server/VPS in de EU (bijv. NL/DE), met Docker; verwerkersovereenkomst met de hostingpartij |
| DNS | Domeinnaam + A/AAAA-record naar de server (`EMC_DOMAIN`) |
| E-mail | SMTP-dienst (EU) met SPF, DKIM en DMARC voor het afzenderdomein (`EMC_SMTP_*`) |
| Geheimen | `EMC_ENCRYPTION_KEYS`, `EMC_DB_PASSWORD` — genereren op de server, bewaren in een kluis |
| Back-ups | Dagelijkse, versleutelde, externe database-back-up + hersteltest |
| Monitoring | Uptime-check op `/api/v1/health`, logverzameling, waarschuwing bij fouten |
| Betalingen | Account bij een professionele betaalprovider (bijv. Mollie of Stripe); koppeling via `billing.PaymentProvider` (webhooks met handtekening). Tot die tijd: handmatig verlengen in het beheerportaal |
| Juridisch | Privacyverklaring, verwerkersovereenkomst (DPA) met klanten, algemene voorwaarden, cookieverklaring — zie [../PRIVACY_COMPLIANCE.md](../PRIVACY_COMPLIANCE.md) |
| Prijzen | Bedragen per abonnement in het beheerportaal |
| Test | End-to-endtest op de echte omgeving (registratie met echte e-mail, koppelen van een echte installatie, opdracht op afstand, back-up terugzetten) |
| Pentest | Onafhankelijke beveiligingstest vóór livegang (aanbevolen) |

Voor de livegang heb ik van u alleen nodig: het domein, de hostingkeuze, SMTP-gegevens en (later) de
betaalprovider. Geheimen genereert u zelf op de server; die horen niet in chat of in Git.
