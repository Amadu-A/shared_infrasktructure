<!-- docs/AI_INFERENCE_ARCHITECTURE.md -->

# Shared AI Inference Architecture

> **Назначение:** source of truth для общей AI inference infrastructure,
> используемой несколькими независимыми проектами на одном GPU host.
>
> Документ определяет:
>
> - границы shared AI infrastructure;
> - роль vLLM, Ollama и Open WebUI;
> - GPU topology и hardware profiles;
> - правила model residency;
> - Tensor Parallel / Data Parallel;
> - shared model endpoints;
> - concurrency и backpressure;
> - batch workloads;
> - model storage;
> - observability;
> - benchmarking;
> - migration и rollback;
> - правила подключения application projects.
>
> Ключевые слова:
>
> - **MUST** — обязательное правило;
> - **MUST NOT** — запрещённое действие;
> - **SHOULD** — рекомендуемое решение по умолчанию;
> - **MAY** — допустимый вариант.

---

# 1. Основной принцип

Shared AI infrastructure обслуживает несколько независимых проектов и пользователей.

Нельзя проектировать GPU runtime исходя из модели:

```text
one project
    =
one model process
    =
one GPU
```

Целевая схема:

```text
                      USERS / SERVICES
                             │
          ┌──────────────────┼───────────────────┐
          │                  │                   │
        PDRD                n8n            Other Projects
          │                  │                   │
          └──────────────────┼───────────────────┘
                             │
                        ai-shared
                             │
             ┌───────────────┴────────────────┐
             │                                │
             ▼                                ▼
        shared-vlm                    shared-embedding
             │                                │
            vLLM                             vLLM
             │                                │
       resident model                  resident model
```

Несколько проектов MUST использовать общие model services, если для их изоляции нет документированной причины.

Business project MUST NOT поднимать собственную копию общей модели только потому, что ему нужен inference.

---

# 2. Shared AI services

Целевая shared infrastructure содержит логические AI-сервисы:

```text
shared-vlm
shared-embedding
```

Дополнительно MAY существовать:

```text
open-webui
shared-reranker
inference-gateway
monitoring stack
```

Они не должны менять базовый contract между application и inference runtime.

---

# 3. Target inference runtime

Основной target runtime:

```text
vLLM
```

Причины использования:

```text
concurrent inference;
continuous batching;
OpenAI-compatible API;
Tensor Parallel;
Data Parallel;
GPU memory management;
production-oriented scheduling;
metrics.
```

Ollama является transitional runtime.

Он MUST NOT удаляться до завершения миграции существующих consumers.

Целевой lifecycle:

```text
CURRENT

projects
    ↓
Ollama


TRANSITION

projects ──────────────→ Ollama
     │
     └───────────────→ vLLM


TARGET

projects
    ↓
vLLM
```

---

# 4. Ray Serve

Ray Serve MUST NOT быть обязательной частью базовой architecture.

На одном physical GPU host с небольшим количеством заранее определённых resident models native возможности vLLM предпочтительнее дополнительного distributed scheduling layer.

Ray MAY рассматриваться позже, если появляются подтверждённые требования:

```text
несколько physical GPU hosts;
dynamic autoscaling;
dynamic replica placement;
сложный cluster scheduling;
cross-host resource allocation.
```

Отсутствие Ray является осознанным архитектурным решением.

---

# 5. Kubernetes

Kubernetes, k3s, NVIDIA GPU Operator и service mesh сейчас MUST NOT использоваться как обязательный orchestration layer.

Текущий target:

```text
Docker Engine
Docker Compose
NVIDIA Container Toolkit / CDI
```

Причина:

```text
один physical GPU host;
небольшое фиксированное число shared models;
понятная GPU topology;
отсутствие cluster-level HA.
```

Architecture должна позволять будущую миграцию в cluster environment, но не должна платить complexity cost заранее.

---

# 6. Hardware portability

`shared-infrastructure` MUST NOT быть архитектурно привязан к:

```text
конкретной модели GPU;
фиксированному количеству GPU;
фиксированному tensor parallel size;
фиксированному data parallel size;
конкретному GPU index;
конкретному physical model ID.
```

Один repository должен поддерживать как минимум:

```text
single-GPU development host

и

multi-GPU production host.
```

Logical service names и API contracts MUST оставаться одинаковыми.

Hardware-specific значения относятся к deployment configuration.

---

# 7. Environment и hardware profile — разные понятия

Нельзя считать:

```text
dev = RTX 3090
prod = A100
```

как единое понятие.

Нужно различать:

```text
environment

dev
stage
prod
```

и:

```text
hardware profile

single-gpu-24gb
a100x4-80gb
```

Например в будущем допустимо:

```text
development:
single-gpu-24gb

staging:
a100-single-80gb

production:
a100x4-80gb
```

Application contract при этом остаётся одинаковым.

---

# 8. Текущие hardware profiles

## 8.1. Development server

Hostname:

```text
aiagent
```

Hardware:

```text
CPU:
Intel Core i5-12400
6 cores / 12 threads

RAM:
около 30 GiB

GPU:
NVIDIA GeForce RTX 3090
24 GiB VRAM

GPU count:
1

NUMA:
1 node
```

Назначение:

```text
development;
integration;
functional staging;
API compatibility tests;
Compose tests;
model integration tests;
small-scale inference tests.
```

Этот server MUST NOT считаться performance equivalent production server.

## 8.2. Production server

Hostname:

```text
aistation
```

Hardware:

```text
CPU:
AMD EPYC 9374F
32 cores / 64 threads

RAM:
около 436 GiB

GPU:
4 x NVIDIA A100-SXM4-80GB

VRAM:
81920 MiB per GPU

NUMA:
1 node
```

GPU topology:

```text
GPU0 <========== NV12 ==========> GPU1

GPU2 <========== NV12 ==========> GPU3
```

Cross-pair topology:

```text
NODE
```

P2P read поддерживается между всеми четырьмя GPU.

---

# 9. GPU topology domains

Production GPU topology разделяется на два strong topology domains.

```text
DOMAIN A

GPU0
GPU1
```

```text
DOMAIN B

GPU2
GPU3
```

Если workload использует `Tensor Parallel > 1`, GPU MUST выбираться внутри одного topology domain.

Разрешённые предпочтительные TP=2 combinations:

```text
GPU0 + GPU1

или

GPU2 + GPU3
```

Cross-domain TP:

```text
GPU0 + GPU2
GPU0 + GPU3
GPU1 + GPU2
GPU1 + GPU3
```

MUST NOT использоваться без отдельной технической причины и benchmark.

---

# 10. Topology domain не является обязательной allocation unit

Наличие NVLink pair НЕ означает:

```text
one model
    =
two GPU.
```

Если модель полностью помещается на одной GPU, single-GPU deployment MUST рассматриваться как основной benchmark candidate.

Допустимо:

```text
GPU2
    ↓
shared-embedding

GPU3
    ↓
free / reserve / another service
```

Topology domain определяет предпочтительное размещение multi-GPU workload, но не требует использовать все GPU domain одновременно.

---

# 11. Single-GPU baseline

Для новой модели первым baseline SHOULD быть:

```text
TP=1
DP=1
```

если модель помещается в память одной GPU.

Single-GPU inference:

```text
не требует меж-GPU communication;
проще диагностируется;
имеет меньшую operational complexity;
оставляет другие GPU независимым workloads.
```

---

# 12. Tensor Parallel

Tensor Parallel используется, когда одна inference replica распределяется между несколькими GPU.

Пример:

```text
GPU0 ─┐
      ├── shared-vlm replica
GPU1 ─┘

TP=2
```

TP SHOULD использоваться, если benchmark показывает преимущество по:

```text
single-request latency;
доступному KV cache;
long-context capacity;
memory fit;
production SLO.
```

TP MUST NOT использоваться только потому, что рядом имеется свободная GPU.

---

# 13. Data Parallel

Data Parallel используется для нескольких independent model replicas.

```text
             one logical endpoint
                     │
          ┌──────────┴──────────┐
          │                     │
      replica 1             replica 2
          │                     │
        GPU0                  GPU1
```

Для модели, помещающейся на одной GPU, DP MAY давать больший aggregate throughput при высокой concurrency, чем TP.

Production mode MUST определяться benchmark.

---

# 14. Обязательный benchmark TP / DP

Для каждой основной generation/VLM model нужно сравнить как минимум:

```text
MODE 1
TP=1
DP=1
one GPU

MODE 2
TP=2
DP=1
one NVLink pair

MODE 3
TP=1
DP=2
two independent replicas
```

Минимальные показатели:

```text
single request latency;
TTFT;
inter-token latency;
output tokens/sec;
aggregate tokens/sec;
requests/sec;
concurrent throughput;
p50 latency;
p95 latency;
GPU utilization;
VRAM usage;
KV cache usage;
running requests;
waiting requests;
errors;
OOM;
preemptions, если runtime их сообщает.
```

---

# 15. Concurrency benchmark

Concurrency SHOULD повышаться ступенчато:

```text
1
2
4
8
16
32
```

Benchmark MAY остановиться раньше, если latency или error rate уже выходят за допустимые пределы.

---

# 16. Real workload benchmark

Synthetic prompts недостаточны.

Generation/VLM benchmark MUST включать реальные workload profiles:

```text
короткий interactive chat;
длинный chat / coding prompt;
PDRD real multimodal page;
PDRD structured analysis;
image input;
long structured output.
```

Benchmark result должен хранить:

```text
model revision;
vLLM version;
GPU profile;
TP;
DP;
context limit;
gpu-memory-utilization;
request concurrency;
input token distribution;
output token distribution.
```

---

# 17. Model residency

На production основные shared models SHOULD оставаться resident в GPU VRAM.

Target lifecycle:

```text
service start
    ↓
load model once
    ↓
model remains resident
    ↓
many requests
```

Базовая production architecture MUST NOT использовать request-level load/unload как обычный lifecycle.

---

# 18. Development residency

Development host MAY использовать другой residency policy.

На single RTX 3090 допустимо:

```text
shared-vlm running
shared-embedding stopped
```

а при работе с embedding:

```text
shared-vlm stopped
shared-embedding running
```

Application API contract при этом MUST оставаться таким же, как на production.

---

# 19. Dev/prod runtime parity

Development и production SHOULD использовать один inference runtime:

```text
vLLM
```

Нежелательная постоянная схема:

```text
DEV: Ollama
PROD: vLLM
```

Target:

```text
DEV: vLLM
PROD: vLLM
```

---

# 20. Development models

Development MAY использовать меньшую physical model, если production model не помещается на development GPU.

Logical name при этом остаётся тем же, например `shared-vlm`.

Model-dependent quality MUST проверяться на production model до production release.

---

# 21. Logical model aliases

Application projects SHOULD знать logical model names:

```text
shared-vlm
shared-embedding
```

Application SHOULD NOT зависеть непосредственно от HuggingFace repository ID, revision, GPU number, replica number, TP или DP configuration.

Концептуальный application configuration:

```text
SHARED_VLM_BASE_URL=http://shared-vlm:8000/v1
SHARED_VLM_MODEL=shared-vlm

SHARED_EMBEDDING_BASE_URL=http://shared-embedding:8000/v1
SHARED_EMBEDDING_MODEL=shared-embedding
```

---

# 22. Physical model identity

Shared infrastructure MUST отдельно хранить logical alias, physical model ID, model revision и runtime version.

Production MUST NOT зависеть от плавающего model revision без явного решения.

---

# 23. Shared VLM

Основная VLM обслуживает PDRD, Open WebUI, n8n, другие Python backends, другие проекты, interactive clients и background workers.

Не создавать project-local duplicate той же physical model без причины.

---

# 24. VLM model candidates

Текущий compatibility baseline:

```text
Qwen3-VL 8B family
```

Production benchmark candidate:

```text
Qwen/Qwen3.8-27B
```

Окончательная production model выбирается только после regression и benchmark.

---

# 25. Shared embedding

Целевой multimodal embedding candidate:

```text
Qwen/Qwen3-VL-Embedding-8B
```

Target dimension:

```text
4096
```

Начальный deployment candidate:

```text
TP=1
DP=1
one GPU
```

---

# 26. PDRD embedding compatibility

PDRD сейчас имеет собственный `multimodal-embedding-service` и использует `Qwen/Qwen3-VL-Embedding-8B`, dimension 4096.

Legacy PDRD service MUST сохраняться, пока shared vLLM embedding не прошёл controlled comparison.

---

# 27. Embedding benchmark

Сравнить current PDRD runtime и target vLLM по:

```text
text;
image;
text + image;
items/sec;
batch throughput;
p50/p95 latency;
GPU utilization;
VRAM usage;
cosine similarity distribution;
Recall@K;
MRR;
real PDRD retrieval queries.
```

---

# 28. Qdrant migration

Embedding provider MUST NOT переключаться silently.

При несовместимости vectors выполняется explicit migration с новым index, reindex, regression, alias switch и rollback path.

---

# 29. Legacy text embedding

Existing consumers MAY зависеть от другой embedding identity.

Во время transition MAY существовать:

```text
shared-embedding-legacy
shared-embedding
```

---

# 30. Request model

Один resident model service должен одновременно обслуживать много users/projects.

Нельзя использовать:

```text
one user = one model instance
```

Concurrency и batching являются ответственностью inference runtime.

---

# 31. Backpressure

Shared inference MUST иметь bounded admission.

При overload запрос должен ожидать, получить controlled rejection или быть направлен на другую replica.

Нельзя допускать unbounded backlog, CUDA OOM, silent CPU fallback или uncontrolled replica creation.

---

# 32. Inference queue и durable queue

vLLM inference queue предназначена для short-lived inference scheduling/admission.

RabbitMQ — для durable background work, batch jobs, indexing и retry.

Один механизм не заменяет другой.

---

# 33. Interactive workload

Interactive requests SHOULD обращаться к inference API напрямую.

Не добавлять RabbitMQ между browser/user-facing backend и model без отдельной причины.

---

# 34. Batch workload

Batch workload SHOULD использовать:

```text
project
    ↓
RabbitMQ
    ↓
bounded worker pool
    ↓
shared inference endpoint
```

Worker concurrency MUST ограничивать давление на shared inference.

---

# 35. Priority classes

Conceptual classes:

```text
INTERACTIVE
NORMAL
BATCH
```

Сложный priority gateway MUST NOT создаваться заранее.

---

# 36. Inference gateway

Inference gateway является optional component.

Он добавляется при необходимости per-project API keys, quotas, rate limits, priority enforcement, advanced routing, audit или per-project usage metrics.

Gateway SHOULD быть lightweight, stateless и replaceable.

---

# 37. Open WebUI

Open WebUI является shared human-facing interface.

Назначение:

```text
ручное общение с shared models;
model selection;
manual functional testing;
vision testing;
prompt testing;
developer access;
conversation UI.
```

Open WebUI MUST NOT являться обязательной dependency business applications.

---

# 38. Open WebUI и model lifecycle

Open WebUI MUST NOT быть source of truth для vLLM model residency.

Целевой vLLM model lifecycle управляется Docker Compose, administrative scripts и deployment configuration.

---

# 39. VRAM administration

Нужно предусмотреть administrative operations:

```text
inference start;
inference stop;
inference restart;
inference status;
GPU status;
model cache list;
model cache size;
model download;
model delete.
```

Administrative commands MUST быть отделены от normal application API.

---

# 40. Model storage

HuggingFace models MUST использовать общий persistent cache.

Shared storage должен переживать restart container, поддерживать pre-download, list, disk usage, delete и pinned revision.

---

# 41. Host storage

Точный host path выбирается после проверки storage layout.

Model cache MUST NOT находиться внутри ephemeral container filesystem.

---

# 42. Security boundaries

Inference workers SHOULD по умолчанию быть доступны через Docker network, а не напрямую из LAN.

Business applications внутри Docker используют `ai-shared` и Docker DNS.

---

# 43. LAN-facing services

Допускается LAN exposure для human/admin services, например Open WebUI, n8n editor и RabbitMQ Management при необходимости.

LAN services MUST быть защищены firewall, authentication и approved source networks.

---

# 44. Production network

Текущий production LAN:

```text
192.168.55.0/24
```

Production server:

```text
192.168.55.167
```

Generic Compose defaults SHOULD оставаться безопасными (`127.0.0.1`), LAN binding выполняется environment-specific override.

---

# 45. Open WebUI external access

Open WebUI MAY публиковаться в trusted LAN.

Он MUST иметь configurable bind address и host port и не должен требовать публикации raw vLLM worker port в LAN.

---

# 46. Context является shared resource

Большой model context увеличивает prefill time, KV cache usage, VRAM pressure и TTFT.

Поддерживаемый моделью maximum context MUST NOT автоматически становиться production service limit.

Context cap выбирается benchmark.

---

# 47. Noisy neighbor protection

Минимальные protection mechanisms:

```text
bounded request queue;
context limit;
max concurrent sequences;
batch worker concurrency;
request timeout;
output token limit.
```

---

# 48. CPU fallback

Production inference MUST NOT silently переключаться на CPU при нехватке GPU resources.

---

# 49. GPU pinning

Production GPU inference container MUST иметь явный GPU placement.

Нежелательно использовать `gpus: all` для каждого inference container.

---

# 50. Production GPU layout

Final production GPU layout пока НЕ фиксируется.

Обязательные candidates:

```text
VLM TP1;
VLM TP2;
VLM DP2;

Embedding TP1;
Embedding DP2;
Embedding TP2.
```

---

# 51. Предварительная benchmark hypothesis

Это НЕ final configuration.

Рабочая гипотеза:

```text
GPU0: VLM replica 1
GPU1: VLM replica 2
GPU2: multimodal embedding
GPU3: reserve
```

---

# 52. Reserve GPU

Свободная production GPU является допустимым и полезным состоянием.

Reserve MAY использоваться для canary, A/B benchmark, second embedding replica, third VLM replica, reranker, new model evaluation или emergency capacity.

---

# 53. Observability

Inference service MUST предоставлять metrics для running/waiting requests, TTFT, latency, inter-token latency, token counts, tokens/sec, KV cache utilization и errors.

GPU monitoring SHOULD включать GPU utilization, VRAM, temperature, power и ECC status где поддерживается.

---

# 54. Monitoring stack

Prometheus/Grafana MAY быть добавлены как shared monitoring services.

Они не являются обязательным условием первого vLLM deployment, но inference metrics MUST быть доступны.

---

# 55. Current unmanaged experiments

Experimental containers, поднятые разработчиками вручную, не являются частью canonical shared architecture.

После появления canonical deployment они SHOULD быть остановлены и удалены.

---

# 56. Model upgrade policy

Shared model upgrade должен иметь pinned revision, canary, regression tests, benchmark и rollback.

---

# 57. Runtime upgrade policy

vLLM image MUST быть pinned.

Production MUST NOT использовать `vllm/vllm-openai:latest` как постоянный deployment contract.

---

# 58. Open WebUI upgrade policy

Open WebUI image SHOULD быть pinned.

Его upgrade не должен останавливать business inference clients.

---

# 59. Rollback principle

Каждый migration step MUST иметь простой rollback без уничтожения persistent data.

---

# 60. Ollama migration

Ollama сохраняется до полной миграции consumers:

```text
1. оставить Ollama production-capable;
2. поднять vLLM parallel;
3. провести tests;
4. переключить один consumer;
5. observation;
6. переключить следующие consumers;
7. убедиться, что Ollama больше никто не использует;
8. stop Ollama;
9. observation;
10. удалить Ollama отдельным подтверждённым этапом.
```

---

# 61. PDRD migration

PDRD migration делится на две независимые операции:

```text
VLM migration
embedding migration
```

Embedding migration требует более строгого retrieval regression.

---

# 62. Development workflow

```text
developer changes shared-infrastructure
        ↓
deploy to aiagent
        ↓
integration / functional tests
        ↓
git commit / push
        ↓
deploy same architecture to aistation
        ↓
production smoke
        ↓
hardware/performance acceptance
```

---

# 63. Что проверяется на RTX 3090

Development server используется для Compose validation, container startup, networking, OpenAI-compatible contract, Open WebUI/RabbitMQ/n8n integration, healthchecks, scripts, logging, metrics, retry/backpressure и basic model functionality.

---

# 64. Что нельзя доказать RTX 3090

RTX 3090 MUST NOT использоваться как доказательство production performance для 4-GPU placement, TP2/DP2 on A100, NCCL topology, production KV cache, production p95 или A100-specific behavior.

---

# 65. Production acceptance

Перед final production deployment проверяются GPU visibility/placement, model revision, vLLM version, health, API, concurrency, VRAM/KV, queue, p50/p95, TTFT, throughput, OOM absence и process placement.

---

# 66. Interconnect benchmark

До окончательного TP configuration SHOULD быть выполнен NCCL и/или CUDA P2P benchmark для:

```text
GPU0 <-> GPU1
GPU2 <-> GPU3
GPU0 <-> GPU2
```

---

# 67. Application integration contract

Application configuration:

```text
SHARED_VLM_BASE_URL
SHARED_VLM_MODEL
SHARED_EMBEDDING_BASE_URL
SHARED_EMBEDDING_MODEL
SHARED_EMBEDDING_DIMENSION
```

Hardcoded host IP или GPU number в application code запрещён.

---

# 68. Docker communication

Application container в `ai-shared` использует Docker DNS:

```text
http://shared-vlm:<internal-port>/v1
http://shared-embedding:<internal-port>/v1
```

---

# 69. Project ownership

Shared inference runtime принадлежит `shared-infrastructure`, а не отдельному business project.

---

# 70. Запрещённые patterns

MUST NOT использоваться без отдельного архитектурного решения:

```text
one model server per project;
one model instance per user;
gpus: all для всех inference services;
динамический load/unload на каждый request;
silent CPU fallback;
cross-domain TP без benchmark;
unbounded inference queue;
hardcoded GPU indexes в business application;
hardcoded HuggingFace model ID в business logic;
silent embedding migration;
unversioned model replacement;
latest runtime tag в production;
Open WebUI как обязательная business dependency;
manual unmanaged production inference containers;
Ray Serve без подтверждённой необходимости;
Kubernetes на единственном host без отдельной причины.
```

---

# 71. Минимальная целевая architecture

```text
                       USERS / SERVICES
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
       PDRD                  n8n             Other Projects
          │                   │                   │
          └───────────────────┼───────────────────┘
                              │
                          ai-shared
                              │
              ┌───────────────┴───────────────┐
              │                               │
              ▼                               ▼
         shared-vlm                   shared-embedding
              │                               │
            vLLM                            vLLM
              │                               │
       resident model                  resident model
```

Optional human interface:

```text
Developer
    ↓
Open WebUI
    ↓
shared-vlm
```

Batch:

```text
Project
    ↓
RabbitMQ
    ↓
bounded worker
    ↓
shared inference
```

---

# 72. Architecture evolution

Сначала реализуется минимальный вариант:

```text
Docker Compose;
vLLM;
shared VLM;
shared embedding;
RabbitMQ;
n8n;
Open WebUI;
persistent model storage;
basic metrics.
```

Только после измерений MAY добавляться multiple replicas, priority gateway, shared reranker, canonical Prometheus/Grafana stack, Ray, multi-host deployment или Kubernetes.

---

# 73. Главное правило

```text
One shared inference architecture.
Stable logical endpoints.
Hardware-specific deployment profiles.
Resident production models.
Topology-aware multi-GPU placement.
Measure before choosing TP or DP.
Queue batch workloads.
Protect interactive workloads.
Keep business projects independent from physical GPU layout.
Test on development hardware.
Accept performance on production hardware.
```
