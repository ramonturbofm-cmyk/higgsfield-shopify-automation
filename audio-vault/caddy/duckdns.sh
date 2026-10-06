#!/bin/sh
# Keeps <name>.duckdns.org pointing at the public IP address of this connection
# (docker-compose.duckdns.yml). DuckDNS answers OK or KO; the token is never logged.
set -u
while true; do
  r=$(curl -fsS --max-time 30 "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=" 2>&1 | head -c 200)
  echo "$(date '+%Y-%m-%d %H:%M') DuckDNS: ${r:-geen antwoord}"
  sleep 300
done
