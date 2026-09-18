#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "[1/5] Docker"

docker --version
docker compose version

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is not available."
  exit 1
fi

echo
echo "[2/5] Runtime configuration"

# shared-infrastructure intentionally uses a sparse .env:
# real secrets + environment-specific overrides belong there.
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
  SHARED_VLM_API_KEY
  SHARED_EMBEDDING_API_KEY
  OPEN_WEBUI_SECRET_KEY
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
  echo "Create/update sparse .env with real secrets."
  echo
  echo "Example generators:"
  echo "  openssl rand -hex 32"
  echo "  openssl rand -base64 32"
  exit 2
fi

echo
echo "[3/5] NVIDIA GPU"

if nvidia-smi >/dev/null 2>&1; then
  nvidia-smi \
    --query-gpu=index,name,memory.total,memory.used,memory.free \
    --format=csv
else
  echo "ERROR: nvidia-smi is unavailable."
  exit 3
fi

echo
echo "[4/5] Shared network"

NETWORK_NAME="${SHARED_NETWORK_NAME:-ai-shared}"

if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
  echo "Network ${NETWORK_NAME} already exists."
else
  docker network create "${NETWORK_NAME}"
  echo "Created network ${NETWORK_NAME}."
fi

echo
echo "[5/5] Compose validation"

docker compose \
  --profile ai-vlm \
  --profile ai-embedding \
  --profile ai-ui \
  config --quiet

echo "COMPOSE OK"

echo
echo "Published shared API/UI services are intended for trusted LAN/VPN."
echo "Verify host firewall rules before exposing the server."
echo
echo "Bootstrap completed."
echo
echo "Next:"
echo "  docker compose pull"
echo "  docker compose up -d"
echo "  docker compose ps"
echo "  ./scripts/check.sh"