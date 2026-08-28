#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[1/4] Docker"
docker --version
docker compose version

echo "[2/4] Runtime secrets"

# shared-infrastructure intentionally uses a sparse .env:
# only real secrets and environment-specific overrides belong there.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "INFO: .env not found; checking exported process environment."
fi

required_vars=(
  N8N_DB_PASSWORD
  N8N_ENCRYPTION_KEY
  RABBITMQ_DEFAULT_PASS
)

missing=0

for var_name in "${required_vars[@]}"; do
  value="${!var_name:-}"

  if [[ -z "${value}" ]]; then
    echo "ERROR: ${var_name} is required."
    missing=1
    continue
  fi

  if [[ "${value}" == CHANGE_ME* ]]; then
    echo "ERROR: ${var_name} still contains a CHANGE_ME placeholder."
    missing=1
  fi
done

if [[ "${missing}" -ne 0 ]]; then
  echo
  echo "Create a sparse .env, for example:"
  cat <<'EOF'
N8N_DB_PASSWORD=<strong-password>
N8N_ENCRYPTION_KEY=<openssl-rand-hex-32>
RABBITMQ_DEFAULT_PASS=<strong-password>
EOF
  echo
  echo "Non-secret defaults do not need to be copied from .env.example."
  exit 2
fi

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
echo "  ./scripts/check.sh"
