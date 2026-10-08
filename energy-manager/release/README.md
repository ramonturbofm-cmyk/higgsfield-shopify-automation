# Release — Energy Manager voor Windows

De Windows-installer wordt door GitHub Actions gebouwd en **pas gepubliceerd na een geslaagde
acceptatietest op een schone Windows-machine** (`windows/acceptance.ps1`).

| | |
|---|---|
| Bestand | `EnergyManagerSetup.exe` |
| Download | https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation/releases/download/energy-manager-windows/EnergyManagerSetup.exe |
| Releasepagina | https://github.com/ramonturbofm-cmyk/higgsfield-shopify-automation/releases/tag/energy-manager-windows |
| Testuitslag | `acceptance-results.txt` bij dezelfde release |
| Ondertekening | UNSIGNED DEVELOPMENT BUILD (geen certificaat geconfigureerd) |

Het installatiebestand zelf staat niet in git (binaire bestanden van ~70 MB horen in een release, niet in de
broncode). Raspberry Pi / Linux: zie `../README.md` → *Installeren op de Raspberry Pi* (`./install.sh`).
