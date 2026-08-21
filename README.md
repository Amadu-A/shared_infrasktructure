# shared-infrastructure

Общий инфраструктурный Docker Compose stack для нескольких приложений на одном сервере.

Репозиторий отделяет lifecycle общей инфраструктуры от lifecycle бизнес-проектов.
Остановка, rebuild или удаление одного приложения не должны останавливать Ollama,
n8n или RabbitMQ, которыми пользуются другие приложения.

## Что разворачивается

```text
shared-infrastructure
├── Ollama
├── n8n
│   └── n8n-db (private PostgreSQL только для n8n)
└── RabbitMQ
```

Общая сеть:

```text
ai-shared
```

Проектные PostgreSQL, Qdrant, Redis, Celery, backend и frontend сюда по умолчанию
не входят.

---

## Структура

```text
shared-infrastructure/
├── compose.yaml
├── .env.example
├── .env                         # local only, MUST NOT be committed
├── .gitignore
├── README.md
├── SERVICES.md
├── PROJECT_INTEGRATION.md
├── docs/
│   ├── INFRASTRUCTURE_INSTRUCTIONS.md
│   ├── LLM_CONTEXT.md
│   └── services.yaml
└── scripts/
    ├── bootstrap.sh
    └── check.sh
```

---

## 1. Требования

На host должны быть установлены:

```bash
docker --version
docker compose version
```

Для Ollama на NVIDIA GPU:

```bash
nvidia-smi
```

и NVIDIA Container Toolkit.

Проверка GPU внутри Docker:

```bash
docker run --rm \
  --gpus all \
  nvidia/cuda:12.8.0-base-ubuntu24.04 \
  nvidia-smi
```

---

## 2. Клонирование

Для приватного GitHub-репозитория рекомендуется SSH:

```bash
mkdir -p ~/projects
cd ~/projects
git clone git@github.com:neo-term-it/shared-infrastructure.git
cd shared-infrastructure
```

HTTPS также допустим, если настроена авторизация GitHub:

```bash
git clone https://github.com/neo-term-it/shared-infrastructure.git
```

---

## 3. `.env`

`.env` содержит локальные secrets и MUST NOT попадать в Git.

На новом сервере:

```bash
cp .env.example .env
```

Сгенерировать ключ n8n:

```bash
openssl rand -hex 32
```

Сгенерировать пароль:

```bash
openssl rand -base64 32
```

Заменить в `.env`:

```dotenv
N8N_DB_PASSWORD=...
N8N_ENCRYPTION_KEY=...
RABBITMQ_DEFAULT_PASS=...
```

### Binding host ports

Безопасное значение по умолчанию:

```dotenv
SHARED_BIND_IP=127.0.0.1
```

При нём host ports доступны только с сервера.

Если прямой доступ нужен из локальной сети, можно задать конкретный LAN IP:

```dotenv
SHARED_BIND_IP=192.168.10.150
```

или, при осознанной необходимости:

```dotenv
SHARED_BIND_IP=0.0.0.0
```

Последний вариант публикует сервис на всех интерфейсах host и требует
дополнительной сетевой защиты.

---

## 4. Первый запуск

Сделать scripts исполняемыми:

```bash
chmod +x scripts/*.sh
```

Bootstrap:

```bash
./scripts/bootstrap.sh
```

Он:

1. проверит Docker;
2. проверит наличие `.env`;
3. создаст external network `ai-shared`, если её ещё нет;
4. провалидирует Compose.

После этого:

```bash
docker compose pull
docker compose up -d
docker compose ps
```

---

## 5. Проверка

Автоматическая:

```bash
./scripts/check.sh
```

Ручная:

```bash
docker compose ps
```

```bash
curl -fsS http://127.0.0.1:11434/api/tags |
python3 -m json.tool
```

```bash
curl -fsS http://127.0.0.1:5678/healthz
```

```bash
docker compose exec rabbitmq rabbitmq-diagnostics -q ping
```

```bash
nvidia-smi
```

---

## 6. Ollama models

Образы Ollama и модели Ollama — разные сущности.

`docker compose pull` скачивает Docker image Ollama, но не LLM/VLM models.

Посмотреть модели:

```bash
docker compose exec ollama ollama list
```

Скачать модель:

```bash
docker compose exec ollama ollama pull qwen3-vl:8b
```

Например embedding model:

```bash
docker compose exec ollama ollama pull qwen3-embedding:4b
```

Модели сохраняются в persistent volume shared Ollama и скачиваются один раз
для всех приложений.

Каждый бизнес-проект должен документировать, какие Ollama models ему нужны.

---

## 7. Как приложение использует shared Ollama

В Compose приложения:

```yaml
services:
  api:
    networks:
      - default
      - ai-shared
    environment:
      OLLAMA_BASE_URL: http://ollama:11434

networks:
  ai-shared:
    external: true
    name: ai-shared
```

Внутри application container:

```text
http://ollama:11434
```

Не использовать:

```text
http://localhost:11434
```

потому что `localhost` внутри контейнера означает сам этот контейнер.

Полный пример:

```text
docs/PROJECT_INTEGRATION.md
```

---

## 8. Как приложение использует n8n

Приложение, подключённое к `ai-shared`, видит n8n по адресу:

```text
http://n8n:5678
```

Workflow names SHOULD иметь project namespace:

```text
[PDRD] Analysis Main
[CONTRACT] Analyze Contract
```

Это упрощает поддержку одного общего n8n несколькими проектами.

---

## 9. Как приложение использует RabbitMQ

Внутренний endpoint:

```text
rabbitmq:5672
```

Но application projects SHOULD NOT использовать bootstrap admin пользователя.

Для каждого проекта рекомендуется:

```text
отдельный vhost
отдельный user
отдельный password
```

Например:

```text
/pdrd
/contract-ai
```

После создания credentials приложение хранит их только в своём `.env`.

---

## 10. Из какой директории выполнять `docker compose ps`

Shared stack:

```bash
cd ~/projects/shared-infrastructure
docker compose ps
```

PDRD:

```bash
cd ~/projects/PDRD-validation
docker compose ps
```

Другой проект:

```bash
cd ~/projects/contract-analysis-ai
docker compose ps
```

`docker compose ps` без явного `-f` относится к Compose project текущей директории.

Все Docker-контейнеры сервера можно увидеть из любой директории:

```bash
docker ps
```

Посмотреть shared stack из любой директории:

```bash
docker compose \
  -f ~/projects/shared-infrastructure/compose.yaml \
  --project-directory ~/projects/shared-infrastructure \
  ps
```

---

## 11. Остановка и запуск

Остановить shared containers:

```bash
cd ~/projects/shared-infrastructure
docker compose stop
```

Запустить снова:

```bash
docker compose start
```

Удалить containers и private network stack:

```bash
docker compose down
```

External network `ai-shared` не удаляется этим Compose.

### Критическое предупреждение

Не использовать без осознанной необходимости:

```bash
docker compose down -v
```

`-v` удаляет persistent volumes и может уничтожить:

- Ollama models;
- n8n data;
- n8n database;
- RabbitMQ data.

---

## 12. Обновление

Версии images зафиксированы в `.env.example`.

Обновление выполняется осознанно.

```bash
git pull
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
./scripts/check.sh
```

Не менять image tags на `latest` для production-like окружений.

---

## 13. Перезагрузка сервера

Services используют:

```yaml
restart: unless-stopped
```

Docker должен быть включён в автозапуск:

```bash
systemctl is-enabled docker
```

После reboot:

```bash
cd ~/projects/shared-infrastructure
docker compose ps
./scripts/check.sh
```

---

## 14. Диагностика

Все shared services:

```bash
docker compose ps
```

Logs Ollama:

```bash
docker compose logs --tail=100 ollama
```

Logs n8n:

```bash
docker compose logs --tail=100 n8n
```

Logs RabbitMQ:

```bash
docker compose logs --tail=100 rabbitmq
```

Занятые порты:

```bash
sudo ss -lntp
```

Docker networks:

```bash
docker network ls
docker network inspect ai-shared
```

---

## 15. Источники истины

Для разработчика:

```text
README.md
SERVICES.md
docs/INFRASTRUCTURE_INSTRUCTIONS.md
docs/PROJECT_INTEGRATION.md
```

Для LLM / automation:

```text
services.yaml
docs/LLM_CONTEXT.md
docs/INFRASTRUCTURE_INSTRUCTIONS.md
```

`docs/services.yaml` описывает intended topology.

Он НЕ заменяет runtime verification.

Перед изменением инфраструктуры всё равно проверять:

```bash
docker ps
docker network ls
sudo ss -lntp
nvidia-smi
```

---

## 16. Архитектурный принцип

```text
                    HOST
                     │
          ┌──────────┴──────────┐
          │                     │
  shared-infrastructure      applications
          │                     │
     ┌────┼─────┐        ┌──────┼───────┐
   Ollama n8n RabbitMQ   PDRD Contract Other
     │     │      │        │
     └─────┴──────┴── ai-shared
```

Основные правила:

```text
Share infrastructure.
Isolate application state.
Do not expose ports unnecessarily.
Do not duplicate expensive services.
Discover infrastructure before creating infrastructure.
Keep shared infrastructure lifecycle independent from business projects.
```
