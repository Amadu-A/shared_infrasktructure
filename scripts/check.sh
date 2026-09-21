#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

PROJECT_NAME="${COMPOSE_PROJECT_NAME:-shared}"
NETWORK_NAME="${SHARED_NETWORK_NAME:-ai-shared}"
PUBLIC_HOST="${SHARED_PUBLIC_HOST:-<not-configured>}"

SHARED_VLM_BIND_IP="${SHARED_VLM_BIND_IP:-0.0.0.0}"
SHARED_VLM_HOST_PORT="${SHARED_VLM_HOST_PORT:-8000}"

SHARED_EMBEDDING_BIND_IP="${SHARED_EMBEDDING_BIND_IP:-0.0.0.0}"
SHARED_EMBEDDING_HOST_PORT="${SHARED_EMBEDDING_HOST_PORT:-8001}"

OPEN_WEBUI_BIND_IP="${OPEN_WEBUI_BIND_IP:-0.0.0.0}"
OPEN_WEBUI_HOST_PORT="${OPEN_WEBUI_HOST_PORT:-3000}"

N8N_BIND_IP="${N8N_BIND_IP:-0.0.0.0}"
N8N_HOST_PORT="${N8N_HOST_PORT:-5678}"

RABBITMQ_BIND_IP="${RABBITMQ_BIND_IP:-0.0.0.0}"
RABBITMQ_AMQP_HOST_PORT="${RABBITMQ_AMQP_HOST_PORT:-5672}"
RABBITMQ_MANAGEMENT_HOST_PORT="${RABBITMQ_MANAGEMENT_HOST_PORT:-15672}"

fail=0

ok() {
  printf '[OK]   %s\n' "$1"
}

bad() {
  printf '[FAIL] %s\n' "$1"
  fail=1
}

skip() {
  printf '[SKIP] %s\n' "$1"
}

check_ip() {
  case "$1" in
    0.0.0.0|"::"|"[::]")
      printf '127.0.0.1'
      ;;
    *)
      printf '%s' "$1"
      ;;
  esac
}

running_services="$(
  docker ps \
    --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
    --format '{{.Label "com.docker.compose.service"}}' \
    2>/dev/null || true
)"

service_running() {
  grep -Fxq "$1" <<<"${running_services}"
}

echo "=== Docker ==="

if docker info >/dev/null 2>&1; then
  ok "Docker daemon"
else
  bad "Docker daemon"
fi

if docker compose \
  --profile ai-vlm \
  --profile ai-embedding \
  --profile ai-ui \
  config --quiet >/dev/null 2>&1; then
  ok "Compose config"
else
  bad "Compose config"
fi

if docker network inspect "${NETWORK_NAME}" >/dev/null 2>&1; then
  ok "Network ${NETWORK_NAME}"
else
  bad "Network ${NETWORK_NAME}"
fi

echo
echo "=== Containers ==="

docker compose \
  --profile ai-vlm \
  --profile ai-embedding \
  --profile ai-ui \
  ps || fail=1

echo
echo "=== NVIDIA ==="

if nvidia-smi >/dev/null 2>&1; then
  ok "NVIDIA GPU"
else
  bad "NVIDIA GPU"
fi

echo
echo "=== shared-vlm ==="

if service_running shared-vlm; then
  vlm_check_ip="$(check_ip "${SHARED_VLM_BIND_IP}")"

  if curl -fsS \
    "http://${vlm_check_ip}:${SHARED_VLM_HOST_PORT}/health" \
    >/dev/null 2>&1; then
    ok "shared-vlm health"
  else
    bad "shared-vlm health"
  fi

  if curl -fsS \
    "http://${vlm_check_ip}:${SHARED_VLM_HOST_PORT}/v1/models" \
    >/dev/null 2>&1; then
    ok "shared-vlm API"
  else
    bad "shared-vlm API"
  fi

  if curl -fsS \
    "http://${vlm_check_ip}:${SHARED_VLM_HOST_PORT}/metrics" \
    >/dev/null 2>&1; then
    ok "shared-vlm metrics"
  else
    bad "shared-vlm metrics"
  fi
else
  skip "shared-vlm is not running"
fi

echo
echo "=== shared-embedding ==="

if service_running shared-embedding; then
  embedding_check_ip="$(check_ip "${SHARED_EMBEDDING_BIND_IP}")"

  if curl -fsS \
    "http://${embedding_check_ip}:${SHARED_EMBEDDING_HOST_PORT}/health" \
    >/dev/null 2>&1; then
    ok "shared-embedding health"
  else
    bad "shared-embedding health"
  fi

  if curl -fsS \
    "http://${embedding_check_ip}:${SHARED_EMBEDDING_HOST_PORT}/v1/models" \
    >/dev/null 2>&1; then
    ok "shared-embedding API"
  else
    bad "shared-embedding API"
  fi

  if curl -fsS \
    "http://${embedding_check_ip}:${SHARED_EMBEDDING_HOST_PORT}/metrics" \
    >/dev/null 2>&1; then
    ok "shared-embedding metrics"
  else
    bad "shared-embedding metrics"
  fi
else
  skip "shared-embedding is not running"
fi

echo
echo "=== Open WebUI ==="

if service_running open-webui; then
  webui_check_ip="$(check_ip "${OPEN_WEBUI_BIND_IP}")"

  if curl -fsS \
    "http://${webui_check_ip}:${OPEN_WEBUI_HOST_PORT}/health" \
    >/dev/null 2>&1; then
    ok "Open WebUI health"
  else
    bad "Open WebUI health"
  fi
else
  skip "Open WebUI is not running"
fi

echo
echo "=== n8n ==="

if service_running n8n; then
  n8n_check_ip="$(check_ip "${N8N_BIND_IP}")"

  if curl -fsS \
    "http://${n8n_check_ip}:${N8N_HOST_PORT}/healthz" \
    >/dev/null 2>&1; then
    ok "n8n healthz"
  else
    bad "n8n healthz"
  fi
else
  bad "n8n container is not running"
fi

echo
echo "=== n8n-db ==="

if service_running n8n-db; then
  if docker compose exec -T n8n-db \
    pg_isready \
      -U "${N8N_DB_USER:-n8n}" \
      -d "${N8N_DB_NAME:-n8n}" \
    >/dev/null 2>&1; then
    ok "n8n-db"
  else
    bad "n8n-db"
  fi
else
  bad "n8n-db container is not running"
fi

echo
echo "=== RabbitMQ ==="

if service_running rabbitmq; then
  if docker compose exec -T rabbitmq \
    rabbitmq-diagnostics -q ping \
    >/dev/null 2>&1; then
    ok "RabbitMQ broker"
  else
    bad "RabbitMQ broker"
  fi

  rabbitmq_check_ip="$(check_ip "${RABBITMQ_BIND_IP}")"

  if curl -fsS \
    -u "${RABBITMQ_DEFAULT_USER:-shared_admin}:${RABBITMQ_DEFAULT_PASS:-}" \
    "http://${rabbitmq_check_ip}:${RABBITMQ_MANAGEMENT_HOST_PORT}/api/overview" \
    >/dev/null 2>&1; then
    ok "RabbitMQ Management HTTP"
  else
    bad "RabbitMQ Management HTTP"
  fi
else
  bad "RabbitMQ container is not running"
fi

echo
echo "=== Published endpoints ==="

echo "shared-vlm:        http://${PUBLIC_HOST}:${SHARED_VLM_HOST_PORT}/v1"
echo "shared-embedding:  http://${PUBLIC_HOST}:${SHARED_EMBEDDING_HOST_PORT}"
echo "Open WebUI:        http://${PUBLIC_HOST}:${OPEN_WEBUI_HOST_PORT}"
echo "n8n:               http://${PUBLIC_HOST}:${N8N_HOST_PORT}"
echo "RabbitMQ AMQP:     ${PUBLIC_HOST}:${RABBITMQ_AMQP_HOST_PORT}"
echo "RabbitMQ UI:       http://${PUBLIC_HOST}:${RABBITMQ_MANAGEMENT_HOST_PORT}"

echo

if [[ "${fail}" -eq 0 ]]; then
  echo "ALL CHECKS PASSED"
else
  echo "ONE OR MORE CHECKS FAILED"
fi

exit "${fail}"