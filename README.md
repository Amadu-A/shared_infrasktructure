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
├── shared-vlm (vLLM, profile ai-vlm)
├── shared-embedding (vLLM, profile ai-embedding)
├── Open WebUI (profile ai-ui)
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
│   ├── FRONTEND_GUIDELINES.md
│   ├── INFRASTRUCTURE_INSTRUCTIONS.md
│   └── services.yaml
└── scripts/
    ├── bootstrap.sh
    └── check.sh
```

---

## Portable `docs/` bundle

Папка:

```text
docs/
```

специально предназначена для передачи LLM при начале нового проекта.

Программист может показать LLM только эти пять файлов:

```text
LLM_CONTEXT.md
ENGINEERING_GUIDELINES.md
FRONTEND_GUIDELINES.md
INFRASTRUCTURE_INSTRUCTIONS.md
services.yaml
```

Главная точка входа:

```text
docs/LLM_CONTEXT.md
```

LLM не обязана читать `README.md`, `PROJECT_INTEGRATION.md` или scripts этого
repository для проектирования нового application project.

Если проект содержит frontend или задача затрагивает HTML/CSS/JS/templates,
`docs/FRONTEND_GUIDELINES.md` является обязательным frontend source of truth и
MUST быть прочитан полностью.

---

## 1. Требования

На host:

```bash
docker --version
docker compose version
```

Для GPU AI-services:

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

Для базового shared stack обычно достаточно:

```dotenv
SHARED_PUBLIC_HOST=<server-ip-or-dns>
N8N_DB_PASSWORD=<strong-password>
N8N_ENCRYPTION_KEY=<generated-key>
RABBITMQ_DEFAULT_PASS=<strong-password>
```

При включении AI profiles также нужны соответствующие secrets, например:

```dotenv
SHARED_EMBEDDING_API_KEY=<strong-api-key>
OPEN_WEBUI_SECRET_KEY=<generated-key>
```

`shared-vlm` в текущей схеме не использует application API key. Доступ к его
published endpoint должен ограничиваться trusted LAN/VPN и host firewall.

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

Shared API/UI services предназначены для доступа из trusted LAN/VPN и имеют
отдельные configurable bind variables:

```text
SHARED_VLM_BIND_IP
SHARED_EMBEDDING_BIND_IP
OPEN_WEBUI_BIND_IP
N8N_BIND_IP
RABBITMQ_BIND_IP
```

Baseline для shared API/UI:

```text
0.0.0.0
```

Это публикует service на всех host interfaces, поэтому host firewall MUST
ограничивать доступ разрешёнными LAN/VPN source networks.

`SHARED_PUBLIC_HOST` задаёт IP/DNS, который должны использовать clients на других
машинах. Он не является bind address.

При необходимости конкретный deployment может привязать service только к LAN IP:

```dotenv
SHARED_PUBLIC_HOST=192.168.55.3
SHARED_VLM_BIND_IP=192.168.55.3
```

Infrastructure-private dependencies, например `n8n-db`, наружу не публикуются.

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
3. проверяет обязательную deployment configuration и secrets;
4. проверяет выбранные GPU/TP/DP для включённых AI profiles;
5. создаёт external network `ai-shared`, если нужно;
6. валидирует Compose.

После этого:

```bash
docker compose pull
docker compose up -d --remove-orphans
docker compose ps
./scripts/check.sh
```

AI services включаются profiles через `.env`, например:

```dotenv
COMPOSE_PROFILES=ai-vlm,ai-embedding,ai-ui
```

или явно через `--profile` в Compose command.

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

## 8. Shared AI inference

Container-to-container URLs через `ai-shared`:

```text
VLM:       http://shared-vlm:8000/v1
Embedding: http://shared-embedding:8000
Open WebUI:http://open-webui:8080
```

Logical model names:

```text
shared-vlm
shared-embedding
```

С другого компьютера или сервера используются published host endpoints через `SHARED_PUBLIC_HOST`:

```text
http://<shared-host>:8000/v1
http://<shared-host>:8001
http://<shared-host>:3000
```

Physical model ID/revision, GPU devices, Tensor/Data Parallel, context и concurrency задаются
через `.env` и не должны hardcode-иться в application projects.

### `SHARED_VLM_MODEL_REVISION`

`SHARED_VLM_MODEL_REVISION` задаёт конкретную revision Hugging Face repository,
из которой должны быть загружены model weights/config/tokenizer.

Допустимые варианты зависят от Hugging Face repository и обычно включают:

```text
main
tag
точный commit SHA
```

Например:

```dotenv
SHARED_VLM_MODEL_ID=Qwen/Qwen3.8-27B
SHARED_VLM_MODEL_REVISION=main
```

`main` удобен для первичной проверки модели, но он не является immutable:
содержимое ветки `main` на Hugging Face может измениться.

Для принятой production-like модели SHOULD использовать точный commit SHA:

```dotenv
SHARED_VLM_MODEL_ID=Qwen/Qwen3.8-27B
SHARED_VLM_MODEL_REVISION=<exact-hugging-face-commit-sha>
```

Это обеспечивает воспроизводимый recreate/rollback: один и тот же deployment
получает тот же model snapshot.

Revision, указанная при предварительном скачивании через `snapshot_download`,
MUST совпадать с `SHARED_VLM_MODEL_REVISION`, которую затем использует vLLM.

Логический alias при смене physical model не меняется:

```dotenv
SHARED_VLM_MODEL_ALIAS=shared-vlm
```

Application projects продолжают использовать:

```text
base URL: http://shared-vlm:8000/v1
model:    shared-vlm
```

и не должны знать physical Hugging Face model ID/revision или GPU layout.

### Утверждённый порядок смены physical VLM model

Перед заменой модели MUST проверить:

1. модель поддерживается используемой версией vLLM;
2. нужная modality сохраняет application contract — VLM не заменяется text-only
   моделью без отдельного архитектурного решения;
3. weights/dtype помещаются в доступный GPU layout;
4. определены `TP`, `DP`, `max_model_len` и ожидаемая VRAM;
5. для gated/private Hugging Face model при необходимости настроен `HF_TOKEN`.

Смена выполняется в следующем порядке.

#### Шаг 1. Предварительно скачать новую модель в shared Hugging Face cache

Текущий `shared-vlm` при этом продолжает работать со старой моделью.

Пример:

```bash
docker compose \
  --profile ai-vlm \
  run --rm \
  --no-deps \
  --entrypoint python3 \
  shared-vlm \
  -c 'from huggingface_hub import snapshot_download; snapshot_download("mistralai/Mistral-Large-Instruct-2407", revision="main")'
```

`docker compose run` использует тот же persistent Hugging Face volume, что и
`shared-vlm`, поэтому snapshot сохраняется в:

```text
/root/.cache/huggingface
```

а не исчезает после удаления временного container.

Для production-like deployment вместо `main` SHOULD использоваться выбранный
точный Hugging Face commit SHA.

#### Шаг 2. Проверить, что snapshot появился в shared cache

Для приведённого примера:

```bash
docker compose \
  --profile ai-vlm \
  run --rm \
  --no-deps \
  --entrypoint sh \
  shared-vlm \
  -c 'du -sh /root/.cache/huggingface/hub/models--mistralai--Mistral-Large-Instruct-2407'
```

Дополнительно SHOULD проверить свободное место:

```bash
df -h
docker system df
```

#### Шаг 3. Изменить deployment configuration в `.env`

Минимально меняются:

```dotenv
SHARED_VLM_MODEL_ID=mistralai/Mistral-Large-Instruct-2407
SHARED_VLM_MODEL_REVISION=main
```

Для принятой production-like модели `main` SHOULD быть заменён точным commit SHA.

Логический alias MUST оставаться:

```dotenv
SHARED_VLM_MODEL_ALIAS=shared-vlm
```

Если новая модель требует другого GPU layout, одновременно меняются deployment
parameters, например:

```dotenv
SHARED_VLM_GPU_DEVICES=0,1,2,3
SHARED_VLM_TP_SIZE=4
SHARED_VLM_DP_SIZE=1
SHARED_VLM_MAX_MODEL_LEN=32768
```

Конкретные значения GPU/TP/DP/context определяются размером модели, topology и
benchmark. Их нельзя механически копировать от предыдущей модели.

#### Шаг 4. Проверить configuration до остановки текущей модели

```bash
docker compose config --quiet
```

```bash
./scripts/bootstrap.sh
```

Если проверки не прошли, текущий `shared-vlm` не пересоздавать.

#### Шаг 5. Переключить `shared-vlm`

После успешного pre-download и configuration validation:

```bash
docker compose \
  --profile ai-vlm \
  up -d \
  --force-recreate \
  shared-vlm
```

Отдельный `docker compose pull` для model weights не требуется: weights уже
находятся в shared Hugging Face cache. `docker compose pull` нужен отдельно,
если меняется сам `VLLM_IMAGE`.

#### Шаг 6. Дождаться `healthy` и проверить startup

```bash
docker compose ps shared-vlm
```

```bash
docker compose logs --tail=200 -f shared-vlm
```

Параллельно:

```bash
watch -n 1 nvidia-smi
```

Нельзя считать смену завершённой, пока container не стал `healthy`.

#### Шаг 7. Smoke test logical contract

Проверка списка моделей:

```bash
curl -fsS \
  http://<shared-host>:8000/v1/models \
  | python3 -m json.tool
```

Ответ должен по-прежнему содержать logical model:

```text
shared-vlm
```

Функциональный text request:

```bash
curl -sS \
  -H "Content-Type: application/json" \
  http://<shared-host>:8000/v1/chat/completions \
  -d '{
    "model": "shared-vlm",
    "messages": [
      {
        "role": "user",
        "content": "Ответь только одним словом: РАБОТАЕТ"
      }
    ],
    "max_tokens": 32
  }' \
  | python3 -m json.tool
```

Если shared contract предполагает vision, MUST отдельно выполнить image/VLM
smoke test.

После этого:

```bash
./scripts/check.sh
```

#### Шаг 8. Rollback при неуспешной смене

Вернуть в `.env` предыдущие:

```text
SHARED_VLM_MODEL_ID
SHARED_VLM_MODEL_REVISION
SHARED_VLM_GPU_DEVICES
SHARED_VLM_TP_SIZE
SHARED_VLM_DP_SIZE
SHARED_VLM_MAX_MODEL_LEN
```

и выполнить:

```bash
docker compose \
  --profile ai-vlm \
  up -d \
  --force-recreate \
  shared-vlm
```

Если предыдущий snapshot сохранился в shared Hugging Face cache, повторное
скачивание обычно не требуется.

Главный invariant:

```text
Application projects use shared-vlm.
Only shared-infrastructure knows the physical model/revision/GPU layout.
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

- Hugging Face model cache;
- vLLM cache;
- Open WebUI data;
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
docker compose up -d --remove-orphans
docker compose ps
./scripts/check.sh
```

После изменения `.env` обычный `docker compose restart` не применяет большинство
изменённых environment variables. Нужен recreate:

```bash
docker compose up -d --force-recreate
```

Для отдельного AI service:

```bash
docker compose --profile ai-vlm up -d --force-recreate shared-vlm
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

shared-vlm:

```bash
docker compose logs --tail=100 shared-vlm
```

shared-embedding:

```bash
docker compose logs --tail=100 shared-embedding
```

Open WebUI:

```bash
docker compose logs --tail=100 open-webui
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
docs/FRONTEND_GUIDELINES.md
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
docs/FRONTEND_GUIDELINES.md
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
  ┌───────┼────────┐      ┌─────┼────────┐
shared-vlm      shared-    API frontend workers
shared-embedding services   │
Open WebUI        │          │
n8n / RabbitMQ     │
          │                 │
          └──────────── ai-shared
                           │
                    private project net
                           │
                 PostgreSQL/Qdrant/Redis
```

```text
Share infrastructure.
Isolate application state.
Use Docker DNS on the same host.
Use published shared endpoints from trusted LAN/VPN.
Keep physical model/revision/GPU placement in deployment configuration.
Keep .env sparse.
Verify runtime instead of assuming it.
```
