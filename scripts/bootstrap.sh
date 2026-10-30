#!/usr/bin/env bash
# scripts/bootstrap.sh
# Безопасный запуск основного stack и Docling или миграция старых AI helpers.
# Учётные записи создают только штатные образы при первом запуске пустых volumes.
set -euo pipefail
cd "$(dirname "$0")/.."

check_only=false
update_ai_helpers=false
compose_args=()
for argument in "$@"; do
  case "$argument" in
    --check-only) check_only=true ;;
    --update-ai-helpers) update_ai_helpers=true ;;
    *) compose_args+=("$argument") ;;
  esac
done

python3 scripts/config-check.py validate --show-settings "${compose_args[@]}"
if ! "$update_ai_helpers" || "$check_only"; then
  python3 scripts/config-check.py docling-validate --show-settings "${compose_args[@]}"
fi

# Bootstrap не меняет настройки ОС. Исправление выполняет администратор отдельно.
if ! command -v systemctl >/dev/null 2>&1; then
  echo '[ERROR] systemctl не найден. Проверьте запуск и автозапуск Docker средствами вашей ОС.' >&2
  exit 1
fi
if ! systemctl is-enabled --quiet docker.service; then
  echo '[ERROR] Docker не включён в автозапуск. Администратор может выполнить: sudo systemctl enable docker.service' >&2
  exit 1
fi
if ! systemctl is-active --quiet docker.service; then
  echo '[ERROR] Docker не запущен. Администратор может выполнить: sudo systemctl start docker.service' >&2
  exit 1
fi

if "$check_only"; then
  python3 scripts/config-check.py prepare --check-only "${compose_args[@]}"
  exec python3 scripts/config-check.py docling-prepare --check-only "${compose_args[@]}"
fi

if "$update_ai_helpers"; then
  python3 scripts/config-check.py prepare --check-only "${compose_args[@]}"
  migration_output="$(python3 scripts/config-check.py migration-plan "${compose_args[@]}")"
  read -r -a migration_services <<<"$migration_output"
  if [[ "${#migration_services[@]}" -eq 0 ]]; then
    echo 'Старые AI helper-mounts не обнаружены; контейнеры не изменены.'
  else
    for service in "${migration_services[@]}"; do
      echo "Обновление только ${service} без зависимостей и force-recreate..."
      docker compose "${compose_args[@]}" up -d --no-deps --wait --wait-timeout 1800 "$service"
    done
  fi
  exec python3 scripts/config-check.py check "${compose_args[@]}"
fi

python3 scripts/config-check.py prepare "${compose_args[@]}"
python3 scripts/config-check.py docling-prepare "${compose_args[@]}"
# Обычный bootstrap создаёт отсутствующие сервисы, но не пересоздаёт существующие.
docker compose "${compose_args[@]}" up -d --no-recreate --wait --wait-timeout 1800
python3 scripts/config-check.py docling-start "${compose_args[@]}"
python3 scripts/config-check.py check "${compose_args[@]}"
