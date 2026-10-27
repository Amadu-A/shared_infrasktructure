#!/usr/bin/env bash
# scripts/open-webui-entrypoint.sh
# Проверяет секрет только при старте UI и передаёт управление штатному start.sh.
# Штатная команда проверена по Dockerfile open-webui v0.11.3; ключ не выводится.
set -euo pipefail
secret="${WEBUI_SECRET_KEY:-}"
if [[ -z "${secret//[[:space:]]/}" || "${secret}" =~ ^[[:space:]]*CHANGE_ME ]]; then
  echo '[ERROR] OPEN_WEBUI_SECRET_KEY обязателен для запуска Open WebUI.' >&2
  exit 2
fi
exec bash /app/backend/start.sh "$@"
