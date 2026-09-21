#!/usr/bin/env bash
# scripts/bootstrap.sh
#
# Выполняет preflight shared-infrastructure перед первым запуском или
# изменением deployment.
#
# Скрипт проверяет Docker, обязательную configuration, GPU layout,
# существование shared Docker network и валидность Compose.
#
# Совместное использование одной physical GPU несколькими inference services
# допускается архитектурой. Скрипт предупреждает об overlap, но не блокирует
# deployment: ответственность за VRAM/capacity/stability несёт operator.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "INFO: .env not found; checking exported process environment."
fi

fail=0

error() {
  printf '[ERROR] %s\n' "$1"
  fail=1
}

warning() {
  printf '[WARN]  %s\n' "$1"
}

profile_enabled() {
  local profile="$1"
  local profiles=",${COMPOSE_PROFILES:-},"

  [[ "${profiles}" == *",${profile},"* ]]
}

require_value() {
  local name="$1"
  local value="${!name:-}"

  if [[ -z "${value}" ]]; then
    error "${name} is required."
    return
  fi

  if [[ "${value}" == CHANGE_ME* ]]; then
    error "${name} still contains a CHANGE_ME placeholder."
  fi
}

validate_positive_int() {
  local name="$1"
  local value="$2"

  if [[ ! "${value}" =~ ^[1-9][0-9]*$ ]]; then
    error "${name} must be a positive integer; got '${value}'."
  fi
}

normalize_gpu_list() {
  printf '%s' "$1" | tr -d '[:space:]'
}

validate_gpu_layout() {
  local prefix="$1"

  local devices_name="${prefix}_GPU_DEVICES"
  local tp_name="${prefix}_TP_SIZE"
  local dp_name="${prefix}_DP_SIZE"

  local devices
  devices="$(normalize_gpu_list "${!devices_name:-}")"

  local tp="${!tp_name:-1}"
  local dp="${!dp_name:-1}"

  if [[ -z "${devices}" ]]; then
    error "${devices_name} must not be empty."
    return
  fi

  validate_positive_int "${tp_name}" "${tp}"
  validate_positive_int "${dp_name}" "${dp}"

  if [[ ! "${tp}" =~ ^[1-9][0-9]*$ ]] \
    || [[ ! "${dp}" =~ ^[1-9][0-9]*$ ]]; then
    return
  fi

  IFS=',' read -r -a gpu_ids <<<"${devices}"

  local expected=$((tp * dp))

  if [[ "${#gpu_ids[@]}" -ne "${expected}" ]]; then
    error "${prefix}: ${devices_name} contains ${#gpu_ids[@]} GPU(s), but ${tp_name} * ${dp_name} = ${expected}."
  fi

  declare -A seen_gpu_ids=()

  local id

  for id in "${gpu_ids[@]}"; do
    if [[ ! "${id}" =~ ^[0-9]+$ ]]; then
      error "${devices_name} must contain numeric GPU indexes; got '${id}'."
      continue
    fi

    if [[ -n "${seen_gpu_ids[${id}]:-}" ]]; then
      error "${devices_name} contains duplicate GPU index ${id}."
      continue
    fi

    seen_gpu_ids["${id}"]=1

    if ! nvidia-smi \
      --query-gpu=index \
      --format=csv,noheader,nounits \
      2>/dev/null \
      | grep -Fxq "${id}"; then
      error "GPU index ${id} from ${devices_name} does not exist on this host."
    fi
  done
}

warn_gpu_overlap() {
  local first
  local second

  first="$(normalize_gpu_list "${SHARED_VLM_GPU_DEVICES:-}")"
  second="$(normalize_gpu_list "${SHARED_EMBEDDING_GPU_DEVICES:-}")"

  IFS=',' read -r -a first_ids <<<"${first}"
  IFS=',' read -r -a second_ids <<<"${second}"

  local left
  local right

  for left in "${first_ids[@]}"; do
    for right in "${second_ids[@]}"; do
      if [[ -n "${left}" && "${left}" == "${right}" ]]; then
        warning "GPU ${left} is assigned to both shared-vlm and shared-embedding. This is allowed, but combined VRAM usage, gpu-memory-utilization and stability MUST be verified at runtime."
      fi
    done
  done
}

echo "[1/6] Docker"

docker --version
docker compose version

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is not available."
  exit 1
fi

echo
echo "[2/6] Required configuration"

require_value SHARED_PUBLIC_HOST
require_value N8N_DB_PASSWORD
require_value N8N_ENCRYPTION_KEY
require_value RABBITMQ_DEFAULT_PASS

if [[ "${SHARED_PUBLIC_HOST:-}" == http://* \
   || "${SHARED_PUBLIC_HOST:-}" == https://* \
   || "${SHARED_PUBLIC_HOST:-}" == */* ]]; then
  error "SHARED_PUBLIC_HOST must be only an IP or DNS name, without scheme, path or port."
fi

if profile_enabled ai-vlm; then
  require_value SHARED_VLM_MODEL_ID
  require_value SHARED_VLM_MODEL_REVISION
  require_value SHARED_VLM_GPU_DEVICES
  require_value SHARED_VLM_API_KEY
fi

if profile_enabled ai-embedding; then
  require_value SHARED_EMBEDDING_MODEL_ID
  require_value SHARED_EMBEDDING_MODEL_REVISION
  require_value SHARED_EMBEDDING_GPU_DEVICES
  require_value SHARED_EMBEDDING_API_KEY
fi

if profile_enabled ai-ui; then
  require_value OPEN_WEBUI_SECRET_KEY

  if ! profile_enabled ai-vlm; then
    error "ai-ui requires ai-vlm in COMPOSE_PROFILES."
  fi
fi

if [[ "${fail}" -ne 0 ]]; then
  exit 2
fi

echo
echo "[3/6] NVIDIA GPU"

if profile_enabled ai-vlm || profile_enabled ai-embedding; then
  if ! nvidia-smi >/dev/null 2>&1; then
    echo "ERROR: nvidia-smi is unavailable."
    exit 3
  fi

  nvidia-smi \
    --query-gpu=index,name,memory.total,memory.used,memory.free \
    --format=csv

  if profile_enabled ai-vlm; then
    validate_gpu_layout SHARED_VLM
  fi

  if profile_enabled ai-embedding; then
    validate_gpu_layout SHARED_EMBEDDING
  fi

  if profile_enabled ai-vlm && profile_enabled ai-embedding; then
    warn_gpu_overlap
  fi

  if [[ "${fail}" -ne 0 ]]; then
    exit 3
  fi
else
  echo "INFO: no GPU inference profile is enabled."
fi

echo
echo "[4/6] Shared Docker network"

NETWORK_NAME="${SHARED_NETWORK_NAME:-ai-shared}"

if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
  echo "Network ${NETWORK_NAME} already exists."
else
  docker network create "${NETWORK_NAME}"
  echo "Created network ${NETWORK_NAME}."
fi

echo
echo "[5/6] Compose validation"

docker compose \
  --profile ai-vlm \
  --profile ai-embedding \
  --profile ai-ui \
  config --quiet

echo "COMPOSE OK"

echo
echo "[6/6] Public endpoints"

echo "shared host:       ${SHARED_PUBLIC_HOST}"
echo "shared-vlm:        http://${SHARED_PUBLIC_HOST}:${SHARED_VLM_HOST_PORT:-8000}/v1"
echo "shared-embedding:  http://${SHARED_PUBLIC_HOST}:${SHARED_EMBEDDING_HOST_PORT:-8001}"
echo "Open WebUI:        http://${SHARED_PUBLIC_HOST}:${OPEN_WEBUI_HOST_PORT:-3000}"
echo "n8n:               http://${SHARED_PUBLIC_HOST}:${N8N_HOST_PORT:-5678}"
echo "RabbitMQ AMQP:     ${SHARED_PUBLIC_HOST}:${RABBITMQ_AMQP_HOST_PORT:-5672}"
echo "RabbitMQ UI:       http://${SHARED_PUBLIC_HOST}:${RABBITMQ_MANAGEMENT_HOST_PORT:-15672}"

echo
echo "Bootstrap completed."
echo "Verify host firewall rules before exposing shared ports outside trusted LAN/VPN."

echo
echo "Next:"
echo "  docker compose pull"
echo "  docker compose up -d --remove-orphans"
echo "  docker compose ps"
echo "  ./scripts/check.sh"