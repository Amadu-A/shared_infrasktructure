#!/usr/bin/env bash
# scripts/check.sh
# Проверяет настройки Compose, health и доступность моделей; ничего не меняет.
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/config-check.py check "$@"
