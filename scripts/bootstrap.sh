#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[1/4] Docker"
docker --version
docker compose version

echo "[2/4] .env"
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found."
  echo "Create it first:"
  echo "  cp .env.example .env"
  echo "Then replace all CHANGE_ME values."
  exit 2
fi

if grep -q "CHANGE_ME" .env; then
  echo "ERROR: .env still contains CHANGE_ME values."
  exit 3
fi

# Load only simple key=value values required by this script.
set -a
# shellcheck disable=SC1091
source .env
set +a

NETWORK_NAME="${SHARED_NETWORK_NAME:-ai-shared}"

echo "[3/4] Docker network: ${NETWORK_NAME}"
if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
  echo "Network already exists."
else
  docker network create "${NETWORK_NAME}"
fi

echo "[4/4] Compose validation"
docker compose config --quiet
echo "COMPOSE OK"

echo
echo "Bootstrap completed."
echo "Next:"
echo "  docker compose pull"
echo "  docker compose up -d"
echo "  docker compose ps"
