#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

# Load local repository settings when present.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

MODELS_FILE="${OLLAMA_MODELS_FILE:-${ROOT_DIR}/models/ollama-models.txt}"
MIN_OLLAMA_VERSION="${OLLAMA_MIN_VERSION:-0.32.12}"
FORCE=0

usage() {
  cat <<'EOF'
Usage:
  ./scripts/pull-ollama-model.sh
      Pull all models from models/ollama-models.txt.
      Models already installed are skipped.

  ./scripts/pull-ollama-model.sh --force
      Pull/update all configured models even if already installed.

  ./scripts/pull-ollama-model.sh --list
      Print configured models without downloading them.

  ./scripts/pull-ollama-model.sh MODEL [MODEL ...]
      Pull only the specified model(s).

Examples:
  ./scripts/pull-ollama-model.sh
  ./scripts/pull-ollama-model.sh --force
  ./scripts/pull-ollama-model.sh qwen3-vl:8b-instruct
  ./scripts/pull-ollama-model.sh qwen3.8:27b
EOF
}

version_ge() {
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

configured_models() {
  sed -e 's/[[:space:]]*#.*$//' \
      -e '/^[[:space:]]*$/d' \
      -e 's/^[[:space:]]*//' \
      -e 's/[[:space:]]*$//' \
      "${MODELS_FILE}"
}

if [[ ! -f "${MODELS_FILE}" ]]; then
  echo "ERROR: model registry not found: ${MODELS_FILE}" >&2
  exit 2
fi

case "${1:-}" in
  --help|-h)
    usage
    exit 0
    ;;
  --list)
    configured_models
    exit 0
    ;;
  --force)
    FORCE=1
    shift
    ;;
esac

if ! docker info >/dev/null 2>&1; then
  echo "ERROR: Docker daemon is unavailable." >&2
  exit 3
fi

if ! docker compose ps --status running --services | grep -qx 'ollama'; then
  echo "ERROR: shared Ollama container is not running." >&2
  echo "Run: docker compose up -d ollama" >&2
  exit 4
fi

OLLAMA_VERSION_RAW="$(docker compose exec -T ollama ollama --version 2>/dev/null || true)"
OLLAMA_VERSION="$(printf '%s\n' "${OLLAMA_VERSION_RAW}" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"

if [[ -z "${OLLAMA_VERSION}" ]]; then
  echo "ERROR: could not determine Ollama version." >&2
  echo "Output: ${OLLAMA_VERSION_RAW}" >&2
  exit 5
fi

echo "Ollama version: ${OLLAMA_VERSION}"

if ! version_ge "${OLLAMA_VERSION}" "${MIN_OLLAMA_VERSION}"; then
  echo "ERROR: Ollama ${OLLAMA_VERSION} is too old for the configured model catalog." >&2
  echo "Required minimum: ${MIN_OLLAMA_VERSION}" >&2
  echo "Upgrade the shared Ollama image first." >&2
  exit 6
fi

if [[ "$#" -gt 0 ]]; then
  MODELS=("$@")
else
  mapfile -t MODELS < <(configured_models)
fi

if [[ "${#MODELS[@]}" -eq 0 ]]; then
  echo "ERROR: no models selected." >&2
  exit 7
fi

echo
echo "Models selected:"
printf '  - %s\n' "${MODELS[@]}"
echo

installed_models() {
  docker compose exec -T ollama ollama list 2>/dev/null | awk 'NR > 1 {print $1}'
}

for model in "${MODELS[@]}"; do
  if [[ "${FORCE}" -eq 0 ]] && installed_models | grep -Fxq "${model}"; then
    echo "[SKIP] ${model} is already installed."
    continue
  fi

  echo "[PULL] ${model}"
  docker compose exec -T ollama ollama pull "${model}"
  echo "[OK]   ${model}"
  echo
done

echo "Installed Ollama models:"
docker compose exec -T ollama ollama list
