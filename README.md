<!-- README.md -->
# shared-infrastructure

Общие сервисы для нескольких независимых проектов и переносимый набор инструкций
в docs/. Stack содержит shared-vlm, shared-embedding, Open WebUI, n8n с private
n8n-db и RabbitMQ. PostgreSQL/Qdrant/Redis приложений и их workers остаются
project-specific. Остановка приложения не должна останавливать shared stack.

## Источники требований

| Область | Источник |
|---|---|
| Порядок чтения | [LLM_CONTEXT](docs/LLM_CONTEXT.md) |
| Общая инженерная практика | [Engineering](docs/ENGINEERING_GUIDELINES.md) |
| Frontend и единый checklist | [Frontend](docs/FRONTEND_GUIDELINES.md) |
| Ownership, lifecycle, смена модели, единый DoD | [Infrastructure](docs/INFRASTRUCTURE_INSTRUCTIONS.md) |
| Intended API/topology | [services.yaml](docs/services.yaml) |
| Решения именно для этого deployment | [Architecture decisions](docs/ARCHITECTURE_DECISIONS.md) |
| Контейнеры и допустимые defaults | compose.yaml |
| Каталог переменных без секретов | .env.example |
| Изменяемые deployment-параметры и секреты | серверный .env |

services.yaml и Compose не доказывают, что сервис сейчас работает.
PROJECT_INTEGRATION.md и SERVICES.md — указатели, а не отдельные наборы правил.
Для нового application project достаточно пяти файлов переносимого docs/ bundle;
project-specific Architecture decisions относятся к оператору этого stack.
Frontend Guidelines читаются полностью, когда текущая задача затрагивает frontend.

## Конфигурация

Единый приоритет:

    окружение команды > серверный .env > разрешённый default Compose

На Linux временное значение передаётся так:

    SHARED_VLM_HOST_PORT=8111 docker compose --profile ai-vlm config --quiet
    SHARED_VLM_HOST_PORT=8111 ./scripts/bootstrap.sh --profile ai-vlm --check-only
    SHARED_VLM_HOST_PORT=8111 ./scripts/check.sh --profile ai-vlm

Во всех трёх случаях применяется один порт. В PowerShell переменная задаётся
через $env:SHARED_VLM_HOST_PORT перед командой; после проверки её нужно вернуть
в прежнее состояние, если она уже существовала.

Скрипты не исполняют .env через shell и не реализуют отдельный dotenv-парсер.
scripts/deployment.py читает итоговый docker compose config --format json в памяти,
не печатая секреты. Переданный --env-file/--profile используется одинаково всеми
проверками. COMPOSE_PROFILES из окружения/.env разрешает сам Compose.

.env.example — каталог, не runtime env-файл. Серверный .env остаётся sparse:
реальные секреты, обязательные параметры и отличия от допустимых defaults.
Копировать весь каталог не требуется; удалять переменные по устаревшему названию
запрещено без проверки фактических обращений.

Для базового stack обязательны настоящие N8N_DB_PASSWORD, N8N_ENCRYPTION_KEY,
RABBITMQ_DEFAULT_PASS. SHARED_PUBLIC_HOST задаёт объявляемый клиентам IP/DNS;
при доступе с других машин он должен быть настроен на реальный адрес сервера.
Он отличается от bind IP.

Для VLM обязательны явно заданные SHARED_VLM_TP_SIZE, SHARED_VLM_DP_SIZE и
SHARED_VLM_MAX_MODEL_LEN. Их значения берутся из принятой серверной конфигурации:
ни каталог, ни переключатель не выбирают их вместо владельца. Проверка Docker
entrypoint отказывает до запуска модели при отсутствии обязательных параметров.
Для других обычных non-secret настроек допускаются документированные defaults.

Подтверждённая модель — Qwen/Qwen3.8-27B. Размещение VLM — GPU 2,3; embedding —
GPU 1. Изменение этого размещения требует нового решения владельца.
MODEL_REVISION=main допустим для оценки, но не доказывает неизменность весов.
Перед переключением с возможностью точного rollback нужна принятая exact revision;
скрипт не закрепляет её автоматически.

SHARED_VLM_MODEL_ARGS содержит дополнительные модельные flags. Существующие
reasoning, tools и MTP оставлены в default; совместимость нового набора проверяется
оператором. Нельзя повторять в MODEL_ARGS model/revision/alias/GPU/TP/DP/context
или добавлять API-key authentication.

OPEN_WEBUI_SECRET_KEY обязателен только для выбранного/запускаемого UI.
Пустой ключ и CHANGE_ME отклоняются до запуска приложения. Остальной stack
может работать без UI и его ключа. Существующий ключ автоматически не меняется.
Новый entrypoint сохраняет штатный запуск bash /app/backend/start.sh образа
open-webui v0.11.3:
https://github.com/open-webui/open-webui/blob/v0.11.3/Dockerfile

Inference endpoints не требуют application API keys. Существующее unused внутри
Open WebUI — техническое значение клиента, не credential и не способ защиты.
HF_TOKEN нужен только для gated/private моделей и не передаётся приложениям.

## Требования и первый запуск Linux

Нужны Docker Compose с поддержкой gpus, Python 3.12+, а для выбранных inference
profiles — NVIDIA Container Toolkit и доступные GPU. Сначала проверить Docker,
GPU, порты и firewall. Сетевые манифесты сохраняют прежние имена ai-shared и
n8n-internal. Порядок создания пользователей RabbitMQ этим комплектом не меняется.

    docker --version
    docker compose version
    docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
    docker network ls
    sudo ss -lntp
    nvidia-smi

Последняя команда нужна для GPU deployment. Выбор AI выполняется через
COMPOSE_PROFILES=ai-vlm,ai-embedding,ai-ui либо соответствующие --profile.
При ai-ui требуется также ai-vlm.

После подготовки закрытого .env:

    python3 scripts/deployment.py validate
    bash scripts/bootstrap.sh --check-only

Отсутствующую внешнюю сеть режим --check-only не создаёт. Только при первом
запуске оператор может выполнить обычный bootstrap, который создаст указанную
в Compose сеть после успешной валидации:

    bash scripts/bootstrap.sh
    docker compose pull
    docker compose up -d
    bash scripts/check.sh

Это команды первого запуска, не команда применения текущего исправления.
Bootstrap не запускает и не пересоздаёт контейнеры. При ошибке обязательных
настроек он завершается до создания сети. config --quiet проверяет структуру,
но не заменяет семантическую проверку deployment.py validate.

## Shared contracts и доступ

| Service | Docker DNS / internal | Default host port |
|---|---|---|
| shared-vlm | http://shared-vlm:8000/v1 | 8000 |
| shared-embedding | http://shared-embedding:8000/v1; также /pooling на корне | 8001 |
| open-webui | http://open-webui:8080 | 3000 |
| n8n | http://n8n:5678 | 5678 |
| rabbitmq | rabbitmq:5672; Management :15672 | 5672 / 15672 |
| n8n-db | n8n-db:5432, только private network n8n | не публикуется |

Logical aliases: shared-vlm и shared-embedding. Physical model/revision,
GPU/TP/DP/context не должны попадать в business code. На том же host application
использует external ai-shared и Docker DNS; на другом разрешённом host —
SHARED_PUBLIC_HOST и published port. Application database остаётся в private
project network. Published bind default — 0.0.0.0; доступ разрешён только из
согласованных LAN/VPN источников и ограничивается host firewall.

n8n-db принадлежит исключительно n8n. n8n workflows используют project namespace
вроде [PDRD] и [CONTRACT]. RabbitMQ сохраняет существующий порядок регистрации:
предпочтительно отдельные vhost и application user на проект; bootstrap admin
не используется приложениями. Новые команды регистрации здесь не вводятся.

## Проверки без изменения deployment

    docker compose config --quiet
    python3 scripts/deployment.py validate
    bash scripts/bootstrap.sh --check-only
    python3 scripts/deployment.py compare-runtime
    bash scripts/check.sh

Те же --profile/--env-file нужно передавать всей последовательности, если они
переданы команде запуска. Если значения окружения были временными, следующая
команда в другом окружении проверит другую конфигурацию — сохраняйте их одинаковыми.

check требует ожидаемые сервисы, а также проверяет уже работающие AI-сервисы
данного Compose-проекта. Отключённый отсутствующий AI допустим; включённый
отсутствующий/неhealthy сервис — ошибка. Без GPU-сервисов NVIDIA не требуется.
Проверяются alias, endpoint, параметры AI и конечные логи. Расхождение runtime
не исправляется автоматически.

## Смена VLM и rollback

Нормативный порядок:
[Infrastructure §9](docs/INFRASTRUCTURE_INSTRUCTIONS.md#9-gpu-и-hardware-portability).

Сначала read-only:

    python3 scripts/switch-vlm.py --dry-run

Переключатель требует существующий здоровый VLM и согласованность его параметров
с Compose. Выбор 0/пустой ответ ничего не пишет. Пункт основной 27B сохраняет
её текущий полный профиль и exact revision; он не подбирает параметры возврата
по параметрам тестовой модели.

Legacy override прежнего переключателя переносится отдельно:

    python3 scripts/switch-vlm.py --migrate-current --dry-run
    python3 scripts/switch-vlm.py --migrate-current

Вторая команда запрашивает YES, переносит полный набор VLM-параметров в .env,
архивирует owned override и доказывает эквивалентность всего Compose. Recreate
не выполняется. Чужой override, COMPOSE_FILE или параллельная запись блокируют
операцию. Архив/backup сохраняются рядом с .env с именем .env.bak-*, не в Git.

Эксплуатационное переключение запускается отдельно:

    python3 scripts/switch-vlm.py

Тестовый каталог не является доказательством совместимости модели. Для выбора
экспериментального профиля дополнительно требуется --approved-model с полным
model ID после отдельного утверждения владельцем сохранения modality/tools и
результатов испытания. Непроверенный parser остаётся заблокированным.

После явного YES допускается recreate только shared-vlm с --no-deps. Активные
запросы прерываются. Веса не скачиваются и не удаляются. Точные прежние параметры
сохраняются в закрытом backup. При ошибке конфигурации файл возвращается без
recreate; после начатого recreate выполняется возврат прежнего VLM и проверки
health/text/vision/tools. Если rollback не подтверждён, требуется ручная проверка.

Для ручного rollback оператор выбирает нужный закрытый .env.bak-* и возвращает
согласованную пару кода/конфигурации. Нельзя слепо восстанавливать старый полный
.env поверх новых секретов: сначала проверить локально, какие несвязанные
параметры менялись. Пример атомарного восстановления при отсутствии таких изменений:

    cp -p -- .env.bak-APPROVED_BACKUP .env.bak-restore
    mv -- .env.bak-restore .env
    python3 scripts/deployment.py validate --profile ai-vlm
    docker compose --profile ai-vlm up -d --no-deps --force-recreate shared-vlm
    python3 scripts/deployment.py compare-runtime --profile ai-vlm
    bash scripts/check.sh --profile ai-vlm

Перед операторским rollback убедиться, что process environment не перекрывает
восстанавливаемые значения. Старый owned override восстанавливается из архива
только при возврате к соответствующей прежней версии кода/конфигурации.
Обычный docker compose restart изменённые параметры не применяет.

## Приёмка и одновременная нагрузка

Пример для фактических опубликованных адресов, base URL содержит /v1:

    python3 scripts/check-vlm-tool-calling.py --url "$vlm_base_url"
    python3 scripts/check-shared-contracts.py --vlm-url "$vlm_base_url" --embedding-url "$embedding_base_url"

Второй скрипт отправляет три одновременных synthetic VLM-запроса и embedding.
При известной принятой размерности передать --embedding-dimension.
Он не заменяет настоящие проверки PDRD-validation, OpenClaw и Open WebUI.
Проверить их одновременно, отсутствие OOM, неожиданных 5xx и голодания клиентов.
MTP/throughput/latency сравниваются с принятой baseline по метрикам и клиентским
сценариям; HTTP 200 не является доказательством сохранения производительности.

Embedding model/revision/dimension/preprocessing не меняются этим исправлением.
Если они меняются позже — нужен compatibility check существующих индексов и,
при несовместимости, отдельный controlled migration/reindex.

## Логи и безопасное обновление

Для всех шести сервисов согласован local driver, max-size=10m, max-file=5.
Каталог SHARED_LOG_MAX_SIZE/SHARED_LOG_MAX_FILE позволяет согласованные overrides.
Текущие контейнеры не меняют driver от Git pull: нужны отдельные целевые recreate.
check обнаруживает контейнеры без подтверждённых конечных ограничений.

Перед применением entrypoint/logging изменений сравнить текущий runtime и
подготовить закрытые backups. Применять по сервисам в согласованные окна.
Для n8n-db требуется проверенная процедура backup/restore и окно n8n.
Общий up --remove-orphans или force-recreate всего stack не использовать.
Recreate только одобренного сервиса:

    docker compose up -d --no-deps --force-recreate APPROVED_SERVICE

Изменение image — отдельная проверка совместимости; tags на latest не менять.
Docker обслуживает собственные log files; активные файлы из /var/lib/docker
вручную не удалять. Ротация ограничивает размер; удалённые старые логи обычным
rollback не восстановить.

Deployment запрещён при неизвестном override, несовпадении runtime, отсутствии
обязательных параметров/backup, падении тестов или нерешённом архитектурном вопросе.
Установка данного коммита не является подтверждением переключения модели.

## Тесты Windows и Linux

Python 3.12+:

    python -m pip install -r requirements-dev.txt
    python -B -m pytest -p no:cacheprovider -q

Offline-набор не запускает Docker daemon/контейнеры. Дополнительные тесты
с маркером compose используют только docker compose config и временные env-файлы.
Windows: Git Bash проверяет shell syntax; Linux CI делает это автоматически.
Если стандартная временная папка недоступна, выбрать новую собственную директорию
через pytest --basetemp. Не использовать существующую папку с нужными файлами:
pytest управляет её содержимым.

Workflow Infrastructure checks запускает проверки на Windows/Linux. Обязательность
проверки перед merge настраивается отдельно в GitHub; workflow сам её не включает.

Все изменения текущего исправления составляют один commit. Перед commit:

    git diff --check
    git diff --cached --check
    git status --short

Не использовать git add . для серверного каталога. У origin этого checkout два
push-адреса; для однозначной отправки пользователь использует явный target:

    git push git@github.com:Amadu-A/shared_infrasktructure.git HEAD:refs/heads/fix/instruction-consistency

## Остановка, данные и reboot

Операторские stop/start/down относятся только к текущему Compose-проекту.
restart: unless-stopped сохраняется. После reboot проверяются Docker, shared
network, GPU и check. Не выполнять down -v без отдельного решения: он может
удалить HF/vLLM cache, Open WebUI, n8n/PostgreSQL и RabbitMQ data.
External ai-shared не принадлежит lifecycle application project.
