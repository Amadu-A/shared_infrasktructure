# Shared Services Registry

Этот файл — человекочитаемый реестр общей инфраструктуры.

> Важно: реестр описывает **предполагаемую топологию**. Фактическое состояние
> всегда подтверждается `docker compose ps`, `docker ps` и health-check командами.

## Сервисы

| Сервис | Назначение | Docker DNS | Внутренний endpoint | Host port по умолчанию | Доступ проектам |
|---|---|---|---|---:|---|
| Ollama | Общий LLM/VLM runtime на NVIDIA GPU | `ollama` | `http://ollama:11434` | `11434` | Да, через `ai-shared` |
| n8n | Общая orchestration-платформа | `n8n` | `http://n8n:5678` | `5678` | Да, через `ai-shared` |
| RabbitMQ | Общий message broker | `rabbitmq` | `rabbitmq:5672` | `5672` | Да, через `ai-shared` |
| RabbitMQ Management | Администрирование RabbitMQ | — | — | `15672` | Только администраторам |
| n8n-db | PostgreSQL только для n8n | `n8n-db` | `n8n-db:5432` | не публикуется | Нет |

## Не управляются этим репозиторием по умолчанию

Эти сервисы обычно принадлежат конкретному приложению:

- PostgreSQL приложения;
- Qdrant приложения;
- Redis приложения;
- Celery worker / Celery beat;
- backend;
- frontend.

## Shared network

```text
ai-shared
```

Приложение подключается к этой сети только тем контейнером, которому
действительно нужны shared-сервисы.

Например backend может быть подключён одновременно к:

```text
project-default
ai-shared
```

а PostgreSQL проекта должен оставаться только в private network проекта.

## Проверка

Из директории репозитория:

```bash
docker compose ps
```

Все контейнеры сервера:

```bash
docker ps \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

Ollama:

```bash
curl -fsS http://127.0.0.1:${OLLAMA_HOST_PORT:-11434}/api/tags
```

n8n:

```bash
curl -fsS http://127.0.0.1:${N8N_HOST_PORT:-5678}/healthz
```

RabbitMQ:

```bash
docker compose exec rabbitmq rabbitmq-diagnostics -q ping
```

GPU:

```bash
nvidia-smi
```

## Ownership

Shared-сервис не должен принадлежать одному бизнес-проекту.

Запрещённая зависимость:

```text
PDRD compose
└── Ollama

Contract AI
└── использует Ollama, принадлежащий PDRD
```

Правильная схема:

```text
shared-infrastructure
├── Ollama
├── n8n
└── RabbitMQ

PDRD
Contract AI
Other projects
```
