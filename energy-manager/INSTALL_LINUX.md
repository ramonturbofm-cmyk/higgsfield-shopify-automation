# Installeren op Linux (zelfstandig)

Werkt op x86-64 en ARM64 (o.a. Raspberry Pi, zie [INSTALL_RASPBERRY_PI.md](INSTALL_RASPBERRY_PI.md)).
Bediening via de browser (`http://<computer>:8080`), op telefoon, tablet of pc. Geen Windows nodig.

## Met Docker (aanbevolen)

```bash
git clone https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation.git
cd higgsfield-shopify-automation/energy-manager
cp .env.example .env          # optioneel: tijdzone, poort (ENTSOE_TOKEN alleen als u ENTSO-E kiest)
./install.sh                  # installeert Docker indien nodig, bouwt en start (productie)
# of: ./install.sh demo
```

Gegevens staan in het Docker-volume `ems-data` (blijven bewaard bij bijwerken en opnieuw aanmaken).
De container herstart vanzelf (`restart: unless-stopped`).

| Opdracht | Doet |
|---|---|
| `./install.sh status` / `logs` | status en live logboek |
| `./install.sh update` | back-up → nieuwe versie → healthcheck → automatische rollback bij een fout |
| `./install.sh backup` | back-up naar `./backups/` (zonder sleutels) |

## Zonder Docker (systemd)

```bash
sudo useradd --system --home /var/lib/energy-manager energy-manager
sudo python3 -m venv /opt/energy-manager
sudo /opt/energy-manager/bin/pip install ./energy-manager
sudo cp energy-manager/deploy/energy-manager.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now energy-manager
```

De unit gebruikt `Type=notify`, een watchdog (60 s) en `Restart=always`; gegevens in `/var/lib/energy-manager`.

## Eerste gebruik

Open `http://<computer>:8080` en maak het beheerdersaccount aan (minimaal 8 tekens). In productie worden
nooit nepwaarden getoond: zonder netmeter ziet u "Geen primaire netmeter ingesteld".

## Netwerk en beveiliging (LAN/TLS-eisen)

* Zet poort 8080 **nooit** open naar internet. Toegang van buiten: VPN (WireGuard/Tailscale).
* De server spreekt HTTP op het LAN. Wilt u HTTPS (aanbevolen bij wifi met gasten), zet er een reverse proxy
  voor (bijv. Caddy of nginx) die `X-Forwarded-Proto: https` doorgeeft; de sessiecookie krijgt dan `Secure`.
* Inloggen gebeurt met een HttpOnly-cookie en CSRF-bescherming; scripts gebruiken een API-token
  (*Instellingen → Gebruikers → API-tokens*) in de `Authorization`-header.
* Wachtwoorden/tokens van apparaten staan versleuteld in de datamap, nooit in de configuratie.

## Getest

`deploy/acceptance-linux.sh` draait in CI op een schone Ubuntu-runner: Docker (amd64), Docker (arm64 via
QEMU) en systemd. Het controleert start, web-UI, eerste account, cookie + CSRF, geen nepdata, herstart,
herstel na een crash en bijwerken met behoud van gegevens.

## Prijzen en cloud

* Prijsbron: *Instellingen → Marktgegevens & prognoses*. EnergyZero werkt zonder account of token; klik
  op "Verbinding testen" om te zien of de prijzen binnenkomen.
* Energy Manager Cloud is optioneel (*Instellingen → Cloud*, niveau Uitgebreid). De installatie maakt dan
  alleen een uitgaande HTTPS-verbinding; open geen poorten.
