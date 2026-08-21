# Infrastructure Development Instructions

> Общий стандарт инфраструктуры, Docker Compose, shared services,
> сетевого взаимодействия и разработки микросервисов.
>
> Документ предназначен для:
>
> - backend-разработчиков;
> - frontend-разработчиков;
> - DevOps-инженеров;
> - AI/ML-инженеров;
> - LLM / AI coding agents.
>
> Любой новый проект на сервере MUST проектироваться с учётом этих правил.

---

## 1. Основной принцип

На одном физическом сервере могут одновременно работать несколько независимых проектов.

Пример:

```text
Server
├── shared-infrastructure
├── project-a
├── project-b
└── project-c
```

Проекты MUST:

1. не конфликтовать по host ports;
2. не конфликтовать по именам контейнеров;
3. не конфликтовать по Docker volumes;
4. не конфликтовать по Docker networks;
5. не создавать дублирующую shared-инфраструктуру без необходимости;
6. иметь изолированные данные;
7. иметь независимый lifecycle приложения;
8. переиспользовать общие инфраструктурные сервисы там, где это предусмотрено архитектурой.

---

## 2. MUST / SHOULD / MAY

В документе используются следующие обозначения.

- **MUST** — обязательное правило.
- **MUST NOT** — запрещённое действие.
- **SHOULD** — рекомендуемое решение; отклонение допустимо только при наличии причины.
- **MAY** — допустимый вариант.

---

## 3. Рекомендуемая структура сервера

```text
/home/<user>/
└── projects/
    ├── shared-infrastructure/
    │   ├── compose.yaml
    │   ├── .env
    │   ├── .env.example
    │   ├── README.md
    │   ├── SERVICES.md
    │   └── services.yaml
    │
    ├── project-a/
    │   ├── compose.yaml
    │   ├── .env
    │   └── ...
    │
    ├── project-b/
    │   ├── compose.yaml
    │   ├── .env
    │   └── ...
    │
    └── project-c/
        ├── compose.yaml
        ├── .env
        └── ...
```

Shared-инфраструктура MUST храниться отдельно от бизнес-проектов.

---

## 4. Категории сервисов

Все Docker-сервисы делятся на две основные категории:

```text
SHARED INFRASTRUCTURE
PROJECT SERVICES
```

---

## 5. Shared Infrastructure

Shared Infrastructure — сервисы, которыми могут пользоваться несколько проектов.

Типичные shared services:

```text
Ollama
RabbitMQ
n8n
Nginx / Traefik
Prometheus
Grafana
Loki
```

В отдельных случаях также MAY быть shared:

```text
Qdrant
Redis
PostgreSQL
```

Но только при наличии строгой логической изоляции данных.

---

## 6. Project Services

Следующие компоненты обычно принадлежат конкретному проекту:

```text
Backend
Frontend
Celery Worker
Celery Beat
project-specific PostgreSQL
project-specific Qdrant
project-specific Redis
project-specific migrations
project-specific background workers
```

Они MUST иметь независимый lifecycle от других проектов.

---

## 7. Ollama

Ollama SHOULD быть единым shared-сервисом на GPU-сервере.

Без специальной причины запрещено создавать:

```text
project-a-ollama
project-b-ollama
project-c-ollama
```

на одном физическом GPU.

Причины:

- модели занимают много дискового пространства;
- модели занимают VRAM;
- несколько Ollama конкурируют за один GPU;
- сложнее контролировать загрузку GPU;
- одинаковые модели скачиваются несколько раз.

Правильная схема:

```text
               Ollama
                  │
        ┌─────────┼─────────┐
        │         │         │
    Project A Project B Project C
```

Все приложения получают URL Ollama через environment variable:

```dotenv
OLLAMA_BASE_URL=http://ollama:11434
```

Hardcoded URL запрещён.

Неправильно:

```python
OLLAMA_URL = "http://localhost:11434"
```

Правильно:

```python
OLLAMA_URL = os.getenv(
    "OLLAMA_BASE_URL",
    "http://ollama:11434",
)
```

---

## 8. GPU

GPU считается shared physical resource.

Перед разработкой AI-сервиса MUST быть определено:

- какая модель используется;
- сколько VRAM она требует;
- сколько моделей может быть загружено одновременно;
- может ли сервис работать через очередь;
- нужно ли ограничивать параллелизм.

Приложение MUST NOT исходить из предположения, что вся GPU принадлежит только ему.

Проверка GPU на Linux:

```bash
nvidia-smi
```

Проверка GPU внутри Docker:

```bash
docker run --rm \
  --gpus all \
  nvidia/cuda:12.8.0-base-ubuntu24.04 \
  nvidia-smi
```

Проверка загрузки VRAM:

```bash
nvidia-smi \
  --query-gpu=name,memory.total,memory.used,memory.free \
  --format=csv
```

---

## 9. RabbitMQ

RabbitMQ SHOULD быть shared-сервисом.

Проекты MUST быть логически разделены.

Предпочтительный способ — RabbitMQ Virtual Hosts:

```text
/project-a
/project-b
/project-c
```

Пример:

```dotenv
RABBITMQ_URL=amqp://project_a_user:password@rabbitmq:5672/project-a
```

Другой проект:

```dotenv
RABBITMQ_URL=amqp://project_b_user:password@rabbitmq:5672/project-b
```

Очереди разных проектов MUST NOT случайно пересекаться.

---

## 10. Celery

Celery MUST NOT рассматриваться как shared infrastructure.

Celery Worker исполняет код конкретного приложения.

Правильно:

```text
RabbitMQ
   │
   ├── project-a-celery
   └── project-b-celery
```

Каждый проект MUST иметь собственный `celery-worker` / `celery-beat`, если они ему нужны.

---

## 11. n8n

n8n MAY быть shared.

Для development/internal environments рекомендуется:

```text
1 server
1 n8n
N workflows
```

Например:

```text
n8n
├── [PDRD] Analysis Main
├── [CONTRACT] Analyze Contract
└── [AVAILABILITY] Daily Scan
```

Для production отдельный экземпляр n8n MAY использоваться, если нужны:

- независимые обновления;
- разные версии n8n;
- разные security policies;
- независимые backups;
- строгая tenant isolation.

---

## 12. PostgreSQL

Есть два допустимых варианта.

### Variant A — PostgreSQL per project

```text
project-a-postgres
project-b-postgres
```

Рекомендуется по умолчанию для независимых проектов.

Внутренний порт у всех может быть одинаковым:

```text
5432
```

Это НЕ является конфликтом, если порт не публикуется одинаково на host.

### Variant B — shared PostgreSQL

Допускается:

```text
postgres
├── project_a_db
├── project_b_db
└── project_c_db
```

Каждый проект MUST иметь:

- отдельную database;
- отдельного user;
- отдельный password;
- минимальные permissions.

---

## 13. Qdrant

По умолчанию Qdrant MAY быть project-specific.

Например:

```text
project-a-qdrant
project-b-qdrant
```

Оба используют внутри контейнера `6333` без конфликтов.

При shared Qdrant collections MUST иметь namespace проекта.

Правильно:

```text
pdrd_normative_v2
pdrd_experience_v2

contract_ai_documents_v1
contract_ai_knowledge_v1
```

Запрещены слишком общие имена:

```text
documents
knowledge
data
vectors
collection1
```

---

## 14. Redis

Redis MAY быть shared.

При shared Redis проекты MUST быть разделены.

Предпочтительно использовать key prefix:

```text
pdrd:
contract-ai:
availability-agent:
```

При критичных данных предпочтительны отдельные Redis instances или отдельные ACL users.

---

## 15. Frontend

Frontend является project-specific сервисом.

Например:

```text
project-a-frontend
project-b-frontend
```

Для development MAY использоваться разные host ports:

```text
project-a → 8080
project-b → 8081
project-c → 8082
```

Production SHOULD использовать общий reverse proxy.

---

## 16. Reverse Proxy

На сервере SHOULD быть один общий reverse proxy, например Nginx или Traefik.

```text
                       80 / 443
                          │
                     Reverse Proxy
                          │
          ┌───────────────┼───────────────┐
          │               │               │
       PDRD UI       Contract UI         n8n
```

Это предпочтительнее схемы с большим количеством внешних портов:

```text
:8080
:8081
:8082
:8083
:8084
```

---

## 17. Docker ports

Необходимо различать:

```text
container port
host port
```

Например:

```yaml
services:
  postgres:
    image: postgres:16
```

PostgreSQL внутри Docker доступен как:

```text
postgres:5432
```

даже если секции `ports` вообще нет.

---

## 18. Не публиковать внутренние сервисы без необходимости

Внутренние сервисы SHOULD NOT публиковать host ports.

Не рекомендуется:

```yaml
postgres:
  ports:
    - "5432:5432"

redis:
  ports:
    - "6379:6379"
```

если к ним обращаются только Docker-контейнеры.

Backend должен обращаться к ним через Docker DNS:

```text
postgres:5432
redis:6379
```

---

## 19. Настраиваемые host ports

Если сервис действительно должен быть доступен с host, порт MUST задаваться через `.env`.

```dotenv
FRONTEND_PORT=8080
API_PORT=8101
```

Compose:

```yaml
ports:
  - "${FRONTEND_PORT}:80"
```

Жёстко заданные host ports SHOULD избегаться в проектах, которые будут размещаться рядом с другими проектами.

---

## 20. localhost внутри Docker

Критически важное правило:

В контейнере `localhost` означает текущий контейнер, а НЕ Docker host и НЕ другой контейнер.

Запрещено:

```text
http://localhost:11434
```

если Ollama находится в другом контейнере.

Использовать Docker service name:

```text
http://ollama:11434
```

или DNS name shared infrastructure.

---

## 21. Docker Networks

Каждый проект SHOULD иметь собственную private network.

Например:

```text
project-a-internal
project-b-internal
```

Shared infrastructure MUST иметь общую external network.

Стандартное имя:

```text
ai-shared
```

Создание:

```bash
docker network create ai-shared
```

В infrastructure Compose:

```yaml
networks:
  ai-shared:
    external: true
```

Project Compose:

```yaml
networks:
  default:
  ai-shared:
    external: true
```

Сервис, которому нужен Ollama:

```yaml
backend:
  networks:
    - default
    - ai-shared
```

PostgreSQL конкретного проекта SHOULD находиться только в private network проекта.

---

## 22. Пример network architecture

```text
                         ai-shared
                             │
       ┌─────────────────────┼────────────────────┐
       │                     │                    │
     Ollama                 n8n               RabbitMQ
       │                     │                    │
       │                     │                    │
 ┌─────┴─────┐        ┌──────┴──────┐      ┌─────┴─────┐
 │ Project A │        │ Project B   │      │ Project C │
 └───────────┘        └─────────────┘      └───────────┘
       │
       │ project-a-internal
       │
 ┌─────┼─────────┐
 │     │         │
API  PostgreSQL Qdrant
```

---

## 23. `container_name`

`container_name` SHOULD NOT использоваться без необходимости.

Не рекомендуется:

```yaml
services:
  postgres:
    container_name: postgres
```

Предпочтительно:

```yaml
services:
  postgres:
    image: postgres:16
```

Docker Compose создаст имена автоматически с project prefix.

---

## 24. Compose project name

Каждый Compose stack MUST иметь уникальное project name.

Предпочтительный способ:

```dotenv
COMPOSE_PROJECT_NAME=pdrd
```

Shared stack:

```dotenv
COMPOSE_PROJECT_NAME=shared
```

Другие проекты:

```dotenv
COMPOSE_PROJECT_NAME=contract-ai
COMPOSE_PROJECT_NAME=availability-agent
```

---

## 25. Volumes

Persistent volumes MUST быть привязаны к проекту.

Примеры:

```text
shared_ollama_data
shared_n8n_data

pdrd_postgres_data
pdrd_qdrant_data

contract_ai_postgres_data
```

Независимые PostgreSQL MUST NOT использовать один и тот же volume.

---

## 26. Environment Variables

Все инфраструктурные адреса MUST передаваться через environment variables.

Пример:

```dotenv
OLLAMA_BASE_URL=http://ollama:11434
QDRANT_URL=http://qdrant:6333
DATABASE_URL=postgresql://...
RABBITMQ_URL=amqp://...
REDIS_URL=redis://redis:6379
N8N_BASE_URL=http://n8n:5678
```

Hardcoded infrastructure URLs запрещены.

---

## 27. `.env.example`

Каждый проект MUST содержать `.env.example`.

Он MUST:

- содержать все необходимые переменные;
- НЕ содержать настоящие passwords;
- НЕ содержать production secrets;
- описывать значения по умолчанию.

---

## 28. Secrets

Запрещено commit:

```text
.env
private keys
tokens
passwords
API keys
N8N encryption keys
database dumps containing secrets
```

`.gitignore` MUST включать:

```gitignore
.env
.env.*
!.env.example
```

---

## 29. Healthcheck

Каждый HTTP microservice MUST предоставлять как минимум:

```text
GET /health/live
GET /health/ready
```

`live` отвечает: процесс жив?

`ready` отвечает: может ли сервис сейчас обслуживать запросы?

Readiness MAY проверять PostgreSQL, Qdrant, Ollama, RabbitMQ и другие обязательные зависимости.

---

## 30. Docker healthcheck

Compose SHOULD содержать healthcheck.

Пример:

```yaml
healthcheck:
  test:
    [
      "CMD",
      "curl",
      "-f",
      "http://localhost:8000/health/live"
    ]
  interval: 30s
  timeout: 5s
  retries: 3
```

---

## 31. `depends_on`

Если сервис зависит от готовности другого сервиса, SHOULD использоваться condition:

```yaml
depends_on:
  postgres:
    condition: service_healthy
```

Обычный `depends_on` гарантирует порядок запуска, но не готовность зависимости принимать запросы.

---

## 32. Проверка существующей инфраструктуры перед разработкой

Перед добавлением нового infrastructure service разработчик или LLM MUST проверить:

- существует ли сервис уже;
- как он называется;
- в какой Docker network находится;
- какой внутренний порт использует;
- какой host port опубликован;
- какой URL должен использовать проект.

Нельзя автоматически добавлять новый Ollama / RabbitMQ / n8n / Redis / Qdrant / PostgreSQL, не проверив существующую инфраструктуру.

---

## 33. Проверка Docker

```bash
docker --version
docker compose version
docker info
systemctl status docker --no-pager
```

---

## 34. Список контейнеров

Запущенные:

```bash
docker ps
```

Информативно:

```bash
docker ps \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

Все, включая остановленные:

```bash
docker ps -a
```

---

## 35. Проверка Docker Networks

```bash
docker network ls
```

Shared network:

```bash
docker network inspect ai-shared
```

Если отсутствует:

```bash
docker network create ai-shared
```

---

## 36. Проверка занятого host port

```bash
sudo ss -lntp
```

Конкретный порт:

```bash
sudo ss -lntp | grep ':11434'
sudo ss -lntp | grep ':5678'
sudo ss -lntp | grep ':6333'
```

---

## 37. Проверка порта

```bash
nc -zv localhost 11434
nc -zv localhost 5678
```

Если `nc` отсутствует:

```bash
sudo apt install netcat-openbsd
```

---

## 38. Проверка Ollama

Контейнер:

```bash
docker ps --filter name=ollama
```

HTTP:

```bash
curl -fsS http://localhost:11434/api/tags
```

Модели:

```bash
curl -fsS http://localhost:11434/api/tags |
python3 -m json.tool
```

Из контейнера, подключённого к `ai-shared`:

```bash
curl -fsS http://ollama:11434/api/tags
```

---

## 39. Проверка Qdrant

```bash
curl -fsS http://localhost:6333/
```

Collections:

```bash
curl -fsS http://localhost:6333/collections |
python3 -m json.tool
```

Docker:

```bash
docker ps --filter name=qdrant
```

---

## 40. Проверка n8n

```bash
docker ps --filter name=n8n
nc -zv localhost 5678
curl -I http://localhost:5678
```

---

## 41. Проверка RabbitMQ

```bash
docker ps --filter name=rabbitmq
nc -zv localhost 5672
```

Management UI:

```bash
nc -zv localhost 15672
```

---

## 42. Проверка PostgreSQL

```bash
docker ps --filter ancestor=postgres
```

Если опубликован host port:

```bash
nc -zv localhost 5432
```

Project-specific проверка:

```bash
docker compose exec postgres pg_isready
```

---

## 43. Проверка Redis

```bash
docker ps --filter ancestor=redis
```

При опубликованном host port:

```bash
redis-cli -h localhost -p 6379 ping
```

Ожидается:

```text
PONG
```

---

## 44. Infrastructure Registry

На сервере SHOULD существовать:

```text
~/projects/shared-infrastructure/SERVICES.md
```

Это человекочитаемый источник истины о shared infrastructure.

Пример:

```markdown
# Shared Services Registry

| Service | Docker service | Internal URL | Host access | Shared Network |
|---|---|---|---|---|
| Ollama | ollama | http://ollama:11434 | 127.0.0.1:11434 | ai-shared |
| n8n | n8n | http://n8n:5678 | 127.0.0.1:5678 | ai-shared |
| RabbitMQ | rabbitmq | rabbitmq:5672 | 127.0.0.1:5672 | ai-shared |
```

---

## 45. Machine-readable Infrastructure Registry

Также SHOULD существовать:

```text
~/projects/shared-infrastructure/services.yaml
```

Пример:

```yaml
version: 1

networks:
  shared:
    name: ai-shared

services:
  ollama:
    type: shared
    docker_service: ollama
    network: ai-shared
    internal_host: ollama
    internal_port: 11434
    protocol: http
    health_url: http://ollama:11434/api/tags
    gpu: true

  n8n:
    type: shared
    docker_service: n8n
    network: ai-shared
    internal_host: n8n
    internal_port: 5678
    protocol: http

  rabbitmq:
    type: shared
    docker_service: rabbitmq
    network: ai-shared
    internal_host: rabbitmq
    internal_port: 5672
    protocol: amqp
```

LLM SHOULD использовать этот файл как основной источник сведений об уже развёрнутой инфраструктуре.

---

## 46. Новый проект

Перед созданием `compose.yaml` новый проект MUST:

1. проверить `~/projects/shared-infrastructure/services.yaml`;
2. проверить реально запущенные контейнеры через `docker ps`;
3. проверить networks через `docker network ls`;
4. проверить необходимые shared services;
5. только после этого решить, какие сервисы переиспользовать, а какие создать внутри проекта.

---

## 47. Запрещённое поведение LLM

LLM MUST NOT автоматически:

- добавлять Ollama;
- добавлять RabbitMQ;
- добавлять n8n;
- добавлять Redis;
- добавлять PostgreSQL;
- добавлять Qdrant;
- назначать host ports;
- создавать shared Docker networks;

пока не проверена существующая инфраструктура.

---

## 48. Правило для LLM при отсутствии доступа к серверу

Если LLM не может самостоятельно выполнить команды, он MUST запросить вывод диагностических команд.

Минимальный набор:

```bash
docker ps \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'

docker network ls

sudo ss -lntp

nvidia-smi
```

После этого LLM MUST дождаться вывода.

LLM MUST NOT предполагать наличие сервиса без подтверждения.

---

## 49. Малые проверочные шаги

При удалённой настройке сервера команды SHOULD даваться небольшими блоками:

```text
2–4 команды
→ получить вывод
→ проверить
→ следующий шаг
```

Не рекомендуется выдавать длинную последовательность команд, если результат первых команд влияет на дальнейшие действия.

---

## 50. Проверка перед `docker compose up`

```bash
docker compose config --quiet &&
echo "COMPOSE OK"
```

---

## 51. Проверка после запуска

```bash
docker compose up -d
docker compose ps
```

Для проблемного сервиса:

```bash
docker compose logs --tail=100 <service>
```

---

## 52. Restart policy

Server services SHOULD использовать:

```yaml
restart: unless-stopped
```

если должны автоматически запускаться после reboot.

One-shot jobs, migrations, indexing и data import не должны использовать такой restart policy.

---

## 53. Indexing / migrations

Миграции, indexing и data import SHOULD быть отделены от обычного старта приложения.

`docker compose up` не должен неожиданно запускать полную длительную переиндексацию.

---

## 54. Observability

Новый backend SHOULD писать logs в stdout/stderr.

Контейнер должен диагностироваться через:

```bash
docker compose logs
```

---

## 55. Service naming

Имена должны отражать проект.

Примеры:

```text
pdrd-api
pdrd-celery
pdrd-qdrant

contract-ai-api
contract-ai-celery
```

---

## 56. Environment naming

Использовать:

```text
dev
stage
prod
```

При необходимости:

```text
pdrd-dev
pdrd-stage
pdrd-prod
```

---

## 57. Infrastructure dependency checklist

Перед добавлением нового проекта необходимо ответить:

```text
[ ] Требуется GPU?
[ ] Требуется Ollama?
[ ] Какая модель Ollama?
[ ] Требуется RabbitMQ?
[ ] Требуется Celery?
[ ] Требуется n8n?
[ ] Требуется PostgreSQL?
[ ] PostgreSQL shared или project-specific?
[ ] Требуется Qdrant?
[ ] Qdrant shared или project-specific?
[ ] Требуется Redis?
[ ] Какие host ports действительно необходимы?
[ ] Какая shared Docker network используется?
[ ] Какие persistent volumes создаются?
[ ] Какие healthchecks реализованы?
[ ] Как выполняются migrations?
[ ] Как выполняется indexing?
[ ] Как выполняется backup?
```

---

## 58. Definition of Done для инфраструктуры

Инфраструктура нового проекта считается готовой только если:

```text
[ ] docker compose config проходит
[ ] контейнеры запускаются
[ ] healthchecks проходят
[ ] нет конфликтов host ports
[ ] нет конфликтов container names
[ ] нет конфликтов volumes
[ ] shared services не дублируются
[ ] backend видит необходимые shared services
[ ] GPU доступна из нужного контейнера
[ ] secrets отсутствуют в Git
[ ] .env.example актуален
[ ] README содержит инструкции запуска
[ ] после reboot сервисы восстанавливаются корректно
```

---

## 59. Обязательная инструкция для LLM / AI Agent

> Не проектируй инфраструктуру проекта изолированно.
> Сначала установи существующую инфраструктуру хоста.
>
> Shared services должны переиспользоваться.
>
> Перед добавлением Ollama, RabbitMQ, n8n, Qdrant, PostgreSQL,
> Redis или другого инфраструктурного компонента проверь,
> существует ли он уже и должен ли новый проект использовать
> существующий экземпляр.
>
> Не назначай host ports без необходимости.
>
> Для общения между контейнерами используй Docker DNS и internal networks.
>
> Все инфраструктурные адреса должны задаваться через environment variables.
>
> Каждый проект должен быть независим по коду, данным и lifecycle,
> но может использовать shared infrastructure.
>
> При отсутствии сведений о сервере сначала запроси или выполни
> диагностические команды, а не делай предположения.

---

## 60. Практическое правило запуска shared stack

Shared services MUST запускаться из отдельного Compose project.

Рекомендуемая директория:

```text
~/projects/shared-infrastructure/
```

Рекомендуемое имя Compose project:

```dotenv
COMPOSE_PROJECT_NAME=shared
```

Типичный запуск:

```bash
cd ~/projects/shared-infrastructure
docker compose up -d
docker compose ps
```

Проверка shared stack из любой директории:

```bash
docker compose \
  --project-directory ~/projects/shared-infrastructure \
  -f ~/projects/shared-infrastructure/compose.yaml \
  ps
```

Либо глобальная проверка всех Docker-контейнеров:

```bash
docker ps
```

`docker compose ps` без `-f` / `--project-directory` показывает Compose project,
определённый текущей директорией и её Compose-файлом. Поэтому для shared services
следует либо перейти в `~/projects/shared-infrastructure`, либо явно указать
путь к Compose-файлу.

---

## 61. Shared infrastructure как отдельный Git repository

Shared infrastructure SHOULD храниться в отдельном Git repository.

Например:

```text
company/shared-infrastructure
```

На сервере:

```bash
cd ~/projects
git clone <repository-url> shared-infrastructure
```

Это позволяет:

- версионировать shared Compose;
- проводить code review изменений инфраструктуры;
- хранить `services.yaml`;
- хранить `.env.example`;
- документировать migrations shared-сервисов;
- независимо обновлять бизнес-проекты и инфраструктуру.

Бизнес-проекты MUST NOT быть владельцами shared Ollama / n8n / RabbitMQ.

---

## 62. Ownership shared services

Неправильно:

```text
PDRD-validation/compose.yaml
└── ollama

Contract-AI/compose.yaml
└── использует Ollama PDRD
```

Потому что удаление или остановка PDRD неожиданно останавливает инфраструктуру Contract-AI.

Правильно:

```text
shared-infrastructure/compose.yaml
├── ollama
├── n8n
└── rabbitmq

PDRD-validation/compose.yaml
└── pdrd services

Contract-AI/compose.yaml
└── contract-ai services
```

Shared stack имеет независимый lifecycle.

---

## 63. Практическая схема команд

### Общая инфраструктура

```bash
cd ~/projects/shared-infrastructure
docker compose up -d
docker compose ps
```

### PDRD

```bash
cd ~/projects/PDRD-validation
docker compose up -d
docker compose ps
```

### Contract AI

```bash
cd ~/projects/contract-analysis-ai
docker compose up -d
docker compose ps
```

### Все контейнеры сервера

Из любой директории:

```bash
docker ps
```

### Только shared Compose project из любой директории

```bash
docker compose \
  -f ~/projects/shared-infrastructure/compose.yaml \
  --project-directory ~/projects/shared-infrastructure \
  ps
```

---

## 64. Главный архитектурный принцип

```text
                   PHYSICAL SERVER
                         │
              ┌──────────┴───────────┐
              │                      │
       SHARED INFRA             PROJECTS
              │                      │
        ┌─────┼─────┐        ┌───────┼────────┐
        │     │     │        │       │        │
     Ollama  n8n RabbitMQ   PDRD  Contract  Other
        │           │        │
        │           │        ├── API
        │           │        ├── frontend
        │           │        ├── postgres
        │           │        ├── qdrant
        │           │        └── celery
        │           │
        └───────────┴──── ai-shared
```

Главное правило:

```text
Share infrastructure.
Isolate application state.
Do not expose ports unnecessarily.
Do not duplicate expensive services.
Discover infrastructure before creating infrastructure.
Keep shared infrastructure lifecycle independent from business projects.
```
