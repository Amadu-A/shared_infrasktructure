<!-- README.md -->
# shared-infrastructure

Общие VLM, embedding, Open WebUI, n8n и RabbitMQ для нескольких проектов.
Данные и жизненный цикл приложений остаются независимыми. Docker уже должен
быть установлен; проект не переустанавливает Docker, драйверы или NVIDIA Toolkit.

## Структура

    shared-infrastructure/
    ├── compose.yaml
    ├── .env.example
    ├── .gitignore
    ├── .gitattributes
    ├── README.md
    ├── docs/
    │   ├── LLM_CONTEXT.md
    │   ├── ENGINEERING_GUIDELINES.md
    │   ├── FRONTEND_GUIDELINES.md
    │   ├── INFRASTRUCTURE_INSTRUCTIONS.md
    │   ├── ARCHITECTURE_DECISIONS.md
    │   └── services.yaml
    ├── scripts/
    │   ├── bootstrap.sh
    │   ├── check.sh
    │   ├── check-vlm-tool-calling.py
    │   └── config-check.py
    ├── tests/
    │   ├── test_config.py
    │   └── test_vlm_contract.py
    ├── requirements-dev.txt
    └── .github/workflows/checks.yml

Серверный .env, .git, локальное Python-окружение и настройки IDE не входят
в публикуемую структуру и не коммитятся.

## Настройки

Приоритет один для Docker и всех скриптов:

    окружение команды > серверный .env > разрешённые defaults Compose

.env.example — каталог, он автоматически не загружается. В серверном .env
хранятся реальные секреты, обязательные настройки и отличия от defaults.
Существующий .env не заменять новым образцом и не переписывать при обновлении.

Для полного набора сервисов указать в .env:

    COMPOSE_PROFILES=ai-vlm,ai-embedding,ai-ui

Можно отключить необязательный UI, убрав ai-ui. Для ai-ui нужен также ai-vlm.
При отключённых inference-профилях проверка не требует NVIDIA.

Обязательные секреты: N8N_DB_PASSWORD, N8N_ENCRYPTION_KEY,
RABBITMQ_DEFAULT_PASS. OPEN_WEBUI_SECRET_KEY нужен только для UI.
На новой установке для каждого секрета отдельно выполнить openssl rand -hex 32
и записать результат в закрытый .env с правами 600. Действующие секреты
существующей установки не менять.

Рабочая модель — Qwen/Qwen3.8-27B, VLM использует GPU 2,3, embedding — GPU 1.
SHARED_VLM_TP_SIZE, SHARED_VLM_DP_SIZE и SHARED_VLM_MAX_MODEL_LEN задаются явно
из принятой серверной конфигурации, не подбираются скриптом.
Размеры очередей, MODEL_ARGS, revision, image, bind IP и порты сохранять
существующими. Все переменные перечислены в .env.example.

main у Hugging Face изменяем; точный SHA закрепляется владельцем отдельно.
Ни один скрипт здесь не выбирает модель, не скачивает другие веса, не редактирует
.env и не создаёт compose.override.yaml. Доступны модели, обслуживаемые
настроенными VLM/embedding контейнерами; само наличие других весов в cache
не делает их отдельными API-моделями.

## Первый запуск

На Linux-сервере с systemd из существующего каталога:

    cd /projects/shared-infrastructure
    bash scripts/bootstrap.sh

Bootstrap проверяет настройки, при необходимости включает автозапуск уже
установленного Docker, создаёт отсутствующую согласованную shared-сеть,
запускает выбранные сервисы и ждёт их готовности до 30 минут.

Пользователь базы n8n и начальный администратор RabbitMQ создаются штатными
образами при первом запуске пустых volumes. Имена, секреты и порядок этой
инициализации сохранены. При повторном запуске существующие пользователи,
пароли, базы и очереди не пересоздаются. Проектные пользователи/vhost брокера
по-прежнему добавляются отдельной согласованной административной операцией.

На работающем сервере bootstrap не является повседневной диагностикой:
docker compose up может применить изменившуюся конфигурацию контейнеров.
Обновления применяются отдельно, только к согласованным сервисам.

Проверка подготовки без изменения системы и запуска контейнеров:

    bash scripts/bootstrap.sh --check-only

При таком запуске отсутствующая сеть считается ошибкой, а не создаётся.

## Перезагрузка сервера

Bootstrap включает Docker в загрузку ОС. Для всех сервисов сохранена существующая
политика restart: unless-stopped. После штатной перезагрузки ранее работающие
контейнеры поднимаются Docker, VLM заново загружает выбранную модель. Веса
сохраняются в named volume huggingface-cache, данные остальных сервисов — в
их собственных постоянных volumes. Повторный bootstrap после reboot не нужен.

Сервис, который оператор явно остановил, остаётся остановленным; это штатная
семантика unless-stopped. После docker compose down контейнеры нужно создать
заново. Во время загрузки большой модели API ещё может быть неготов.

После reboot:

    systemctl is-enabled docker.service
    systemctl is-active docker.service
    bash scripts/check.sh

Основание: [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/)
и [Docker systemd autostart](https://docs.docker.com/engine/install/linux-postinstall/).

## Проверки

Одна команда проверки работающего стека, без изменений:

    bash scripts/check.sh

Скрипт проверяет итоговый Compose, ожидаемые и уже работающие AI-сервисы,
health и логические имена из /v1/models. Отсутствующий включённый сервис —
ошибка. Секреты и полный config/inspect не печатаются.

Отдельная проверка только настроек:

    python3 scripts/config-check.py validate

Временные --profile и --env-file передаются одинаково bootstrap/check/config-check.
Например:

    SHARED_VLM_HOST_PORT=8111 bash scripts/check.sh --profile ai-vlm

Функциональная проверка текста, vision и Tool Calling реальной модели:

    python3 scripts/check-vlm-tool-calling.py --url http://127.0.0.1:8000/v1

Использовать фактический порт/bind IP. Эта проверка отправляет запросы модели.
Готовность PDRD-validation, OpenClaw, Open WebUI и остальных клиентов проверяется
перед эксплуатационными изменениями; HTTP 200 не доказывает сохранение throughput.

## Подключение приложений

| Сервис | Адрес внутри ai-shared | Default host port |
|---|---|---|
| shared-vlm | http://shared-vlm:8000/v1 | 8000 |
| shared-embedding | http://shared-embedding:8000/v1; /pooling на корне | 8001 |
| Open WebUI | http://open-webui:8080 | 3000 |
| n8n | http://n8n:5678 | 5678 |
| RabbitMQ | rabbitmq:5672 | 5672, панель 15672 |

Приложения используют logical model aliases shared-vlm и shared-embedding.
На том же сервере нужный application-контейнер подключается к external ai-shared,
а собственная БД остаётся в private project network. На другом approved host
используются SHARED_PUBLIC_HOST и published ports. Published 0.0.0.0 ограничивается
firewall разрешённой LAN/VPN. API keys для inference не требуются.

n8n-db доступна только n8n через n8n-internal и не публикует host port.
Bootstrap admin RabbitMQ не используется приложениями. Для workflow n8n
сохраняются префиксы проекта вроде [PDRD] и [CONTRACT].

## Обновление и ручная смена модели

Установка кода не переключает работающую модель. Новый формат использует один
config-check.py вместо удалённых helper/entrypoint-файлов. Поэтому при переходе
со старой версии до следующей перезагрузки требуется согласованно пересоздать
shared-vlm, shared-embedding и open-webui: старые контейнеры могут ссылаться
на удалённые host-файлы. Их текущую конфигурацию и секреты предварительно сверить
и сохранить. В рамках локальной разработки эти операции не выполняются.

Для дальнейшей смены модели — нормативный порядок
[Infrastructure §9](docs/INFRASTRUCTURE_INSTRUCTIONS.md#9-gpu-и-hardware-portability):
проверить совместимость, заранее подготовить snapshot в существующем cache,
сохранить backup, вручную изменить только согласованные значения .env,
проверить настройки и применить изменение только к VLM:

    python3 scripts/config-check.py validate --profile ai-vlm
    docker compose --profile ai-vlm up -d --no-deps --force-recreate shared-vlm
    bash scripts/check.sh --profile ai-vlm

Rollback: вернуть прежние согласованные параметры .env и соответствующую версию
кода, затем целевой recreate и повторная проверка. restart не применяет новые
environment/command. Не удалять чужой или старый override без отдельного разбора.
Изменение embedding identity требует проверки совместимости существующих индексов.

Не выполнять массовый force-recreate, --remove-orphans и down -v для изменения
одного сервиса. Сети, порядок регистрации RabbitMQ, images и постоянные volumes
при упрощении сохранены. Ротация логов остаётся local, пять файлов по 10 МБ.

## Разработчикам и LLM

Начать с [LLM_CONTEXT](docs/LLM_CONTEXT.md), затем читать документы по задаче:
[Engineering](docs/ENGINEERING_GUIDELINES.md),
[Frontend](docs/FRONTEND_GUIDELINES.md),
[Infrastructure](docs/INFRASTRUCTURE_INSTRUCTIONS.md).
Контракты и intended topology: [services.yaml](docs/services.yaml).
Решения проекта: [Architecture decisions](docs/ARCHITECTURE_DECISIONS.md).

Минимальные тесты в двух файлах; дополнительные сервисы или модель им не нужны:

    python -m pip install -r requirements-dev.txt
    python -B -m pytest -p no:cacheprovider -q

Для проверки приоритета настроек часть тестов вызывает только Compose config
на временных env-файлах, без Docker daemon и без запуска контейнеров.
CI запускает эти тесты и проверяет синтаксис двух Bash-скриптов.
