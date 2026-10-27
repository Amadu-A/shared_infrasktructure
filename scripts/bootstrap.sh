#!/usr/bin/env bash
# scripts/bootstrap.sh
# Проверяет итоговый Compose: окружение > .env > defaults; контейнеры не запускает.
# .env как shell-код не исполняется. Bootstrap --check-only не создаёт сеть.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/deployment.py bootstrap "$@"
