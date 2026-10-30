#!/usr/bin/env bash
# scripts/check.sh
# Проверяет оба Compose projects, контейнеры и API; --with-tests добавляет tests.
set -euo pipefail
cd "$(dirname "$0")/.."

with_tests=false
compose_args=()
for argument in "$@"; do
  if [[ "$argument" == --with-tests ]]; then
    with_tests=true
  else
    compose_args+=("$argument")
  fi
done

python3 scripts/config-check.py check "${compose_args[@]}"
python3 scripts/config-check.py docling-check "${compose_args[@]}"
if "$with_tests"; then
  python3 -m ruff check scripts tests
  python3 -m ruff format --check scripts tests
  python3 -B -m pytest -p no:cacheprovider -q
fi
