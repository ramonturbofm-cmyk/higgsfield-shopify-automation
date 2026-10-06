#!/usr/bin/env bash
# Energy Manager — installer / updater for Raspberry Pi OS (64-bit) and other Linux hosts.
#   ./install.sh            install (Docker) and start
#   ./install.sh demo       install and start in Demo Mode
#   ./install.sh update     backup -> update -> health check -> automatic rollback on failure
#   ./install.sh status | logs | backup | watchdog | uninstall
set -euo pipefail
cd "$(dirname "$0")"

PORT="${EMS_PORT:-8080}"
c_ok() { printf '\033[32m%s\033[0m\n' "$*"; }
c_err() { printf '\033[31m%s\033[0m\n' "$*" >&2; }

need_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker wordt geïnstalleerd (officieel script van get.docker.com)…"
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER" || true
  fi
  if ! docker compose version >/dev/null 2>&1; then
    c_err "Docker Compose v2 ontbreekt (docker compose). Installeer docker-compose-plugin."
    exit 1
  fi
  if [ "$(uname -m)" != "aarch64" ] && [ "$(uname -m)" != "x86_64" ]; then
    c_err "Waarschuwing: $(uname -m) wordt niet getest; gebruik Raspberry Pi OS 64-bit."
  fi
}

dc() { if docker info >/dev/null 2>&1; then docker compose "$@"; else sudo docker compose "$@"; fi; }
dk() { if docker info >/dev/null 2>&1; then docker "$@"; else sudo docker "$@"; fi; }

wait_healthy() {
  for _ in $(seq 1 90); do
    if curl -fs "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

show_url() {
  local ip
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  c_ok "Energy Manager draait: http://${ip:-<ip-adres>}:${PORT}  (of http://$(hostname).local:${PORT})"
  echo "Open dit adres in de Windows-app of in een browser op hetzelfde netwerk."
}

cmd_install() {
  need_docker
  [ -f .env ] || cp .env.example .env
  if [ "${1:-}" = "demo" ]; then sed -i 's/^EMS_MODE=.*/EMS_MODE=demo/' .env; fi
  dc up -d --build
  if wait_healthy; then show_url; else c_err "Server niet gezond; zie: ./install.sh logs"; exit 1; fi
}

cmd_backup() {
  local name="backup-$(date +%Y%m%d-%H%M%S).zip"
  dc exec -T ems ems backup "/data/backups/${name}"
  mkdir -p backups
  dk cp "energy-manager:/data/backups/${name}" "backups/${name}"
  c_ok "Back-up: backups/${name}"
}

cmd_update() {
  need_docker
  echo "1/5 back-up maken"; cmd_backup || c_err "back-up mislukt (eerste installatie?)"
  echo "2/5 huidige versie bewaren"; dk image tag energy-manager:latest energy-manager:previous 2>/dev/null || true
  echo "3/5 nieuwe versie ophalen"; if [ -d .git ]; then git pull --ff-only; fi
  echo "4/5 bouwen en starten"; dc up -d --build
  echo "5/5 gezondheid controleren"
  if wait_healthy; then c_ok "Update geslaagd (configuratie en historie zijn behouden in volume ems-data)."; show_url; return; fi
  c_err "Nieuwe versie niet gezond — terug naar de vorige versie."
  dk image tag energy-manager:previous energy-manager:latest
  dc up -d --no-build
  wait_healthy && c_ok "Vorige versie draait weer." || c_err "Ook de vorige versie start niet; zie ./install.sh logs"
  exit 1
}

cmd_watchdog() {
  # Hardware watchdog of the Raspberry Pi: the board reboots if the OS hangs.
  if ! grep -q '^dtparam=watchdog=on' /boot/firmware/config.txt 2>/dev/null; then
    echo 'dtparam=watchdog=on' | sudo tee -a /boot/firmware/config.txt >/dev/null
  fi
  sudo sed -i 's/^#\?RuntimeWatchdogSec=.*/RuntimeWatchdogSec=15/' /etc/systemd/system.conf
  grep -q '^RuntimeWatchdogSec=' /etc/systemd/system.conf || echo 'RuntimeWatchdogSec=15' | sudo tee -a /etc/systemd/system.conf
  c_ok "Hardware-watchdog ingesteld; herstart de Pi om te activeren."
}

case "${1:-install}" in
  install) cmd_install ;;
  demo) cmd_install demo ;;
  update) cmd_update ;;
  status) dc ps; curl -fs "http://127.0.0.1:${PORT}/healthz" && echo ;;
  logs) dc logs --tail 200 -f ems ;;
  backup) cmd_backup ;;
  watchdog) cmd_watchdog ;;
  uninstall) dc down; c_ok "Gestopt. Gegevens staan nog in volume ems-data (verwijder met: docker volume rm energy-manager_ems-data)." ;;
  *) echo "gebruik: $0 [install|demo|update|status|logs|backup|watchdog|uninstall]"; exit 1 ;;
esac
