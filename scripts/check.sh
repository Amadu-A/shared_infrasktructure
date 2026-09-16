#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

OLLAMA_PORT="${OLLAMA_HOST_PORT:-11434}"
N8N_PORT="${N8N_HOST_PORT:-5678}"
NETWORK_NAME="${SHARED_NETWORK_NAME:-ai-shared}"

fail=0

ok() {
  printf '[OK]   %s\n' "$1"
}

bad() {
  printf '[FAIL] %s\n' "$1"
  fail=1
}

echo "=== Docker ==="
if docker info >/dev/null 2>&1; then
  ok "Docker daemon"
else
  bad "Docker daemon"
fi

if docker compose config --quiet >/dev/null 2>&1; then
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
docker compose ps || fail=1

echo
echo "=== Ollama ==="
if curl -fsS "http://127.0.0.1:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; then
  ok "Ollama HTTP"
else
  bad "Ollama HTTP"
fi

if nvidia-smi >/dev/null 2>&1; then
  ok "NVIDIA GPU"
else
  bad "NVIDIA GPU"
fi

echo
echo "=== n8n ==="
if docker compose exec -T n8n \
  node -e \
  "fetch('http://127.0.0.1:5678/healthz')
    .then(r => process.exit(r.ok ? 0 : 1))
    .catch(() => process.exit(1))" \
  >/dev/null 2>&1; then
  ok "n8n healthz"
else
  bad "n8n healthz"
fi

echo
echo "=== RabbitMQ ==="
if docker compose exec -T rabbitmq rabbitmq-diagnostics -q ping >/dev/null 2>&1; then
  ok "RabbitMQ"
else
  bad "RabbitMQ"
fi

echo
if [[ "${fail}" -eq 0 ]]; then
  echo "ALL CHECKS PASSED"
else
  echo "ONE OR MORE CHECKS FAILED"
fi

exit "${fail}"
