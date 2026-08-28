# shared-infrastructure

Общий Docker Compose stack и набор инженерных инструкций для нескольких
приложений на одном сервере.

Repository решает две отдельные задачи:

1. управляет lifecycle общей infrastructure;
2. хранит переносимый `docs/` instruction bundle для разработчиков и
   LLM / AI coding agents.

---

## Что разворачивается

```text
shared-infrastructure
├── Ollama
├── n8n
│   └── n8n-db (private PostgreSQL только для n8n)
└── RabbitMQ
```

Shared network:

```text
ai-shared
```

Application PostgreSQL, Qdrant, Redis, Celery, backend и frontend сюда
по умолчанию не входят.

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
│   ├── LLM_CONTEXT.md
│   ├── ENGINEERING_GUIDELINES.md
│   ├── INFRASTRUCTURE_INSTRUCTIONS.md
│   └── services.yaml
└── scripts/
    ├── bootstrap.sh
    ├── check.sh
    └── pull-ollama-model.sh
```

---

## Portable `docs/` bundle

Папка:

```text
docs/
```

специально предназначена для передачи LLM при начале нового проекта.

Программист может показать LLM только эти четыре файла:

```text
LLM_CONTEXT.md
ENGINEERING_GUIDELINES.md
INFRASTRUCTURE_INSTRUCTIONS.md
services.yaml
```

Главная точка входа:

```text
docs/LLM_CONTEXT.md
```

LLM не обязана читать `README.md`, `PROJECT_INTEGRATION.md` или scripts этого
repository для проектирования нового application project.

---

## 1. Требования

На host:

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

```bash
mkdir -p ~/projects
cd ~/projects
git clone <repository-url> shared-infrastructure
cd shared-infrastructure
```

---

## 3. Configuration model

### `.env.example`

`.env.example` — committed полный каталог configuration variables.

Он содержит:

- non-secret baseline values;
- pinned image versions;
- safe defaults;
- placeholders для обязательных secrets.

Он не содержит настоящих production secrets.

### `.env`

`.env` — private sparse override.

Для текущего shared stack обычно достаточно:

```dotenv
N8N_DB_PASSWORD=<strong-password>
N8N_ENCRYPTION_KEY=<generated-key>
RABBITMQ_DEFAULT_PASS=<strong-password>
```

Non-secret values **не нужно копировать** из `.env.example`.

Это позволяет добавлять новые non-secret settings без ручного обновления
каждого существующего `.env`.

### Генерация значений

n8n encryption key:

```bash
openssl rand -hex 32
```

password:

```bash
openssl rand -base64 32
```

---

## 4. Docker Compose и `.env.example`

Docker Compose не использует `.env.example` как автоматический runtime source.

Поэтому `compose.yaml` содержит безопасные defaults:

```text
${VAR:-default}
```

и required secrets:

```text
${SECRET:?message}
```

`.env.example` остаётся полным configuration catalog.

`.env` остаётся sparse private override.

---

## 5. Host binding

Безопасный default:

```text
127.0.0.1
```

Для текущего stack используются отдельные variables:

```text
SHARED_BIND_IP
N8N_BIND_IP
RABBITMQ_BIND_IP
```

Если конкретный service должен быть доступен из LAN, переопределить только
нужный variable в `.env`.

Например:

```dotenv
SHARED_BIND_IP=192.168.10.150
```

`0.0.0.0` публикует service на всех host interfaces и должен использоваться
только осознанно.

---

## 6. Первый запуск

Сделать scripts исполняемыми:

```bash
chmod +x scripts/*.sh
```

Bootstrap:

```bash
./scripts/bootstrap.sh
```

Он:

1. проверяет Docker;
2. загружает sparse `.env`, если он существует;
3. проверяет обязательные secrets;
4. создаёт external network `ai-shared`, если нужно;
5. валидирует Compose.

После этого:

```bash
docker compose pull
docker compose up -d
docker compose ps
./scripts/check.sh
```

---

## 7. Shared network

Application container, которому нужен shared service:

```yaml
services:
  api:
    networks:
      - default
      - ai-shared

networks:
  ai-shared:
    external: true
    name: ai-shared
```

Project PostgreSQL обычно остаётся только в private project network.

---

## 8. Ollama

Container-to-container URL:

```text
http://ollama:11434
```

Application configuration:

```dotenv
OLLAMA_BASE_URL=http://ollama:11434
```

Проверка host:

```bash
curl -fsS http://127.0.0.1:${OLLAMA_HOST_PORT:-11434}/api/tags
```

Проверка models:

```bash
docker compose exec ollama ollama list
```

---

## 9. n8n

Internal URL:

```text
http://n8n:5678
```

Workflow names SHOULD иметь project namespace:

```text
[PDRD] Analysis Main
[CONTRACT] Analyze Contract
```

n8n database является private infrastructure dependency n8n и не должна
использоваться application projects.

---

## 10. RabbitMQ

Internal endpoint:

```text
rabbitmq:5672
```

Application projects SHOULD использовать:

```text
one vhost per project
one user per project
```

Bootstrap admin credentials не предназначены для application runtime.

---

## 11. Проверка shared stack

Из repository:

```bash
docker compose ps
./scripts/check.sh
```

Все containers host:

```bash
docker ps \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

Networks:

```bash
docker network ls
docker network inspect ai-shared
```

Ports:

```bash
sudo ss -lntp
```

GPU:

```bash
nvidia-smi
```

---

## 12. Из какой директории выполнять Compose commands

Shared stack:

```bash
cd ~/projects/shared-infrastructure
docker compose ps
```

Другой project:

```bash
cd ~/projects/<project-name>
docker compose ps
```

`docker compose ps` относится к Compose project текущей директории, если
Compose files/project directory явно не указаны.

Shared stack из любой директории:

```bash
docker compose \
  -f ~/projects/shared-infrastructure/compose.yaml \
  --project-directory ~/projects/shared-infrastructure \
  ps
```

---

## 13. Остановка и запуск

Остановить containers:

```bash
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

External `ai-shared` этим Compose не удаляется.

### Критическое предупреждение

Не использовать без осознанной необходимости:

```bash
docker compose down -v
```

`-v` может удалить persistent data:

- Ollama models;
- n8n data;
- n8n database;
- RabbitMQ data.

---

## 14. Обновление

Images pinned.

Обновление выполнять осознанно:

```bash
git pull
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
./scripts/check.sh
```

Не менять production-like image tags на `latest` без причины.

---

## 15. Reboot

Long-running services используют:

```yaml
restart: unless-stopped
```

Docker:

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

## 16. Диагностика

Ollama:

```bash
docker compose logs --tail=100 ollama
```

n8n:

```bash
docker compose logs --tail=100 n8n
```

RabbitMQ:

```bash
docker compose logs --tail=100 rabbitmq
```

---

## 17. Sources of truth

### Для runtime/operator работы shared repository

```text
compose.yaml
README.md
SERVICES.md
docs/INFRASTRUCTURE_INSTRUCTIONS.md
docs/services.yaml
scripts/
```

### Для проектирования нового application с LLM

```text
docs/LLM_CONTEXT.md
docs/ENGINEERING_GUIDELINES.md
docs/INFRASTRUCTURE_INSTRUCTIONS.md
docs/services.yaml
```

`PROJECT_INTEGRATION.md` является только коротким pointer на этот bundle и
не должен дублировать его правила.

`docs/services.yaml` описывает intended topology и НЕ заменяет runtime
verification.

---

## 18. Repository-wide consistency rule

Если меняется обязательное engineering/infrastructure правило, необходимо
проверить связанные источники, которые описывают тот же workflow.

Минимально проверить:

```text
docs/LLM_CONTEXT.md
docs/ENGINEERING_GUIDELINES.md
docs/INFRASTRUCTURE_INSTRUCTIONS.md
docs/services.yaml
README.md
compose.yaml
.env.example
scripts/
```

Изменять файл нужно только если новое правило действительно влияет на него.

Цель — отсутствие нескольких противоречащих друг другу copies одного правила.

---

## 19. Архитектурный принцип

```text
                    HOST
                     │
          ┌──────────┴──────────┐
          │                     │
  shared-infrastructure      applications
          │                     │
     ┌────┼─────┐        ┌──────┼────────┐
   Ollama n8n RabbitMQ   API frontend workers
     │     │      │        │
     └─────┴──────┴── ai-shared
                           │
                    private project net
                           │
                 PostgreSQL/Qdrant/Redis
```

```text
Share infrastructure.
Isolate application state.
Use Docker DNS.
Keep .env sparse.
Do not expose ports unnecessarily.
Verify runtime instead of assuming it.
```
