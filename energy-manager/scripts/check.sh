#!/usr/bin/env bash
# Lint + tests + korte rooksimulatie. Gebruik: scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
ruff check backend tests
python -m pytest -q
python -m ems.simulator --config config/ems.example.yaml --start 2026-06-15 --days 0.5 --step 30 --show-decisions 0 >/dev/null
echo "OK"
