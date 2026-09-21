<!-- docs/INFRASTRUCTURE_INSTRUCTIONS.md -->

# Infrastructure Development Instructions

Этот документ объясняет LLM / developer, какие infrastructure services уже являются
общими, как ими пользоваться и что нельзя дублировать в business projects.

Если задача затрагивает shared service, MUST дополнительно прочитать:

```text
docs/services.yaml
```

Главные правила:

```text
Reuse shared services.
Do not embed shared runtimes into business projects.
Keep application state isolated.
Use stable logical contracts.
Verify runtime before infrastructure changes.
```

**MUST** — обязательно. **MUST NOT** — запрещено. **SHOULD** — default.
**MAY** — допустимо.

---

## 1. Ownership

`shared-infrastructure` — отдельный stack с независимым lifecycle.

Если service в `docs/services.yaml` имеет:

```yaml
managed_here: true
```

новый project MUST использовать его как external dependency и MUST NOT создавать
свою копию без отдельного архитектурного решения.

Project-specific по умолчанию:

```text
backend/frontend;
application PostgreSQL/Qdrant/Redis;
Celery worker/beat;
migrations;
background jobs.
```

Shared и project-specific state/lifecycle нельзя смешивать.

---

## 2. Текущие reusable shared services

Актуальный состав всегда брать из `docs/services.yaml`.

Сейчас shared:

```text
shared-vlm
shared-embedding
Open WebUI
RabbitMQ
n8n
```

`n8n-db` — private dependency n8n и MUST NOT использоваться как application database.

---

## 3. Anti-duplication rule

Новый business project MUST NOT:

```text
добавлять shared-vlm/vLLM в свой compose;
встраивать model runtime в Dockerfile;
поднимать duplicate shared embedding;
поднимать отдельный RabbitMQ без причины;
поднимать отдельный n8n без причины;
копировать shared volumes;
использовать shared admin credentials как application credentials.
```

Правильно:

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

Project MAY иметь только client/adaptor code:

```text
SharedVlmClient;
EmbeddingClient;
RabbitMQ publisher/consumer;
n8n API client;
ports/interfaces;
timeouts/retries;
health checks.
```

Project хранит client, а не shared runtime.

---

## 4. Discovery before change

`services.yaml` описывает intended topology, но не runtime fact.

Перед infrastructure change MUST:

1. прочитать актуальный `services.yaml`, если затронут shared;
2. прочитать актуальный `compose.yaml`;
3. проверить related config / `.env.example` / scripts;
4. проверить actual runtime;
5. только затем предлагать изменение.

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

GPU-related:

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

Если shell access отсутствует, LLM MUST запросить relevant output.

Не проектировать infrastructure по памяти, старому SHA или старому Compose.

---

## 5. Подключение project к shared

### Same Docker host

Использовать external network:

```text
ai-shared
```

Пример:

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

Project database/state SHOULD оставаться в private project network.

### Stable internal endpoints

```text
shared-vlm:       http://shared-vlm:8000/v1
shared-embedding: http://shared-embedding:8000/v1
Open WebUI:       http://open-webui:8080
n8n:              http://n8n:5678
RabbitMQ:         rabbitmq:5672
```

Container-to-container traffic SHOULD использовать Docker DNS/internal ports.

Для application configuration на том же Docker host SHOULD использовать stable
logical values:

```dotenv
SHARED_VLM_BASE_URL=http://shared-vlm:8000/v1
SHARED_VLM_MODEL=shared-vlm

SHARED_EMBEDDING_BASE_URL=http://shared-embedding:8000/v1
SHARED_EMBEDDING_MODEL=shared-embedding
```

Application MUST NOT подставлять physical Hugging Face model ID вместо
`shared-vlm` / `shared-embedding`.

### Другой host

Использовать:

```text
SHARED_PUBLIC_HOST + published port
```

При default published ports application endpoints имеют вид:

```text
VLM:       http://<SHARED_PUBLIC_HOST>:8000/v1
Embedding: http://<SHARED_PUBLIC_HOST>:8001/v1
```

Например для deployment с `SHARED_PUBLIC_HOST=192.168.55.3`:

```dotenv
SHARED_VLM_BASE_URL=http://192.168.55.3:8000/v1
SHARED_VLM_MODEL=shared-vlm

SHARED_EMBEDDING_BASE_URL=http://192.168.55.3:8001/v1
SHARED_EMBEDDING_MODEL=shared-embedding
```

Точные ports/contracts брать из:

```text
docs/services.yaml
.env.example
deployment .env
```

`shared-vlm` и `shared-embedding` в текущем contract не используют Bearer/API-key
authentication. Application MUST NOT требовать `SHARED_VLM_API_KEY` или
`SHARED_EMBEDDING_API_KEY`.

Published inference endpoints доступны только в пределах разрешённой
trusted LAN/VPN reachability и host firewall rules.

Если конкретный client SDK требует непустой `api_key` только из-за своего
constructor contract, MAY использовать non-secret literal:

```text
unused
```

Такое значение не является credential и не должно превращаться в shared secret.

### `localhost`

В container `localhost` / `127.0.0.1` означает текущий container, а не Docker host
и не другой service.

---

## 6. Shared AI inference

Target runtime:

```text
vLLM
```

Logical contracts:

```text
VLM:
http://shared-vlm:8000/v1
model: shared-vlm

Embedding:
http://shared-embedding:8000/v1
model: shared-embedding
```

Порядок доступа к shared models:

1. определить, находится client на том же Docker host или на другом approved host;
2. на том же host подключить только нужный application service к `ai-shared` и
   использовать Docker DNS;
3. на другом host использовать `SHARED_PUBLIC_HOST` и published port;
4. перед inference MAY проверить `/health` и `/v1/models`;
5. в inference request всегда использовать logical model alias;
6. не добавлять `Authorization: Bearer ...` для `shared-vlm` или `shared-embedding`;
7. не зависеть от physical model ID, revision или GPU layout.

Основные VLM endpoints:

```text
GET  /health
GET  /v1/models
POST /v1/chat/completions
GET  /metrics
```

На том же Docker host:

```text
http://shared-vlm:8000/health
http://shared-vlm:8000/v1/models
http://shared-vlm:8000/v1/chat/completions
```

Пример direct VLM request:

```bash
curl -sS \
  -H "Content-Type: application/json" \
  http://shared-vlm:8000/v1/chat/completions \
  -d '{
    "model": "shared-vlm",
    "messages": [
      {
        "role": "user",
        "content": "Ответь только одним словом: РАБОТАЕТ"
      }
    ],
    "max_tokens": 32
  }'
```

Основные embedding endpoints:

```text
GET  /health
GET  /v1/models
POST /v1/embeddings
POST /pooling
GET  /metrics
```

На том же Docker host:

```text
http://shared-embedding:8000/health
http://shared-embedding:8000/v1/models
http://shared-embedding:8000/v1/embeddings
http://shared-embedding:8000/pooling
```

Пример text embedding request:

```bash
curl -sS \
  -H "Content-Type: application/json" \
  http://shared-embedding:8000/v1/embeddings \
  -d '{
    "model": "shared-embedding",
    "input": "Проверка общей embedding модели"
  }'
```

Для clients на другом approved host те же paths используются через published
URLs:

```text
http://<SHARED_PUBLIC_HOST>:8000/...
http://<SHARED_PUBLIC_HOST>:8001/...
```

Business project MUST NOT зависеть от:

```text
physical model ID/revision;
GPU index/model/count;
TP/DP layout;
VRAM size;
GPU topology.
```

Это deployment configuration shared infrastructure.

Production models SHOULD оставаться resident в GPU VRAM.

Normal request path MUST NOT быть:

```text
load model -> inference -> unload model
```

Одна loaded model должна обслуживать requests нескольких projects/users.

vLLM отвечает за concurrency, continuous batching, scheduling и bounded backpressure.

Project MUST NOT запускать отдельный model process на пользователя/request.

---

## 7. Interactive и batch

Interactive:

```text
user/API -> shared inference
```

Background/batch:

```text
Project API
-> RabbitMQ
-> bounded project worker
-> shared inference
```

RabbitMQ нужен для durable jobs/retry/workload smoothing, а не для каждого
interactive model call.

---

## 8. Embedding compatibility

Embedding identity — часть data contract.

Нельзя silently менять:

```text
model/revision;
vector dimension;
preprocessing;
normalization;
multimodal processing semantics.
```

Application использует logical alias:

```text
shared-embedding
```

но при смене physical embedding model operator MUST отдельно проверить фактическую
vector dimension и compatibility существующих indexes.

Если существует vector index, смена embedding model/runtime требует compatibility check.

Если vectors несовместимы — выполнить controlled migration/reindex.

---

## 9. GPU и hardware portability

Business projects не должны знать physical GPU layout.

Shared deployment configuration определяет:

```text
MODEL_ID / MODEL_REVISION;
GPU_DEVICES;
TP_SIZE / DP_SIZE;
MAX_MODEL_LEN;
GPU_MEMORY_UTILIZATION;
concurrency / queue limits.
```

`MODEL_REVISION` задаёт revision Hugging Face repository, из которой vLLM
загружает weights/config/tokenizer.

`main` MAY использоваться для первичной оценки модели. Для принятой
production-like модели SHOULD использоваться exact Hugging Face commit SHA,
чтобы recreate/rollback был воспроизводимым.

При смене physical VLM model MUST сохраняться logical application contract:

```text
base URL: http://shared-vlm:8000/v1
model:    shared-vlm
```

Утверждённый operational order смены physical VLM model:

1. проверить compatibility модели с текущим vLLM, modality, VRAM и GPU topology;
2. предварительно скачать выбранный `MODEL_ID` + `MODEL_REVISION` в shared
   Hugging Face cache;
3. проверить наличие snapshot и свободное disk space;
4. изменить deployment `.env`: model ID/revision и при необходимости
   GPU/TP/DP/context;
5. выполнить `docker compose config --quiet` и `./scripts/bootstrap.sh`;
6. только после успешной validation выполнить force-recreate `shared-vlm`;
7. дождаться `healthy`, проверить logs и `nvidia-smi`;
8. выполнить `/v1/models`, functional smoke test и `./scripts/check.sh`;
9. при failure вернуть предыдущие deployment values и выполнить recreate;
10. не менять application `SHARED_VLM_MODEL=shared-vlm`.

Pre-download SHOULD выполняться до остановки текущего VLM и использовать shared
Hugging Face persistent cache. Пример:

```bash
docker compose \
  --profile ai-vlm \
  run --rm \
  --no-deps \
  --entrypoint python3 \
  shared-vlm \
  -c 'from huggingface_hub import snapshot_download; snapshot_download("<MODEL_ID>", revision="<MODEL_REVISION>")'
```

Проверка snapshot:

```bash
docker compose \
  --profile ai-vlm \
  run --rm \
  --no-deps \
  --entrypoint sh \
  shared-vlm \
  -c 'du -sh /root/.cache/huggingface/hub/models--<ORG>--<MODEL>'
```

После изменения `.env`:

```bash
docker compose \
  --profile ai-vlm \
  up -d \
  --force-recreate \
  shared-vlm
```

Для `TP_SIZE > 1` MUST учитывать actual GPU topology и benchmark.

Если модель помещается на одной GPU, `TP=1` SHOULD быть baseline.

Выбор `single GPU / TP / DP` делается по измерениям.

Несколько services MAY использовать одну physical GPU только после проверки:

```text
VRAM feasibility;
отсутствия OOM;
runtime stability;
acceptable latency/throughput.
```

Silent CPU fallback не допускается.

---

## 10. RabbitMQ

RabbitMQ broker — shared.

Endpoint:

```text
rabbitmq:5672
```

Project-specific:

```text
vhost;
application user;
exchange/queue/routing key;
consumer/worker code.
```

Предпочтительно:

```text
one vhost per project
one application user per project
```

Application MUST NOT использовать bootstrap/admin account как runtime account.

Celery worker остаётся project-specific.

---

## 11. n8n

n8n — shared automation/orchestration service.

Endpoint:

```text
http://n8n:5678
```

Project MAY создавать workflows/webhooks/project-specific credentials.

Workflow names SHOULD иметь namespace:

```text
[PDRD] ...
[CONTRACT] ...
[PROJECT-X] ...
```

Project MUST NOT автоматически поднимать отдельный n8n.

Отдельный n8n MAY появиться только при явной причине: security/tenant isolation,
несовместимая version или независимый lifecycle.

---

## 12. Open WebUI

Open WebUI — shared human UI для manual chat, prompt/vision testing и проверки models.

Он MUST NOT быть dependency business application и не управляет model lifecycle.

---

## 13. Project-specific databases

Application PostgreSQL и Qdrant SHOULD быть project-specific по умолчанию.

`n8n-db` MUST NOT использоваться как application database.

Если PostgreSQL/Qdrant/Redis становятся shared, MUST отдельно определить:

```text
isolation;
namespace/users/ACL;
credentials;
backup/restore;
ownership;
migration lifecycle.
```

---

## 14. Lifecycle

Остановка Project A не должна останавливать shared services или Project B.

Business project MUST NOT самостоятельно restart/recreate shared service.

Project SHOULD корректно переживать временную недоступность shared dependency через:

```text
timeout;
bounded retry;
backpressure;
graceful error handling;
readiness semantics.
```

---

## 15. Ports, networks, volumes

Container-to-container communication не требует host port.

Host port нужен только для доступа:

```text
из LAN/VPN;
с другого host;
через UI/admin;
для webhook/callback.
```

`ai-shared` используется только для approved shared dependencies.

Project state SHOULD оставаться в private network.

Каждый Compose stack MUST иметь уникальный project name.

`container_name` SHOULD NOT использоваться без необходимости.

Persistent volume MUST иметь понятного owner.

---

## 16. Environment и secrets

`.env.example`:

```text
committed catalog;
safe defaults;
secret placeholders.
```

`.env`:

```text
private;
sparse;
real secrets;
environment-specific overrides.
```

`.env` MUST NOT попадать в Git.

Текущий inference contract не использует:

```text
SHARED_VLM_API_KEY
SHARED_EMBEDDING_API_KEY
```

Business projects MUST NOT добавлять эти variables как обязательные credentials.
Безопасность published inference endpoints обеспечивается network perimeter,
trusted LAN/VPN и host firewall.

`HF_TOKEN`, если он нужен для gated/private Hugging Face repository, является
deployment secret shared infrastructure и MUST NOT передаваться business projects.

После environment/config change нужен recreate:

```bash
docker compose up -d --force-recreate <service>
```

Не использовать `docker compose down -v` без понимания последствий.

---

## 17. Security

`SHARED_PUBLIC_HOST` — IP/DNS для approved clients на других hosts.

Это НЕ bind address.

`shared-vlm` и `shared-embedding` не требуют application API key/Bearer token.

Published inference endpoints защищаются trusted LAN/VPN и host firewall.

Host firewall MUST ограничивать published inference ports только approved source
networks/hosts. Отсутствие application API key не означает public Internet access.

Infrastructure-private dependencies SHOULD не публиковаться наружу.

Secrets MUST NOT попадать в Git, logs или diagnostics.

---

## 18. Когда duplicate service допустим

Duplicate shared service — исключение.

Отдельный instance MAY быть создан только при документированной причине:

```text
несовместимая major version;
tenant/security isolation;
regulatory boundary;
отдельный maintenance window;
experimental sandbox;
shared service не предоставляет нужный contract;
нагрузка требует отдельного deployment.
```

Перед этим MUST:

1. проверить возможность использовать/расширить existing shared service;
2. описать причину;
3. определить owner/lifecycle;
4. определить network/ports/volumes/secrets;
5. получить явное архитектурное решение.

LLM MUST NOT создавать duplicate service "на всякий случай".

---

## 19. Новый shared service

Service НЕ становится shared только потому, что "может пригодиться".

Перед добавлением MUST определить:

```text
owner;
real consumers;
stable logical contract;
DNS/network/ports;
authentication;
volumes;
healthcheck;
backup/restore;
upgrade/rollback.
```

После принятия решения обновить связанные:

```text
compose/config;
.env.example;
docs/services.yaml;
relevant scripts/docs.
```

---

## 20. Алгоритм LLM для нового project

LLM MUST:

1. прочитать актуальный `services.yaml`;
2. определить required dependencies;
3. проверить `managed_here`;
4. если `managed_here: true`:
   - НЕ добавлять server container;
   - использовать stable endpoint;
   - добавить только client/adaptor/config;
5. если service отсутствует — определить, project-specific он или кандидат в shared;
6. подключить только нужные services к `ai-shared`;
7. оставить project state в private network;
8. не публиковать host ports без необходимости;
9. определить secrets/volumes;
10. добавить health/retry semantics;
11. проверить runtime.

Для `shared-vlm` / `shared-embedding` новый project MUST сначала определить
location client:

```text
same Docker host -> ai-shared + Docker DNS/internal port;
other approved host -> SHARED_PUBLIC_HOST + published port.
```

После этого project использует logical model alias и не вводит inference API-key
credentials.

---

## 21. Stop conditions

LLM MUST остановиться и потребовать явное архитектурное решение, если собирается:

```text
добавить второй vLLM/model server;
добавить duplicate shared embedding;
добавить отдельный RabbitMQ;
добавить отдельный n8n;
сделать PostgreSQL/Qdrant shared;
перенести project worker в shared;
hardcode GPU/model ID в business code;
использовать shared admin credentials;
подключить project DB к ai-shared без причины;
опубликовать private/stateful service наружу;
создать duplicate service "для удобства".
```

Без отдельного решения также MUST NOT добавляться:

```text
Ray Serve;
Kubernetes/k3s;
GPU Operator;
service mesh;
request-level dynamic model load/unload;
gateway "на будущее".
```

---

## 22. Runtime operations

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

Shared check:

```bash
./scripts/check.sh
```

Диагностика:

```bash
docker compose logs --tail=100 <service>
```

---

## 23. Definition of Done

```text
[ ] Актуальные compose/config/scripts прочитаны.
[ ] services.yaml прочитан, если затронут shared.
[ ] Runtime проверен.
[ ] Для dependency определён owner.
[ ] Existing shared services переиспользованы.
[ ] Shared runtime не встроен в business project.
[ ] Нет duplicate vLLM/RabbitMQ/n8n/shared embedding.
[ ] Project содержит clients/adapters, а не shared runtimes.
[ ] Используются stable logical endpoints.
[ ] Project state остаётся private.
[ ] Secrets отсутствуют в Git/logs.
[ ] GPU parameters остаются deployment config.
[ ] Для TP>1 проверена topology/benchmark.
[ ] Для embedding migration проверена compatibility.
[ ] docker compose config проходит.
[ ] Runtime проверен после запуска.
[ ] Rollback/migration понятен.
```

---

## 24. Итог

```text
Reuse shared services.
Do not embed shared runtimes into business projects.
Do not duplicate them without explicit architecture decision.
Use clients/adapters instead of local copies.
Keep shared lifecycle independent.
Keep project state isolated.
Use stable logical contracts.
Keep physical model/GPU placement in shared deployment configuration.
Verify runtime before changing infrastructure.
```


## 25. Infrastructure Definition of Done

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

## 26. Итоговая модель

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
