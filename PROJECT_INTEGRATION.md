# Project Integration

Как подключить новый проект к shared infrastructure.

## 0. # LLM Infrastructure Context

Перед проектированием или изменением инфраструктуры:
0. Перед разработкой прочитай LLM_CONTEXT.md и следуй указанным там инфраструктурным инструкциям
1. Прочитай `docs/INFRASTRUCTURE_INSTRUCTIONS.md`.
2. Прочитай `docs/services.yaml`.
3. Не считай `docs/services.yaml` доказательством того, что контейнер сейчас запущен.
4. Перед изменениями проверь runtime:
   - docker ps
   - docker network ls
   - ss -lntp
   - nvidia-smi
5. Не создавай duplicate shared services.

## 1. Сначала прочитать

```text
../services.yaml
INFRASTRUCTURE_INSTRUCTIONS.md
LLM_CONTEXT.md
```

## 2. Проверить shared stack

На сервере:

```bash
cd ~/projects/shared-infrastructure
docker compose ps
./scripts/check.sh
```

Не считать сервис доступным только потому, что он описан в `docs/services.yaml`.

## 3. Подключить только нужный application service к `ai-shared`

Пример:

```yaml
services:
  api:
    build: docs
    environment:
      OLLAMA_BASE_URL: http://ollama:11434
      N8N_BASE_URL: http://n8n:5678
    networks:
      - default
      - ai-shared

  postgres:
    image: postgres:16-alpine
    networks:
      - default

networks:
  ai-shared:
    external: true
    name: ai-shared
```

Здесь:

- `api` видит shared services;
- project PostgreSQL НЕ подключён к общей сети;
- PostgreSQL остаётся приватным для своего проекта.

## 4. Ollama

Environment:

```dotenv
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=qwen3-vl:8b
```

Проверка из application container:

```bash
curl -fsS http://ollama:11434/api/tags
```

Если модель отсутствует, она устанавливается в shared stack:

```bash
cd ~/projects/shared-infrastructure
docker compose exec ollama ollama pull qwen3-vl:8b
```

## 5. n8n

Внутренний URL:

```dotenv
N8N_BASE_URL=http://n8n:5678
```

Workflow naming:

```text
[PROJECT-NAME] Workflow Name
```

Например:

```text
[PDRD] Analysis Main
```

## 6. RabbitMQ

Shared broker:

```text
rabbitmq:5672
```

Но каждому проекту создать собственный vhost/user.

Пример application `.env`:

```dotenv
RABBITMQ_URL=amqp://pdrd_user:SECRET@rabbitmq:5672/pdrd
```

Credentials MUST NOT попадать в Git.

## 7. Host ports проекта

Не публиковать внутренние базы и brokers только ради связи контейнеров.

Плохо:

```yaml
postgres:
  ports:
    - "5432:5432"
```

если к PostgreSQL обращается только backend.

Хорошо:

```yaml
postgres:
  image: postgres:16-alpine
```

Backend использует:

```text
postgres:5432
```

через private Docker network.

## 8. Проверка после интеграции

```bash
docker compose config --quiet &&
echo "COMPOSE OK"
```

```bash
docker compose up -d
docker compose ps
```

Потом проверить connectivity из application container.

## 9. Запрещённый вариант

Не добавлять в application Compose новый shared Ollama:

```yaml
services:
  ollama:
    image: ollama/ollama
```

если shared Ollama уже существует.

То же правило распространяется на другие сервисы с
`managed_here: true` в `services.yaml`.
