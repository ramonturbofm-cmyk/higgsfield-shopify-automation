#!/usr/bin/env bash
# Standalone Linux / Raspberry Pi acceptance test (audit test 14). Runs in CI on a clean Ubuntu runner.
#
#   deploy/acceptance-linux.sh docker <image> [platform]   e.g. energy-manager:ci linux/arm64 (via QEMU)
#   deploy/acceptance-linux.sh systemd                      native install with the systemd unit
#
# Checks: server starts, web interface served, first account, login (cookie session + CSRF), settings
# saved, restart keeps settings and account, automatic restart after a crash, new container/upgrade on
# the same data keeps everything, production mode shows no fake data.
set -euo pipefail
MODE="${1:?docker|systemd}"
BASE="http://127.0.0.1:8080"
JAR="$(mktemp)"
PASS=0
pass() { echo "PASS  $*"; PASS=$((PASS + 1)); }
fail() { echo "FAIL  $*"; dump_logs; exit 1; }
dump_logs() {
  if [ "$MODE" = docker ]; then docker logs ems-acc 2>&1 | tail -40 || true; else sudo journalctl -u energy-manager -n 40 --no-pager || true; fi
}
healthy() {
  for _ in $(seq 1 "${1:-90}"); do curl -fs "$BASE/healthz" >/dev/null 2>&1 && return 0; sleep 2; done
  return 1
}
csrf() { awk '$6 == "ems_csrf" {print $7}' "$JAR"; }
api() {   # api METHOD PATH [JSON]
  local m="$1" p="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -fsS -b "$JAR" -c "$JAR" -X "$m" -H "Content-Type: application/json" -H "X-CSRF-Token: $(csrf)" -d "$body" "$BASE/api/v1$p"
  else
    curl -fsS -b "$JAR" -c "$JAR" -X "$m" -H "X-CSRF-Token: $(csrf)" "$BASE/api/v1$p"
  fi
}
login() { rm -f "$JAR"; curl -fsS -c "$JAR" -H "Content-Type: application/json" -d '{"username":"beheer","password":"acceptatie-1234"}' "$BASE/api/v1/auth/login" >/dev/null; }

start() {
  if [ "$MODE" = docker ]; then
    docker run -d --name ems-acc --restart unless-stopped ${PLATFORM:+--platform "$PLATFORM"} \
      -v ems-acc-data:/data -p 127.0.0.1:8080:8080 "$IMAGE" >/dev/null
  else
    sudo systemctl start energy-manager
  fi
}
restart() { if [ "$MODE" = docker ]; then docker restart ems-acc >/dev/null; else sudo systemctl restart energy-manager; fi; }

if [ "$MODE" = docker ]; then
  IMAGE="${2:?image}"; PLATFORM="${3:-}"
  docker rm -f ems-acc >/dev/null 2>&1 || true; docker volume rm ems-acc-data >/dev/null 2>&1 || true
else
  sudo useradd --system --home /var/lib/energy-manager energy-manager 2>/dev/null || true
  sudo python3 -m venv /opt/energy-manager
  sudo /opt/energy-manager/bin/pip install -q .
  sudo cp deploy/energy-manager.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable energy-manager >/dev/null 2>&1
fi

# 1. Start ------------------------------------------------------------------------------------
start
healthy || fail "server did not become healthy"
curl -fs "$BASE/" | grep -q "<title>" || fail "web interface not served"
pass "server starts ($MODE${PLATFORM:+ $PLATFORM}); web interface served"

# 2. First account, cookie session, CSRF ------------------------------------------------------
curl -fsS -c "$JAR" -H "Content-Type: application/json" -d '{"username":"beheer","password":"acceptatie-1234"}' \
  "$BASE/api/v1/auth/setup" >/dev/null || fail "first account"
grep -q ems_session "$JAR" || fail "no session cookie"
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$JAR" -X PUT -H "Content-Type: application/json" \
  -d '{"site":{"name":"x"}}' "$BASE/api/v1/settings")
[ "$code" = 403 ] || fail "state change without CSRF token was not refused ($code)"
api PUT /settings '{"site":{"name":"Linux-acceptatie"}}' >/dev/null || fail "settings save"
api GET /settings | grep -q '"Linux-acceptatie"' || fail "setting not saved"
pass "first account, cookie session, CSRF enforced, settings saved"

# 3. Production mode without devices: no fake values ------------------------------------------
live=$(api GET /energy/live)
echo "$live" | grep -q '"no_primary_grid_meter"' || fail "expected 'no primary grid meter' in production"
echo "$live" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["flows"]["grid_w"] is None and d["price"]["import"] is None' \
  || fail "production mode shows values without devices"
pass "production mode: no fake data, 'Geen primaire netmeter ingesteld'"

# 4. Restart keeps data ----------------------------------------------------------------------
restart
healthy || fail "no restart"
login || fail "login after restart"
api GET /settings | grep -q '"Linux-acceptatie"' || fail "setting lost after restart"
pass "restart: account and settings kept"

# 5. Crash -> automatic restart --------------------------------------------------------------
if [ "$MODE" = systemd ]; then
  sudo systemctl kill -s KILL energy-manager; sleep 3
  healthy 60 || fail "service did not come back after a crash (Restart=always)"
  pass "crash (SIGKILL): systemd restarts the service"
else
  pid=$(docker inspect -f '{{.State.Pid}}' ems-acc)
  sudo kill -9 "$pid"; sleep 3
  healthy 60 || fail "container did not come back after a crash (restart: unless-stopped)"
  pass "crash (process killed): Docker restarts the container"
fi

# 6. New container / reinstall on the same data (upgrade) ------------------------------------
if [ "$MODE" = docker ]; then
  docker rm -f ems-acc >/dev/null
  start
else
  sudo systemctl stop energy-manager
  sudo /opt/energy-manager/bin/pip install -q --force-reinstall --no-deps .
  sudo systemctl start energy-manager
fi
healthy || fail "no start after upgrade"
login || fail "login after upgrade"
api GET /settings | grep -q '"Linux-acceptatie"' || fail "setting lost after upgrade"
pass "upgrade/new container on the same data: everything kept"

echo
echo "LINUX ACCEPTANCE ($MODE${PLATFORM:+ $PLATFORM}): PASS ($PASS checks)"
if [ "$MODE" = docker ]; then docker rm -f ems-acc >/dev/null; docker volume rm ems-acc-data >/dev/null; fi
