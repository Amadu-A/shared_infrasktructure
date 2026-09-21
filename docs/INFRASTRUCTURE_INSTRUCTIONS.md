<!-- docs/INFRASTRUCTURE_INSTRUCTIONS.md -->

# Infrastructure Development Instructions

Этот документ описывает правила работы с инфраструктурой проектов и, прежде всего,
с **переиспользуемыми shared services**.

Главная цель документа — не позволить новому application project:

- повторно поднимать уже существующий shared service;
- встраивать shared runtime внутрь business service;
- создавать собственную копию общей модели, broker или automation platform;
- связывать business project с физической GPU topology;
- превращать один business project во владельца инфраструктуры, которой пользуются другие проекты.

Читать этот документ, когда задача затрагивает:

```text
Docker / Docker Compose;
deployment;
networks;
host ports;
GPU;
shared AI inference;
RabbitMQ;
n8n;
shared service integration;
operational scripts;
environment configuration.
```

Если задача добавляет, изменяет или использует shared service, LLM / developer
**MUST дополнительно прочитать актуальный `docs/services.yaml`**.

Ключевые слова:

- **MUST** — обязательное правило;
- **MUST NOT** — запрещённое действие;
- **SHOULD** — решение по умолчанию, отклонение требует причины;
- **MAY** — допустимый вариант.

---

## 1. Главный принцип

```text
Share infrastructure.
Isolate application state.
Keep project lifecycle independent.
Reuse existing shared services.
Use stable logical contracts.
Discover runtime before changing infrastructure.
```

На одном физическом host могут работать несколько независимых проектов:

```text
HOST
├── shared-infrastructure
├── project-a
├── project-b
└── project-c
```

`shared-infrastructure` владеет общими сервисами.

Business projects владеют только своей application logic, project-specific state,
workers, database/vector storage и другими компонентами, которые действительно
принадлежат конкретному проекту.

---

## 2. Source of truth и обязательный discovery

Machine-readable registry shared services:

```text
docs/services.yaml
```

Он описывает intended topology и ownership, но **не доказывает**, что service
в данный момент запущен.

Перед infrastructure change MUST:

1. прочитать актуальный `docs/services.yaml`;
2. прочитать актуальный `compose.yaml` затрагиваемого stack;
3. прочитать связанные `.env.example` / configuration / scripts;
4. проверить actual runtime;
5. только после этого предлагать новый service или изменение существующего.

Минимальная runtime-проверка:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

```bash
docker network ls
```

```bash
sudo ss -lntp
```

Для GPU-related задачи:

```bash
nvidia-smi
```

Shared stack:

```bash
cd ~/projects/shared-infrastructure
```

```bash
docker compose ps
```

```bash
./scripts/check.sh
```

Если shell access отсутствует, LLM MUST запросить relevant output у пользователя.

LLM MUST NOT проектировать infrastructure:

- по памяти;
- по старому сообщению;
- по старому SHA;
- по предположению о составе shared services;
- только на основании `services.yaml` без runtime verification, если runtime важен для решения.

---

## 3. Ownership: shared и project-specific

Перед добавлением любого infrastructure component MUST определить его owner.

### 3.1. Shared service

Service считается shared, если:

- им независимо пользуются несколько проектов;
- его lifecycle не должен зависеть от lifecycle одного business project;
- он зарегистрирован в `docs/services.yaml` как `managed_here: true`;
- его конфигурацией и обновлением управляет `shared-infrastructure`.

Business project **MUST NOT** повторно создавать такой service.

### 3.2. Project-specific service

Project-specific service:

- исполняет код конкретного приложения;
- хранит application-specific state;
- имеет независимый lifecycle;
- удаляется/обновляется вместе с project без влияния на другие проекты.

По умолчанию project-specific:

```text
backend;
frontend;
application PostgreSQL;
application Qdrant;
application Redis;
Celery worker;
Celery beat;
project migrations;
project background workers;
project-specific import/indexing jobs.
```

PostgreSQL, Qdrant или Redis MAY быть shared только после отдельного архитектурного
решения и определения logical isolation, credentials, backup и ownership.

---

## 4. Текущие переиспользуемые shared services

Актуальный полный registry всегда брать из `docs/services.yaml`.

На текущей архитектуре к reusable shared infrastructure относятся:

```text
shared-vlm
shared-embedding
Open WebUI
RabbitMQ
n8n
```

Также существует:

```text
n8n-db
```

но это **private infrastructure dependency самого n8n**, а не application database.

### 4.1. `shared-vlm`

Назначение:

```text
общая LLM/VLM inference infrastructure
для нескольких проектов и пользователей
```

Runtime:

```text
vLLM
```

Stable same-host contract:

```text
base URL: http://shared-vlm:8000/v1
model:    shared-vlm
```

Business project MUST использовать logical contract и MUST NOT знать:

```text
physical Hugging Face model ID;
model revision;
GPU index;
GPU model/count;
TP/DP layout;
VRAM size;
physical GPU topology.
```

Эти параметры принадлежат deployment configuration `shared-infrastructure`.

### 4.2. `shared-embedding`

Назначение:

```text
общая embedding infrastructure
```

Stable same-host contract:

```text
base URL: http://shared-embedding:8000/v1
model:    shared-embedding
```

Типичные endpoints:

```text
/v1/embeddings
/pooling
```

Business project MUST NOT поднимать собственную копию общей embedding model,
если существующий shared endpoint удовлетворяет требованиям проекта.

### 4.3. Open WebUI

Назначение:

```text
human-facing chat;
prompt testing;
vision testing;
manual verification shared models.
```

Same-host endpoint:

```text
http://open-webui:8080
```

Open WebUI:

- MAY использоваться разработчиками и пользователями;
- MUST NOT быть dependency business application;
- MUST NOT владеть model lifecycle;
- MUST NOT заставлять project поднимать собственный model runtime.

### 4.4. RabbitMQ

RabbitMQ — общий broker.

Same-host endpoint:

```text
rabbitmq:5672
```

Projects SHOULD использовать:

```text
one vhost per project
one application user per project
```

Application MUST NOT использовать bootstrap/admin account как обычный runtime account.

### 4.5. n8n

n8n — shared automation/orchestration service.

Same-host endpoint:

```text
http://n8n:5678
```

Workflow names SHOULD иметь project namespace:

```text
[PDRD] ...
[CONTRACT] ...
[PROJECT-X] ...
```

Новый project MUST NOT автоматически поднимать собственный n8n только потому,
что ему нужен workflow.

### 4.6. `n8n-db`

`n8n-db` принадлежит только n8n.

Он:

```text
не является shared application PostgreSQL;
не должен использоваться business projects;
не должен публиковаться наружу только ради application access.
```

---

## 5. Главное anti-duplication rule

Если service в `docs/services.yaml` имеет:

```yaml
managed_here: true
```

то новый business project MUST считать его внешней shared dependency.

Новый project MUST NOT:

```text
добавлять копию этого service в свой compose;
добавлять model runtime в свой Dockerfile;
поднимать отдельный broker только для обычного использования;
поднимать отдельный n8n без явной причины;
скачивать и обслуживать общую model локально;
делать project owner'ом shared service;
копировать shared persistent volume;
копировать shared admin credentials.
```

Правильная модель:

```text
shared-infrastructure
├── shared-vlm
├── shared-embedding
├── RabbitMQ
├── n8n
└── Open WebUI

Project A ─┐
Project B ─┼──> shared services
Project C ─┘
```

Неправильная модель:

```text
Project A
├── backend
├── own-vllm
├── own-rabbitmq
└── own-n8n

Project B
├── backend
├── another-vllm
├── another-rabbitmq
└── another-n8n
```

---

## 6. Что MAY находиться в новом project

Запрет на дублирование shared runtime **не запрещает client/adaptor code**.

Business project MAY иметь:

```text
VllmClient / SharedVlmClient;
EmbeddingClient;
RabbitMQ publisher/consumer adapter;
n8n API client;
configuration для shared endpoint;
health/readiness probe к required dependency;
retry / timeout / circuit-breaker logic;
application-specific DTO/ports/interfaces.
```

То есть project хранит **клиент**, но не владеет **сервером**.

Пример:

```text
Project
├── application/
│   └── ports/
│       └── vision_model.py
│
├── infrastructure/
│   └── shared_ai/
│       └── vllm_client.py
│
└── compose.yaml
    └── НЕ содержит shared-vlm
```

Это правильный dependency direction.

---

## 7. Как подключать новый project к shared services

### 7.1. Project на том же Docker host

Application container SHOULD подключаться к external network:

```text
ai-shared
```

Пример project Compose:

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

Private application dependencies SHOULD оставаться в project network.

Например:

```text
api
├── project private network -> postgres / qdrant / redis
└── ai-shared               -> shared-vlm / RabbitMQ / n8n
```

### 7.2. Stable Docker DNS

На том же host использовать Docker DNS/internal ports:

```text
shared-vlm:       http://shared-vlm:8000/v1
shared-embedding: http://shared-embedding:8000/v1
Open WebUI:       http://open-webui:8080
n8n:              http://n8n:5678
RabbitMQ:         rabbitmq:5672
```

Не использовать host-published port для container-to-container traffic без причины.

### 7.3. Project на другом host

Использовать:

```text
SHARED_PUBLIC_HOST
+
published host port
```

Пример:

```text
http://<SHARED_PUBLIC_HOST>:8000/v1
http://<SHARED_PUBLIC_HOST>:8001/v1
http://<SHARED_PUBLIC_HOST>:5678
<SHARED_PUBLIC_HOST>:5672
```

Точные host ports MUST браться из актуальных:

```text
docs/services.yaml
.env.example
deployment .env
```

### 7.4. `localhost` внутри Docker

Внутри container:

```text
localhost
127.0.0.1
```

означают текущий container.

Они НЕ означают:

```text
Docker host;
shared-vlm;
RabbitMQ;
n8n;
PostgreSQL другого container.
```

Для другого container на той же Docker network использовать service DNS name.

---

## 8. Cross-project lifecycle

Business project MUST сохранять независимый lifecycle.

Остановка:

```text
Project A
```

не должна останавливать:

```text
shared-vlm
RabbitMQ
n8n
shared-embedding
Project B
```

И наоборот, business project MUST уметь корректно переживать временную
недоступность shared dependency через:

```text
timeout;
controlled retry;
backpressure;
graceful error handling;
readiness/health semantics.
```

Business project SHOULD NOT пытаться самостоятельно restart/recreate shared service.

Lifecycle shared service принадлежит `shared-infrastructure`.

---

## 9. Shared AI inference

### 9.1. Target runtime

Target runtime:

```text
vLLM
```

Application code SHOULD использовать stable logical service/model identity.

Для VLM:

```text
base URL: http://shared-vlm:8000/v1
model:    shared-vlm
```

Для embedding:

```text
base URL: http://shared-embedding:8000/v1
model:    shared-embedding
```

### 9.2. Physical model configuration

Business project MUST NOT hardcode:

```text
Qwen/... physical model ID;
HF revision;
GPU 0/1/2/3;
Tensor Parallel size;
Data Parallel size;
max model context;
GPU memory utilization;
physical topology.
```

Это configuration shared deployment.

Один и тот же logical contract SHOULD сохраняться между:

```text
DEV host;
STAGE host;
PROD host;
single-GPU host;
multi-GPU host.
```

Project должен менять endpoint/configuration environment, а не business logic.

### 9.3. Model residency

Production shared model SHOULD оставаться resident в GPU memory в течение
lifecycle inference service.

Normal request path MUST NOT быть:

```text
request
-> load model
-> inference
-> unload model
```

Одна загруженная shared model обслуживает запросы нескольких projects/users.

### 9.4. Concurrency

vLLM отвечает за:

```text
concurrent inference;
continuous batching;
inference scheduling;
bounded admission/backpressure.
```

Project MUST NOT создавать отдельный model process на каждого пользователя или запрос.

### 9.5. Interactive и batch

Interactive workload:

```text
user/API
-> shared inference
```

Durable/background workload:

```text
Project API
-> RabbitMQ
-> bounded project worker
-> shared inference
```

RabbitMQ используется для durable job lifecycle, retry и workload smoothing.

RabbitMQ не нужен только для того, чтобы сделать обычный interactive model call.

---

## 10. GPU и hardware portability

GPU является physical shared resource, но business applications не должны
знать physical layout.

Shared deployment configuration определяет:

```text
MODEL_ID / MODEL_REVISION;
GPU_DEVICES;
TP_SIZE;
DP_SIZE;
MAX_MODEL_LEN;
GPU_MEMORY_UTILIZATION;
concurrency / queue limits.
```

Для `TP_SIZE > 1` operator MUST учитывать фактическую GPU topology и benchmark.

Нельзя выбирать multi-GPU TP по принципу:

```text
любые две свободные карты
```

без проверки topology.

Если модель помещается на одной GPU, `TP=1` SHOULD быть baseline для benchmark.

Окончательный выбор между:

```text
single GPU;
TP;
несколькими replicas / DP;
```

делается по измерениям, а не по размеру модели "на глаз".

Architecture MUST NOT автоматически запрещать нескольким services использовать
один physical GPU только из-за совпадения GPU index.

Но совместное использование допустимо только если operator подтвердил:

```text
VRAM feasibility;
runtime stability;
отсутствие OOM;
приемлемую latency/throughput.
```

Silent CPU fallback для shared inference не допускается.

---

## 11. Embedding compatibility

Embedding identity является частью data contract.

Нельзя silently менять:

```text
embedding model;
model revision;
vector dimension;
preprocessing;
normalization;
multimodal processing semantics.
```

Если project имеет существующий vector index, смена embedding runtime/model
MUST сопровождаться compatibility check.

Если vectors несовместимы:

```text
создать controlled migration plan;
при необходимости создать новую collection/index;
выполнить reindex;
переключить consumers;
проверить retrieval quality;
только затем удалить старый index/runtime.
```

Project MUST NOT смешивать несовместимые старые и новые vectors без явно
подтверждённой совместимости.

---

## 12. RabbitMQ: правила переиспользования

RabbitMQ server является shared.

Project-specific:

```text
exchange;
queue;
routing key;
vhost;
application user;
consumer/worker code.
```

Shared:

```text
RabbitMQ broker lifecycle;
broker container;
management UI;
base infrastructure.
```

Предпочтительно:

```text
one vhost per project
one application user per project
```

Пример logical separation:

```text
/pdrd
/contract-ai
/project-x
```

Project Celery worker остаётся project-specific:

```text
shared RabbitMQ
      │
      ├── project-a worker
      ├── project-b worker
      └── project-c worker
```

Нельзя переносить application worker в shared только потому, что broker shared.

---

## 13. n8n: правила переиспользования

n8n lifecycle принадлежит shared infrastructure.

Project MAY создавать:

```text
workflows;
credentials, если политика безопасности допускает;
webhooks;
project-specific workflow conventions.
```

Project MUST NOT автоматически создавать отдельный n8n container.

Workflow names SHOULD иметь namespace:

```text
[PDRD] ...
[CONTRACT] ...
[PROJECT-X] ...
```

Отдельный n8n MAY быть оправдан только отдельным архитектурным решением, например:

```text
жёсткая tenant isolation;
несовместимая версия;
независимое maintenance window;
отдельная security policy;
отдельный backup/restore boundary.
```

---

## 14. Project-specific databases и data services

### PostgreSQL

Application PostgreSQL SHOULD быть project-specific.

Если несколько проектов должны использовать один PostgreSQL service,
это требует отдельного решения по:

```text
database isolation;
users/credentials;
privileges;
backup;
restore;
upgrade lifecycle.
```

`n8n-db` MUST NOT использоваться как application database.

### Qdrant

Qdrant SHOULD быть project-specific по умолчанию.

Если Qdrant становится shared:

```text
collections MUST иметь project namespace;
embedding identity MUST быть документирована;
backup/migration ownership MUST быть определён.
```

### Redis

Redis MAY быть project-specific или shared только после определения:

```text
namespace;
ACL;
data sensitivity;
eviction policy;
persistence;
ownership.
```

---

## 15. Когда duplicate service всё же допустим

Дублирование shared service — исключение, а не default.

Отдельный экземпляр MAY быть создан только если есть документированная причина,
например:

```text
несовместимая major version;
security/tenant isolation;
regulatory boundary;
необходим независимый upgrade window;
несовместимый model/runtime;
экспериментальный sandbox;
нагрузка требует отдельного physical deployment;
shared service не предоставляет требуемый contract.
```

Перед таким решением MUST:

1. проверить, нельзя ли расширить существующий shared contract;
2. описать причину;
3. указать нового owner;
4. определить network/ports/volumes/secrets;
5. определить lifecycle и backup;
6. убедиться, что новый service не маскируется под существующий logical contract;
7. получить явное архитектурное решение.

LLM MUST NOT создавать duplicate service "на всякий случай".

---

## 16. Docker ports, networks и isolation

### Container port vs host port

Container-to-container communication не требует публикации host port.

Например:

```text
postgres:5432
rabbitmq:5672
shared-vlm:8000
```

доступны внутри соответствующих Docker networks без отдельного host port.

Host port публикуется только когда service должен быть доступен:

```text
из trusted LAN/VPN;
с другого application host;
человеку через UI/admin interface;
external callback/webhook consumer.
```

### `ai-shared`

`ai-shared` — external cross-project network для approved shared services.

Новый project SHOULD подключать к `ai-shared` только те services, которым
действительно нужен shared dependency.

Project database SHOULD оставаться в private project network.

### `container_name`

`container_name` SHOULD NOT использоваться без необходимости.

Compose project prefix даёт более безопасную изоляцию и уменьшает collisions.

### Compose project name

Каждый stack MUST иметь уникальное Compose project name.

### Volumes

Persistent volume MUST иметь понятного owner.

Независимые stateful services MUST NOT разделять один data volume без явного
архитектурного решения.

---

## 17. Public access и security

`SHARED_PUBLIC_HOST` — IP/DNS, который используют approved clients на других hosts.

Это НЕ bind address.

Bind variables определяют local interface, например:

```text
SHARED_VLM_BIND_IP
SHARED_EMBEDDING_BIND_IP
OPEN_WEBUI_BIND_IP
N8N_BIND_IP
RABBITMQ_BIND_IP
```

Для trusted LAN/VPN `0.0.0.0` MAY быть допустим, если host firewall ограничивает
source networks.

Application-level API key является дополнительной защитой, но не заменяет firewall.

Infrastructure-private dependency SHOULD не иметь published host port.

Secrets MUST NOT попадать в Git или diagnostics/logs.

---

## 18. Environment configuration

`.env.example`:

```text
committed;
полный catalog поддерживаемых variables;
safe defaults;
placeholders вместо secrets;
не содержит production secrets.
```

`.env`:

```text
private;
sparse;
real secrets;
environment-specific overrides;
не коммитится.
```

Compose non-secret default SHOULD использовать:

```yaml
${VAR:-default}
```

Required secret SHOULD использовать:

```yaml
${VAR:?VAR must be set}
```

или equivalent validation script.

После изменения container environment/config обычный:

```bash
docker compose restart
```

обычно не применяет новое environment.

Нужен recreate:

```bash
docker compose up -d --force-recreate <service>
```

Не использовать:

```bash
docker compose down -v
```

без осознанной необходимости, потому что `-v` может удалить persistent data/model cache.

---

## 19. Health, readiness и lifecycle

Long-running service SHOULD иметь:

```text
healthcheck;
restart policy;
bounded logs;
persistent volume, если service stateful.
```

Если service зависит от readiness другой dependency, startup logic SHOULD
использовать readiness/health semantics, а не только порядок запуска.

Cross-project dependency нельзя выразить обычным `depends_on` между независимыми
Compose projects.

Поэтому business application SHOULD:

```text
иметь timeout;
корректно обрабатывать connection failure;
при необходимости выполнять bounded retry;
не считать факт запуска собственного container доказательством готовности shared service.
```

One-shot:

```text
migration;
indexing;
data import;
reindex;
model benchmark;
```

не должны автоматически становиться бесконечно перезапускаемыми long-running services.

---

## 20. Logging и observability

Container SHOULD быть диагностируем через:

```bash
docker compose logs
```

Logs MUST иметь finite rotation/retention policy.

High-frequency infrastructure path MUST NOT создавать uncontrolled log spam.

Для shared inference SHOULD быть доступны метрики, позволяющие оценивать:

```text
health;
active/waiting requests;
latency;
throughput;
GPU/VRAM utilization;
queue/backpressure;
errors.
```

Business project не должен самостоятельно управлять lifecycle shared monitoring
компонентов, если они принадлежат shared stack.

---

## 21. Как добавлять новый shared service

Новый service НЕ становится shared только потому, что его "могут когда-нибудь использовать".

Перед добавлением нового shared service MUST ответить:

```text
Кто owner?
Какие проекты используют его сейчас?
Почему project-specific instance недостаточен?
Какой stable logical contract?
Какой Docker DNS name?
Какая network?
Какой internal port?
Нужен ли published host port?
Какая authentication?
Какие credentials/isolation boundaries?
Какие volumes?
Какой backup/restore?
Какой healthcheck?
Как обновляется service?
Как откатить изменение?
```

После принятия решения MUST согласованно обновить:

```text
shared compose/config;
.env.example;
docs/services.yaml;
Infrastructure Instructions, если меняется правило;
bootstrap/check scripts, если требуется;
README/SERVICES, если они описывают новый public/operator workflow.
```

Не добавлять service только в один файл документации.

---

## 22. Пошаговый алгоритм LLM при новом project

Если создаётся новый business project, LLM MUST действовать так:

1. Прочитать актуальный `docs/services.yaml`.
2. Составить список required external dependencies.
3. Для каждой dependency проверить `managed_here`.
4. Если `managed_here: true`:
   - НЕ добавлять server container в project;
   - подключить project как client;
   - использовать stable logical endpoint;
   - добавить только нужную client configuration/adaptor.
5. Если service отсутствует:
   - определить, project-specific он или кандидат в shared;
   - не объявлять его shared автоматически.
6. Определить project private network.
7. Подключить только нужные application services к `ai-shared`.
8. Не публиковать host ports без необходимости.
9. Определить project volumes/state.
10. Проверить secrets.
11. Добавить health/retry semantics для required shared dependencies.
12. Проверить runtime после запуска.

---

## 23. Stop conditions для LLM

LLM MUST остановиться и уточнить архитектурное решение, если собирается:

```text
добавить второй vLLM/model server;
добавить отдельную копию общей embedding model;
добавить отдельный RabbitMQ;
добавить отдельный n8n;
сделать application PostgreSQL shared;
сделать application Qdrant shared;
перенести project worker в shared;
зависеть от GPU index/model ID в business code;
выдать admin shared credentials application runtime;
подключить project DB к shared network без причины;
опубликовать stateful/private service наружу без причины;
создать duplicate service "для удобства".
```

Исключение возможно только после явно сформулированной причины.

---

## 24. Что не добавлять автоматически

Без отдельного архитектурного решения MUST NOT добавляться:

```text
project-local vLLM, если подходит shared-vlm;
duplicate shared embedding model;
duplicate RabbitMQ;
duplicate n8n;
Ray Serve;
Kubernetes / k3s;
GPU Operator;
service mesh;
request-level dynamic model load/unload;
gateway только "на будущее";
shared PostgreSQL/Qdrant/Redis без isolation design.
```

---

## 25. Runtime operations

Перед запуском:

```bash
docker compose config --quiet
```

Запуск:

```bash
docker compose up -d
```

Проверка:

```bash
docker compose ps
```

Shared stack:

```bash
./scripts/check.sh
```

Диагностика отдельного service:

```bash
docker compose logs --tail=100 <service>
```

После изменения env/config:

```bash
docker compose up -d --force-recreate <service>
```

Перед destructive operation developer/LLM MUST явно объяснить последствия.

---

## 26. Infrastructure Definition of Done

Перед завершением infrastructure task проверить:

```text
[ ] Прочитан актуальный compose/config/scripts.
[ ] Прочитан services.yaml, если затронут shared.
[ ] Runtime state проверен.
[ ] Для каждой dependency определён owner.
[ ] Existing shared services переиспользованы.
[ ] Shared service не встроен в business project.
[ ] Нет duplicate model server/broker/n8n.
[ ] Application содержит только clients/adapters к shared runtime.
[ ] Stable logical endpoints используются вместо physical model/GPU details.
[ ] ai-shared подключена только там, где нужна.
[ ] Project private state остаётся в project network.
[ ] Host ports публикуются только при необходимости.
[ ] Volumes имеют понятного owner.
[ ] Secrets отсутствуют в Git/logs.
[ ] .env.example отражает новые variables.
[ ] Health/retry/backpressure semantics определены.
[ ] GPU parameters остаются deployment configuration.
[ ] Для TP>1 проверена topology/benchmark.
[ ] Для embedding migration проверена vector compatibility.
[ ] docker compose config проходит.
[ ] Runtime проверен после запуска.
[ ] Для migration/reindex/recreate есть явные instructions.
[ ] Rollback понятен.
```

---

## 27. Итоговая модель

```text
                         SHARED INFRASTRUCTURE
                    owner: shared-infrastructure
                               │
          ┌────────────────────┼─────────────────────┐
          │                    │                     │
     shared-vlm         shared-embedding         RabbitMQ
          │                    │                     │
          ├──────────────┐     │                     │
          │              │     │                     │
      Open WebUI        n8n    │                     │
                         │     │                     │
                      n8n-db   │                     │
                    (private)  │                     │
                               │
                          ai-shared
                               │
              ┌────────────────┼────────────────┐
              │                │                │
          Project A        Project B        Project C
              │                │                │
         private net       private net       private net
              │                │                │
      PostgreSQL/Qdrant  PostgreSQL/...   PostgreSQL/...
```

Главные правила:

```text
Reuse shared services.
Do not embed them into business projects.
Do not duplicate them without an explicit architecture decision.
Keep shared lifecycle independent.
Keep application state isolated.
Use clients/adapters, not local copies of shared runtimes.
Use stable logical contracts.
Keep physical model/GPU placement inside shared deployment configuration.
Verify runtime before changing infrastructure.
```
