# Infrastructure Development Instructions

> Общий стандарт инфраструктуры, Docker Compose, shared services,
> сетевого взаимодействия и конфигурации новых проектов.
>
> Документ предназначен для разработчиков, DevOps/AI инженеров и
> LLM / AI coding agents.

Этот документ входит в переносимый instruction bundle:

```text
docs/
├── LLM_CONTEXT.md
├── ENGINEERING_GUIDELINES.md
├── INFRASTRUCTURE_INSTRUCTIONS.md
└── services.yaml
```

Начинать работу LLM SHOULD с `LLM_CONTEXT.md`.

---

## 1. Основной принцип

На одном физическом сервере могут одновременно работать несколько независимых
проектов.

```text
Server
├── shared-infrastructure
├── project-a
├── project-b
└── project-c
```

Проекты MUST:

1. не конфликтовать по host ports;
2. не конфликтовать по именам Compose projects;
3. не конфликтовать по volumes;
4. не конфликтовать по private networks;
5. не создавать дублирующую shared infrastructure без необходимости;
6. иметь изолированные application data;
7. иметь независимый lifecycle;
8. переиспользовать утверждённые shared services.

Главная модель:

```text
Share infrastructure.
Isolate application state.
Keep project lifecycle independent.
Do not expose ports unnecessarily.
Discover infrastructure before creating infrastructure.
```

---

## 2. MUST / SHOULD / MAY

- **MUST** — обязательное правило.
- **MUST NOT** — запрещённое действие.
- **SHOULD** — решение по умолчанию; отклонение требует причины.
- **MAY** — допустимый вариант.

---

## 3. Категории сервисов

Все сервисы делятся на:

```text
SHARED INFRASTRUCTURE
PROJECT SERVICES
```

### Shared infrastructure

Типичные shared services:

```text
vLLM shared-vlm
vLLM shared-embedding
Ollama — transitional
RabbitMQ
n8n
Open WebUI — optional human UI
reverse proxy
Prometheus
Grafana
Loki
```

В отдельных случаях MAY быть shared:

```text
Qdrant
Redis
PostgreSQL
```

только при явной логической изоляции.

### Project services

По умолчанию project-specific:

```text
backend
frontend
Celery worker
Celery beat
application PostgreSQL
application Qdrant
application Redis
migrations
application background workers
```

Они MUST иметь независимый lifecycle.

---

## 4. Shared infrastructure registry

Канонический machine-readable registry этого repository:

```text
docs/services.yaml
```

Если папка `docs/` передана LLM отдельно, используется предоставленный
`services.yaml` из instruction bundle.

Registry описывает **intended topology**, а не фактический runtime state.

Перед изменением инфраструктуры MUST дополнительно проверить runtime.

---

## 5. Рекомендуемая структура сервера

```text
/home/<user>/
└── projects/
    ├── shared-infrastructure/
    │   ├── compose.yaml
    │   ├── .env
    │   ├── .env.example
    │   ├── README.md
    │   └── docs/
    │       ├── LLM_CONTEXT.md
    │       ├── ENGINEERING_GUIDELINES.md
    │       ├── INFRASTRUCTURE_INSTRUCTIONS.md
    │       └── services.yaml
    │
    ├── project-a/
    │   ├── compose.yaml
    │   ├── .env
    │   └── ...
    │
    └── project-b/
        ├── compose.yaml
        ├── .env
        └── ...
```

Shared infrastructure MUST храниться отдельно от business projects.

---

## 6. Shared AI inference

AI inference является shared infrastructure.

Целевая схема:

```text
Application projects
        │
        ├──────────────> shared-vlm
        │                    │
        │                   vLLM
        │
        └──────────────> shared-embedding
                             │
                            vLLM
```

Application project MUST NOT поднимать собственный Ollama/vLLM или копию общей
модели, если подходящий shared endpoint уже существует.

### 6.1. Stable contracts

Для containers на том же Docker host использовать:

```text
http://shared-vlm:8000/v1
http://shared-embedding:8000
```

Для clients на другом компьютере или сервере использовать опубликованные host
endpoints:

```text
http://<shared-host>:<VLM-host-port>/v1
http://<shared-host>:<embedding-host-port>
```

Logical model names:

```text
shared-vlm
shared-embedding
```

Business application MUST NOT знать:

```text
GPU index;
количество GPU;
Tensor Parallel size;
число replicas;
HuggingFace physical model ID;
физическую GPU topology.
```

### 6.2. Deployment configuration

Следующие параметры являются configuration shared infrastructure:

```text
physical model ID;
model alias;
GPU devices;
Tensor Parallel size;
context limit;
GPU memory utilization;
concurrency limits;
queue limits;
bind IP;
host port.
```

Они MUST задаваться через deployment configuration / environment variables,
а не hardcode внутри business projects.

Один и тот же shared Compose должен поддерживать разные GPU hosts.

Resource capacity конкретного host является ответственностью deployment
operator, а не application architecture.

### 6.3. GPU placement

Inference service MUST получать явный набор доступных GPU через deployment
configuration.

Для multi-GPU Tensor Parallel перед deployment MUST быть проверена фактическая
GPU topology.

Нельзя выбирать GPU для TP только по принципу "любые свободные карты".

Если модель помещается на одной GPU, TP=1 является обязательным baseline для
benchmark.

Окончательный выбор между single-GPU, Tensor Parallel и несколькими replicas
принимается по benchmark, а не по предположению.

### 6.4. Model residency

Production shared models SHOULD оставаться resident в GPU VRAM на протяжении
lifecycle inference service.

Обычная production request path не должна выглядеть как:

```text
load model
-> inference
-> unload model
```

Для development/operator работы MAY запускаться только нужный inference service.

### 6.5. Concurrency и очереди

vLLM отвечает за:

```text
concurrent inference;
continuous batching;
short-lived inference scheduling;
bounded admission/backpressure.
```

RabbitMQ отвечает за:

```text
durable background jobs;
batch processing;
indexing;
retry;
workload smoothing.
```

Interactive request SHOULD идти непосредственно к shared inference endpoint.

Batch workload SHOULD использовать:

```text
RabbitMQ
-> bounded project worker
-> shared inference endpoint
```

### 6.6. Shared embedding

`shared-embedding` является общей embedding infrastructure.

Замена существующей embedding model/provider MUST учитывать compatibility
существующих vector indexes.

Нельзя silently менять:

```text
embedding model;
vector dimension;
preprocessing;
normalization.
```

Если vectors несовместимы, project выполняет controlled reindex/migration.

### 6.7. Open WebUI

Open WebUI MAY быть shared human-facing UI для:

```text
ручного общения с моделями;
prompt testing;
vision testing;
developer testing.
```

Он MUST NOT быть обязательной dependency business applications.

Open WebUI не является source of truth для vLLM model lifecycle.

Start/stop/restart inference и model cache administration выполняются shared
Compose/scripts.

### 6.8. LAN/VPN publication

Managed shared service, который предоставляет application API, user UI или
administration UI, MUST иметь configurable published host endpoint.

По умолчанию shared stack MAY использовать `0.0.0.0`, если host firewall
ограничивает доступ разрешёнными LAN/VPN source networks.

Infrastructure-private dependency, например `n8n-db`, SHOULD оставаться только
во внутренней Docker network.

Application-level API key является дополнительной защитой, но MUST NOT
рассматриваться как замена firewall.

### 6.9. Ollama transition

Ollama является transitional shared service.

Во время migration допустимо:

```text
existing consumers -> Ollama
new/migrated consumers -> shared-vlm
```

Новые проекты SHOULD использовать `shared-vlm`, если нет документированной
причины использовать Ollama.

Ollama удаляется только после проверки, что active consumers больше от него
не зависят.

### 6.10. Что сейчас не добавляется

Без отдельного архитектурного решения MUST NOT добавляться:

```text
Ray Serve;
Kubernetes / k3s;
GPU Operator;
service mesh;
request-level dynamic model load/unload;
project-local duplicate shared models.
```

---

## 7. GPU

GPU — shared physical resource.

Перед проектированием AI-service MUST определить:

- модель;
- ожидаемое потребление VRAM;
- context size;
- сколько моделей может быть loaded одновременно;
- допустимый parallelism;
- нужна ли очередь;
- допустима ли CPU fallback;
- как диагностируется загрузка GPU.

Проверка host:

```bash
nvidia-smi
```

Проверка внутри Docker:

```bash
docker run --rm \
  --gpus all \
  nvidia/cuda:12.8.0-base-ubuntu24.04 \
  nvidia-smi
```

Память:

```bash
nvidia-smi \
  --query-gpu=name,memory.total,memory.used,memory.free \
  --format=csv
```

Application MUST NOT исходить из предположения, что вся GPU принадлежит ему.

---

## 8. RabbitMQ

RabbitMQ SHOULD быть shared service.

Projects MUST быть логически разделены.

Предпочтительно:

```text
one vhost per project
one application user per project
```

Пример:

```dotenv
RABBITMQ_URL=amqp://project_a_user:password@rabbitmq:5672/project-a
```

Другой проект:

```dotenv
RABBITMQ_URL=amqp://project_b_user:password@rabbitmq:5672/project-b
```

Application projects SHOULD NOT использовать bootstrap admin account.

---

## 9. Celery

Celery worker и Celery beat являются project-specific, потому что исполняют
код конкретного приложения.

```text
shared RabbitMQ
      │
      ├── project-a-celery
      └── project-b-celery
```

Celery MUST NOT автоматически переноситься в shared infrastructure.

---

## 10. n8n

n8n MAY быть shared.

Для internal/development environments допустима схема:

```text
one server
one n8n
many workflows
```

Workflow names SHOULD иметь project namespace:

```text
[PDRD] Analysis Main
[CONTRACT] Analyze Contract
```

Отдельный n8n MAY быть нужен при:

- независимых update windows;
- разных версиях;
- разных security policies;
- строгой tenant isolation;
- независимых backups.

---

## 11. PostgreSQL

### Вариант A — PostgreSQL per project

Рекомендуется по умолчанию.

```text
project-a-postgres
project-b-postgres
```

Оба containers могут использовать internal port `5432`.

Host port не нужен, если к БД обращаются только containers проекта.

### Вариант B — shared PostgreSQL

Допустим только при осознанном решении.

Каждый проект MUST иметь:

- отдельную database;
- отдельного user;
- отдельный password;
- минимальные privileges;
- независимую backup/restore strategy.

n8n private PostgreSQL из shared stack MUST NOT использоваться как
application database.

---

## 12. Qdrant

По умолчанию Qdrant SHOULD быть project-specific.

При shared Qdrant collections MUST иметь project namespace.

Хорошо:

```text
pdrd_normative_v2
contract_ai_documents_v1
contract_ai_knowledge_v1
```

Плохо:

```text
documents
knowledge
data
vectors
collection1
```

---

## 13. Redis

Redis MAY быть project-specific или shared по явному решению.

При shared Redis использовать isolation, например:

```text
pdrd:
contract-ai:
availability-agent:
```

Для критичных данных предпочтительны ACL users или отдельные instances.

---

## 14. Frontend

Frontend является project-specific service.

Для development MAY использоваться отдельный host port.

Production SHOULD публиковать frontend/backend через общий reverse proxy,
а не накапливать большое количество публичных портов.

---

## 15. Reverse proxy

На одном host SHOULD быть общий reverse proxy, например Nginx или Traefik.

```text
                     80 / 443
                        │
                  Reverse Proxy
                        │
          ┌─────────────┼─────────────┐
          │             │             │
       PDRD UI      Contract UI      n8n
```

---

## 16. Container port и host port

Не путать:

```text
container port
host port
```

Container-to-container communication через Docker network не требует
публикации host port.

Например PostgreSQL доступен как:

```text
postgres:5432
```

даже без секции `ports`.

Internal databases, Redis и brokers SHOULD NOT публиковать host ports только
ради связи containers.

---

## 17. Настраиваемые host ports

Host ports shared services MUST быть configurable через environment variables.

Для shared infrastructure API/UI публикация host port является нормальной
частью architecture, потому что services могут использоваться:

```text
с containers на том же host;
с другого компьютера;
с другого application server;
из trusted LAN/VPN.
```

Container-to-container traffic на том же host всё равно SHOULD использовать
Docker DNS и internal port.

`.env.example` документирует полный каталог bind IP и host port variables.

---

## 18. Binding interfaces

Для managed shared API/UI services baseline:

```text
0.0.0.0
```

допустим и ожидаем, если host firewall ограничивает доступ разрешёнными
LAN/VPN source networks.

Deployment MAY вместо `0.0.0.0` указать конкретный LAN IP host.

Примеры отдельных bind variables:

```text
SHARED_VLM_BIND_IP
SHARED_EMBEDDING_BIND_IP
OPEN_WEBUI_BIND_IP
SHARED_BIND_IP
N8N_BIND_IP
RABBITMQ_BIND_IP
```

Infrastructure-private dependencies SHOULD не иметь published host port.

Пример: `n8n-db` не публикуется наружу и доступен только внутри private Docker
network.

---

## 19. localhost внутри Docker и host access

В container:

```text
localhost
127.0.0.1
```

означают текущий container.

Для другого service на том же Docker host использовать Docker DNS:

```text
http://shared-vlm:8000/v1
http://shared-embedding:8000
http://ollama:11434
http://n8n:5678
rabbitmq:5672
postgres:5432
```

Для client на другом host использовать IP/DNS shared server и опубликованный
host port.

Например:

```text
http://<shared-host>:8000/v1
http://<shared-host>:8001
http://<shared-host>:3000
```

---

## 20. Docker networks

Каждый application project SHOULD иметь private network.

Shared infrastructure использует external network:

```text
ai-shared
```

Создание:

```bash
docker network create ai-shared
```

Shared Compose:

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
    name: ai-shared
```

Только service, которому нужен shared dependency, подключается к `ai-shared`.

Project PostgreSQL SHOULD оставаться только в private network проекта.

---

## 21. `container_name`

`container_name` SHOULD NOT использоваться без необходимости.

Плохо:

```yaml
services:
  postgres:
    container_name: postgres
```

Предпочтительно доверить Compose формирование имени с project prefix.

---

## 22. Compose project name

Каждый Compose stack MUST иметь уникальное project name.

Пример:

```dotenv
COMPOSE_PROJECT_NAME=contract-ai
```

Shared stack:

```text
shared
```

Project name MAY задаваться через `name:` в Compose или через environment,
но итоговое имя MUST быть предсказуемым и уникальным.

---

## 23. Volumes

Persistent volumes MUST быть изолированы по ownership.

Примеры:

```text
shared_ollama_data
shared_n8n_data
contract_ai_postgres_data
pdrd_qdrant_data
```

Независимые PostgreSQL MUST NOT использовать один data volume.

---

## 24. Environment variables

Infrastructure addresses MUST передаваться через configuration.

Пример:

```dotenv
SHARED_VLM_BASE_URL=http://shared-vlm:8000/v1
SHARED_VLM_MODEL=shared-vlm
SHARED_EMBEDDING_BASE_URL=http://shared-embedding:8000/v1
SHARED_EMBEDDING_MODEL=shared-embedding
QDRANT_URL=http://qdrant:6333
DATABASE_URL=postgresql://...
RABBITMQ_URL=amqp://...
REDIS_URL=redis://redis:6379
N8N_BASE_URL=http://n8n:5678
```

Hardcoded environment-specific URLs запрещены в application logic.

---

## 25. `.env.example` и `.env`

Каждый project MUST содержать `.env.example`.

### `.env.example`

Это committed baseline и полный каталог поддерживаемых environment variables.

Он MUST:

- быть в Git;
- содержать non-secret defaults;
- содержать placeholders вместо настоящих secrets;
- отражать текущую configuration schema;
- позволять понять configuration без доступа к production `.env`.

### `.env`

Это private sparse override.

Он SHOULD содержать только:

```text
real secrets
environment-specific overrides
```

Например:

```dotenv
SECRET_KEY=...
POSTGRES_PASSWORD=...
APP_ENV=prod
```

Он SHOULD NOT быть полной копией `.env.example`.

Добавление новой non-secret настройки SHOULD требовать изменения:

```text
Settings/config schema
.env.example
Compose/application default, если нужен
```

и не должно заставлять обновлять каждый существующий `.env`.

---

## 26. Pydantic Settings precedence

Для Python/Pydantic проектов рекомендуемый baseline:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env.example", ".env"),
        case_sensitive=False,
        env_nested_delimiter="__",
    )
```

Порядок:

```text
.env.example
    ↓ overridden by
.env
    ↓ overridden by
real process environment
```

Если проект использует prefix, он MAY быть задан через `env_prefix`.

---

## 27. Docker Compose env precedence

Docker Compose не следует считать эквивалентом Pydantic Settings.

Compose **не обязан автоматически загружать `.env.example`**.

Поэтому для Compose:

- non-secret baseline SHOULD иметь default через `${VAR:-default}`;
- required secrets SHOULD использовать `${VAR:?message}`;
- `.env` остаётся sparse override;
- `.env.example` документирует полный каталог variables.

Пример:

```yaml
services:
  api:
    ports:
      - "${API_BIND_IP:-127.0.0.1}:${API_HOST_PORT:-8000}:8000"
    environment:
      SECRET_KEY: ${SECRET_KEY:?SECRET_KEY must be set}
```

---

## 28. Secrets

Запрещено commit:

```text
.env
private keys
tokens
passwords
API keys
encryption keys
database dumps with secrets
```

`.gitignore` SHOULD включать:

```gitignore
.env
.env.*
!.env.example
```

Production secrets MUST NOT печататься в logs или diagnostic output.

---

## 29. Health endpoints

Каждый HTTP backend SHOULD предоставлять:

```text
GET /health/live
GET /health/ready
```

`live` — процесс жив.

`ready` — service может принимать рабочие запросы.

Readiness MAY проверять обязательные dependencies.

---

## 30. Docker healthcheck

Compose SHOULD содержать healthcheck для long-running services.

Пример:

```yaml
healthcheck:
  test:
    - CMD
    - curl
    - -f
    - http://localhost:8000/health/live
  interval: 30s
  timeout: 5s
  retries: 3
```

---

## 31. `depends_on`

Если start зависит от readiness другого service, SHOULD использовать
health condition там, где Compose version это поддерживает:

```yaml
depends_on:
  postgres:
    condition: service_healthy
```

Обычный startup order не доказывает readiness.

---

## 32. Discovery перед добавлением infrastructure

Перед добавлением нового service разработчик или LLM MUST выяснить:

- существует ли такой service;
- кто им управляет;
- Docker service name;
- network;
- internal port;
- опубликован ли host port;
- какой URL должен использовать project;
- как проверяется health;
- как изолируются credentials/data.

Нельзя автоматически добавлять vLLM, Ollama, RabbitMQ, n8n, Redis, Qdrant или
PostgreSQL, не проверив существующую инфраструктуру и project requirements.

---

## 33. Runtime verification

Минимальный набор:

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

Если LLM не имеет shell access, он MUST запросить необходимый output.

---

## 34. Базовая Docker диагностика

```bash
docker --version
docker compose version
docker info
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

All containers:

```bash
docker ps -a
```

---

## 35. Проверка shared services

### shared-vlm

Из container в `ai-shared`:

```bash
curl -fsS http://shared-vlm:8000/health
```

### shared-embedding

Из container в `ai-shared`:

```bash
curl -fsS http://shared-embedding:8000/health
```

### Ollama — transitional

```bash
curl -fsS http://127.0.0.1:11434/api/tags
```

### n8n

```bash
curl -fsS http://127.0.0.1:5678/healthz
```

### RabbitMQ

```bash
docker compose exec rabbitmq rabbitmq-diagnostics -q ping
```

### PostgreSQL проекта

```bash
docker compose exec postgres pg_isready
```

### Qdrant проекта

Если опубликован только для диагностики:

```bash
curl -fsS http://127.0.0.1:6333/
```

---

## 36. Новый проект

Перед созданием project `compose.yaml` MUST:

1. прочитать `docs/services.yaml` или переданный `services.yaml`;
2. проверить runtime containers;
3. проверить Docker networks;
4. проверить необходимые shared services;
5. определить project-specific services;
6. определить private network;
7. определить, какие host ports реально нужны;
8. определить volumes;
9. определить healthchecks;
10. определить migration/indexing jobs;
11. определить secrets и configuration;
12. проверить GPU requirements, если применимо.

---

## 37. Запрещённое поведение LLM

LLM MUST NOT автоматически:

- добавлять project-local vLLM;
- добавлять второй Ollama;
- дублировать shared VLM или shared embedding model;
- добавлять второй shared RabbitMQ;
- добавлять второй shared n8n;
- делать PostgreSQL shared без решения;
- делать Qdrant shared без решения;
- назначать host ports без необходимости;
- подключать project database к shared network без причины;
- использовать host port для container-to-container traffic;
- считать `services.yaml` доказательством runtime state;
- хранить secrets в generated Git files.

---

## 38. Малые проверочные шаги

При удалённой настройке сервера, если дальнейшие действия зависят от output,
команды SHOULD даваться небольшими блоками:

```text
2–4 команды
→ получить output
→ проверить
→ следующий шаг
```

---

## 39. Проверка перед запуском

Перед `up`:

```bash
docker compose config --quiet &&
echo "COMPOSE OK"
```

После запуска:

```bash
docker compose up -d
docker compose ps
```

Проблемный service:

```bash
docker compose logs --tail=100 <service>
```

---

## 40. Restart policy

Server services SHOULD использовать:

```yaml
restart: unless-stopped
```

если должны восстановиться после reboot.

One-shot jobs, migrations, indexing и imports не должны бесконечно
перезапускаться.

---

## 41. Migrations / indexing / imports

Long-running или one-shot operations SHOULD быть отделены от обычного
application startup.

`docker compose up` не должен неожиданно запускать полную переиндексацию,
обучение модели или тяжёлый data import.

---

## 42. Observability

Backend SHOULD писать application logs в stdout/stderr.

Container должен диагностироваться через:

```bash
docker compose logs
```

Structured application logs SHOULD соответствовать
`ENGINEERING_GUIDELINES.md`.

---

## 43. Naming

Service names и resources SHOULD отражать ownership проекта.

Примеры:

```text
pdrd-api
pdrd-celery
contract-ai-api
contract-ai-celery
```

Environment naming:

```text
dev
stage
prod
```

---

## 44. Infrastructure checklist

Перед добавлением проекта ответить:

```text
[ ] Требуется shared-vlm?
[ ] Требуется shared-embedding?
[ ] Требуется transitional Ollama?
[ ] Требуется RabbitMQ?
[ ] Требуется Celery?
[ ] Требуется n8n?
[ ] Требуется PostgreSQL?
[ ] PostgreSQL shared или project-specific?
[ ] Требуется Qdrant?
[ ] Qdrant shared или project-specific?
[ ] Требуется Redis?
[ ] Какие host ports действительно необходимы?
[ ] Какая shared network используется?
[ ] Какие private networks нужны?
[ ] Какие persistent volumes создаются?
[ ] Какие healthchecks реализованы?
[ ] Как выполняются migrations?
[ ] Как выполняется indexing/import?
[ ] Как выполняется backup?
[ ] Какие secrets требуются?
[ ] .env.example содержит полный catalog?
[ ] .env остаётся sparse?
```

Для владельца shared inference deployment дополнительно:

```text
[ ] Какая physical model загружается?
[ ] Какие GPU ей доступны?
[ ] Какой TP/replica layout выбран?
[ ] Какой context limit?
[ ] Какие concurrency/backpressure limits?
[ ] Проверена ли GPU topology для multi-GPU TP?
```

---

## 45. Definition of Done для инфраструктуры

```text
[ ] docker compose config проходит
[ ] containers запускаются
[ ] healthchecks проходят
[ ] нет конфликтов host ports
[ ] нет конфликтов Compose project names
[ ] нет конфликтов volumes
[ ] shared services не дублируются
[ ] project data изолированы
[ ] backend видит нужные shared services по Docker DNS
[ ] GPU доступна нужному container, если требуется
[ ] secrets отсутствуют в Git
[ ] .env.example актуален
[ ] .env не требуется заполнять non-secret defaults без необходимости
[ ] README содержит запуск и диагностику
[ ] migrations/indexing имеют явный lifecycle
[ ] после reboot long-running services восстанавливаются корректно
```

---

## 46. Shared infrastructure ownership

Shared infrastructure SHOULD храниться в отдельном repository и Compose project.

```text
shared-infrastructure
├── shared-vlm
├── shared-embedding
├── Ollama (transitional)
├── Open WebUI (optional)
├── n8n
└── RabbitMQ

Project A
Project B
Project C
```

Business project MUST NOT быть владельцем shared service, от которого
независимо зависит другой business project.

Плохо:

```text
Project A compose
└── vLLM / Ollama

Project B
└── depends on Project A model runtime
```

Правильно:

```text
shared-infrastructure
├── shared-vlm
└── shared-embedding

Project A ─┐
Project B ─┼──> shared inference
Project C ─┘
```

---

## 47. Главная схема

```text
                    PHYSICAL SERVER
                          │
              ┌───────────┴───────────┐
              │                       │
        SHARED INFRA              PROJECTS
              │                       │
   ┌──────────┼──────────┐      ┌─────┼────────┐
   │          │          │      │     │        │
shared-vlm shared-embedding   API frontend  workers
   │          │                 │
   └──────────┴────────── ai-shared
              │                 │
        n8n / RabbitMQ    project private net
                                │
                      PostgreSQL / Qdrant / Redis
```

Optional human access:

```text
Developer
    ↓
Open WebUI
    ↓
shared-vlm
```

Главное правило:

```text
Share infrastructure.
Isolate application state.
Use stable logical inference endpoints.
Keep physical model/GPU placement in shared deployment configuration.
Do not expose ports unnecessarily.
Do not duplicate expensive services.
Discover runtime before changing infrastructure.
Keep shared lifecycle independent from business projects.
```
