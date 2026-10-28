#!/usr/bin/env bash
# scripts/bootstrap.sh
# Первоначальная настройка уже установленного Docker и запуск выбранных сервисов.
# Учётные записи создают штатные образы при первом запуске пустых volumes.
# --check-only ничего не запускает и не меняет настройки системы.
set -euo pipefail
cd "$(dirname "$0")/.."

check_only=false
compose_args=()
for argument in "$@"; do
  if [[ "$argument" == --check-only ]]; then
    check_only=true
  else
    compose_args+=("$argument")
  fi
done

# До любых изменений проверяем тот же итоговый config, который получит Docker.
python3 scripts/config-check.py validate "${compose_args[@]}"
if "$check_only"; then
  exec python3 scripts/config-check.py prepare --check-only "${compose_args[@]}"
fi

# Сам Docker должен запускаться при загрузке ОС; отдельный manager для контейнеров не нужен.
if command -v systemctl >/dev/null 2>&1; then
  if ! systemctl is-enabled --quiet docker.service || ! systemctl is-active --quiet docker.service; then
    if [[ "$EUID" -eq 0 ]]; then
      systemctl enable --now docker.service
    else
      sudo systemctl enable --now docker.service
    fi
  fi
else
  echo 'Проверьте автозапуск установленного Docker средствами вашей ОС.' >&2
  exit 1
fi

python3 scripts/config-check.py prepare "${compose_args[@]}"
# Сохраняются restart policy, volumes и штатная инициализация пользователей.
docker compose "${compose_args[@]}" up -d --wait --wait-timeout 1800
python3 scripts/config-check.py check "${compose_args[@]}"
