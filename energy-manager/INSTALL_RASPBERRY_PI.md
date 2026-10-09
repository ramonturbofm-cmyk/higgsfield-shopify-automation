# Installeren op een Raspberry Pi

Raspberry Pi 4 of 5 met **Raspberry Pi OS 64-bit** (Lite volstaat), bij voorkeur een SSD via USB 3 en een
netwerkkabel. De Pi draait het EMS 24/7 zelfstandig; bediening via de browser of de Windows-app.

> Status: het arm64-image is in CI gebouwd en getest **onder QEMU-emulatie**, niet op een fysieke Pi.
> Meld uw ervaringen (model, OS-versie) zodat dit in DEVELOPMENT.md kan worden bijgewerkt.

## Stappen

1. Raspberry Pi Imager → Raspberry Pi OS Lite (64-bit) → instellingen: hostnaam `energy-manager`, SSH aan,
   gebruiker en wachtwoord, tijdzone Europe/Amsterdam.
2. Inloggen via SSH en installeren:

   ```bash
   sudo apt-get update && sudo apt-get install -y git
   git clone https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation.git
   cd higgsfield-shopify-automation/energy-manager
   ./install.sh            # of: ./install.sh demo
   ./install.sh watchdog   # hardware-watchdog: herstart bij vastlopen
   ```

3. Open `http://energy-manager.local:8080` (of `http://<ip-van-de-pi>:8080`) en maak het beheerdersaccount aan.

## P1-meter en RS485

* **HomeWizard P1**: *Apparaten → Apparaat toevoegen → Slimme meter → HomeWizard*; wordt automatisch gevonden.
* **P1-kabel (USB)**: driver *DSMR P1*, poort `/dev/ttyUSB0`; in Docker de regel `devices:` in
  `docker-compose.yml` aanzetten.

## Samen met Windows (nodes)

De Pi kan gateway zijn (meet P1/RS485 en voert opdrachten uit) terwijl een andere computer beslist, of
alles zelf doen. Koppelen: op de Pi *Nodes → Koppelcode tonen*, op de andere computer *Nodes → Koppelen*.
Valt de verbinding weg, dan gaan de apparaten binnen 30 seconden terug naar hun eigen regeling.

## Bijwerken en back-up

`./install.sh update` maakt eerst een back-up en rolt automatisch terug als de nieuwe versie niet gezond
start. Verhuizen naar een nieuwe Pi: *Systeem → Back-up* met sleutels én wachtwoord, nieuwe Pi installeren,
back-up herstellen.

## Prijzen en cloud (0.5.0)

* Prijsbron: *Instellingen → Marktgegevens & prognoses*. EnergyZero werkt zonder account of token
  ("Verbinding testen" laat zien of de prijzen binnenkomen). Wat u zelf betaalt stelt u in bij
  *Energiecontract*.
* Energy Manager Cloud is optioneel (*Instellingen → Cloud*, niveau Uitgebreid): alleen een uitgaande
  HTTPS-verbinding, geen open poorten. Lokaal werkt alles ook zonder cloud.
