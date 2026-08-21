# LLM Infrastructure Context

Этот файл предназначен для LLM / AI coding agents.

## Обязательный порядок

Перед созданием или изменением `compose.yaml` приложения:

1. Прочитать `docs/INFRASTRUCTURE_INSTRUCTIONS.md`.
2. Прочитать `services.yaml`.
3. Определить, какие зависимости уже являются shared.
4. Не дублировать сервисы с `managed_here: true`.
5. Проверить фактическое состояние сервера.

## Runtime verification

Если есть shell-доступ:

```bash
docker ps \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'

docker network ls

sudo ss -lntp

nvidia-smi
```

Shared stack:

```bash
cd ~/projects/shared-infrastructure
docker compose ps
```

Если shell-доступа нет, LLM MUST запросить этот вывод у пользователя.

## Shared DNS endpoints

После подтверждения работы stack и подключения application container к
`ai-shared` использовать:

```text
Ollama:   http://ollama:11434
n8n:      http://n8n:5678
RabbitMQ: rabbitmq:5672
```

Не использовать host ports для container-to-container communication.

## Project-specific by default

Не выносить автоматически в shared:

```text
application PostgreSQL
application Qdrant
Celery worker
Celery beat
frontend
backend
```

## Critical rule

`services.yaml` — декларация intended topology, а не доказательство runtime state.

Нельзя утверждать, что сервис запущен, пока это не подтверждено
`docker compose ps`, `docker ps` или health check.

## Output requirements for infrastructure changes

При предложении изменений LLM должен явно указать:

- какие services остаются shared;
- какие services принадлежат project;
- какие networks используются;
- какие host ports публикуются и зачем;
- какие volumes создаются;
- какие environment variables нужны;
- как проверить конфигурацию;
- как проверить runtime после запуска.
