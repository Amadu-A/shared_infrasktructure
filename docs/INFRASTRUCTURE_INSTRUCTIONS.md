<!-- docs/INFRASTRUCTURE_INSTRUCTIONS.md -->

# Infrastructure Development Instructions

Читать этот документ, когда задача затрагивает Docker/Compose, deployment, networks, GPU, host ports, shared infrastructure или operational scripts.

Если затронут shared service, дополнительно MUST прочитать `docs/services.yaml`.

Ключевые слова: **MUST** — обязательно; **SHOULD** — default; **MAY** — допустимо.

## 1. Основной принцип

```text
Share infrastructure.
Isolate application state.
Keep project lifecycle independent.
Use stable logical contracts.
Discover runtime before changing infrastructure.
```

Несколько projects на одном host MUST не конфликтовать по Compose project names, host ports, private networks и persistent data.

Shared infrastructure хранится отдельно от business projects.

## 2. Discovery before change

`services.yaml` описывает intended topology, но не доказывает runtime state.

Перед infrastructure change проверить relevant runtime:

```bash
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
docker network ls
sudo ss -lntp
nvidia-smi
```

Shared stack:

```bash
cd ~/projects/shared-infrastructure
docker compose ps
./scripts/check.sh
```

Если shell-access отсутствует — запросить output у пользователя.

Не проектировать infrastructure по памяти или старому Compose.

## 3. Shared vs project-specific

Shared по текущей architecture:

```text
shared-vlm
shared-embedding
Open WebUI
RabbitMQ
n8n
```

`n8n-db` — private dependency самого n8n.

Project-specific по умолчанию:

```text
backend/frontend;
application PostgreSQL;
application Qdrant;
application Redis;
Celery worker/beat;
project migrations/background jobs.
```

PostgreSQL/Qdrant/Redis MAY стать shared только по явному решению с logical isolation.

Business project MUST NOT владеть shared service, от которого независимо зависят другие projects.

## 4. Shared AI inference

Target runtime — vLLM.

Stable contracts:

```text
http://shared-vlm:8000/v1
model: shared-vlm

http://shared-embedding:8000
model: shared-embedding
```

Business application MUST NOT зависеть от:

```text
physical Hugging Face model ID/revision;
GPU index/model/count;
TP/DP layout;
VRAM size;
physical GPU topology.
```

Это deployment configuration shared infrastructure:

```text
MODEL_ID / MODEL_REVISION
GPU_DEVICES
TP_SIZE / DP_SIZE
MAX_MODEL_LEN
GPU_MEMORY_UTILIZATION
concurrency/backpressure limits
bind IP / host port
```

Один Compose SHOULD работать на разных GPU hosts через разные env values.

### GPU placement

Inference service получает GPU set из deployment configuration.

Для TP>1 оператор MUST учитывать фактическую GPU topology и benchmark.

Architecture MUST NOT запрещать использование одной physical GPU несколькими services только из-за совпадения индекса. Capacity/VRAM feasibility — responsibility deployment operator и runtime tests.

Silent CPU fallback для shared inference не допускается.

Production shared models SHOULD оставаться resident на протяжении lifecycle service. Request-level load/unload не является normal request path.

### Concurrency и queues

vLLM отвечает за short-lived inference scheduling/continuous batching/bounded admission.

Interactive request SHOULD идти напрямую к shared inference endpoint.

Durable/batch workload SHOULD использовать:

```text
RabbitMQ
-> bounded project worker
-> shared inference
```

### Embedding compatibility

Нельзя silently менять embedding model/vector identity.

При смене model/preprocessing/dimension/normalization project MUST проверить compatibility existing index и выполнить controlled reindex/migration, если vectors несовместимы.

### Open WebUI

Open WebUI — human-facing UI для manual chat/prompt/vision testing.

Он MUST NOT быть dependency business applications и не является source of truth для vLLM lifecycle.

## 5. Что не добавлять автоматически

Без отдельного решения MUST NOT добавляться:

```text
project-local vLLM, если подходит shared;
duplicate shared model;
Ray Serve;
Kubernetes/k3s;
GPU Operator;
service mesh;
request-level dynamic model load/unload;
gateway только "на будущее".
```

Gateway MAY появиться позже для quotas/trusted priorities/routing/audit, если появится реальная необходимость.

## 6. Docker access model

Same-host containers SHOULD использовать Docker DNS/internal ports через `ai-shared`.

Другой computer/server использует `SHARED_PUBLIC_HOST` и published port.

`SHARED_PUBLIC_HOST` — IP/DNS для clients. Это НЕ bind address.

Bind variables определяют local interface:

```text
SHARED_VLM_BIND_IP
SHARED_EMBEDDING_BIND_IP
OPEN_WEBUI_BIND_IP
N8N_BIND_IP
RABBITMQ_BIND_IP
```

Для trusted LAN/VPN `0.0.0.0` MAY использоваться как baseline, если host firewall ограничивает source networks.

Application-level API key — дополнительная защита, но не замена firewall.

Infrastructure-private dependencies SHOULD не иметь published host port.

## 7. Docker networks и ownership

Shared external network:

```text
ai-shared
```

Application service подключается к ней только если нужен shared dependency.

Project database SHOULD оставаться в private project network.

`container_name` SHOULD NOT использоваться без необходимости.

Каждый Compose stack MUST иметь уникальный project name.

Persistent volumes MUST быть изолированы по ownership.

## 8. Environment и secrets

`.env.example` — committed catalog variables/defaults/placeholders.

`.env` — private sparse override: real secrets + environment-specific deployment values.

`.env` MUST NOT попадать в Git.

Compose non-secret defaults SHOULD задаваться через `${VAR:-default}`. Required secrets SHOULD использовать `${VAR:?message}` или equivalent validation scripts.

После изменения container environment/config обычный `docker compose restart` не применяет большинство изменений — нужен recreate:

```bash
docker compose up -d --force-recreate
```

Не использовать `down -v` без осознанной необходимости.

## 9. Public URLs

Если shared service должен знать собственный public URL, он SHOULD вычисляться из `SHARED_PUBLIC_HOST` + соответствующего port/protocol.

Это особенно важно для callback/webhook/OAuth services, например n8n:

```text
N8N_HOST
N8N_EDITOR_BASE_URL
WEBHOOK_URL
```

Public URL и bind address не смешивать.

## 10. RabbitMQ и n8n

RabbitMQ — shared broker. Projects SHOULD использовать one vhost + one application user per project. Application SHOULD NOT использовать bootstrap admin account.

n8n — shared service. Workflow names SHOULD иметь project namespace (`[PDRD]`, `[CONTRACT]`, ...).

Private `n8n-db` принадлежит только n8n и MUST NOT использоваться application projects.

## 11. PostgreSQL / Qdrant / Redis

Application PostgreSQL SHOULD быть project-specific. Если shared — отдельные database/user/credentials и independent backup strategy.

Qdrant SHOULD быть project-specific. При shared Qdrant collections MUST иметь project namespace.

Redis MAY быть project-specific или shared по отдельному решению; shared usage требует namespace/ACL/instance isolation по риску данных.

## 12. Health, lifecycle и logging

Long-running services SHOULD иметь:

```text
healthcheck;
restart: unless-stopped;
bounded logs;
persistent volume там, где есть state.
```

Если start зависит от readiness dependency, SHOULD использовать health condition там, где поддерживается.

One-shot migrations/indexing/import jobs SHOULD быть отделены от ordinary startup и не должны бесконечно restart-иться.

Docker/container logs MUST иметь конечную rotation/retention policy.

## 13. Runtime operations

Перед запуском:

```bash
docker compose config --quiet
```

Запуск/проверка:

```bash
docker compose up -d
docker compose ps
./scripts/check.sh
```

Диагностика:

```bash
docker compose logs --tail=100 <service>
```

После изменения env/config:

```bash
docker compose up -d --force-recreate <service>
```

## 14. Shared endpoints

Same host / `ai-shared`:

```text
shared-vlm:       http://shared-vlm:8000/v1
shared-embedding: http://shared-embedding:8000
Open WebUI:       http://open-webui:8080
n8n:              http://n8n:5678
RabbitMQ:         rabbitmq:5672
```

Other trusted host:

```text
http://<SHARED_PUBLIC_HOST>:<published-port>
```

Точные ports/contracts брать из `services.yaml` и актуального `.env.example`.

## 15. Checklist / Definition of Done

```text
[ ] Прочитан актуальный Compose/config/scripts.
[ ] Проверен runtime state.
[ ] Проверен services.yaml, если затронут shared.
[ ] Ownership shared vs project-specific определён.
[ ] Host ports/networks/volumes не конфликтуют.
[ ] Secrets не попадут в Git.
[ ] .env.example отражает новые variables.
[ ] Healthcheck/lifecycle/log retention определены.
[ ] GPU parameters остаются env-driven.
[ ] Для TP проверена topology/benchmark.
[ ] Для embedding migration проверена vector compatibility.
[ ] docker compose config проходит.
[ ] Runtime реально проверен после запуска.
[ ] Есть recreate/migration/reindex/rollback instructions, если нужны.
```
