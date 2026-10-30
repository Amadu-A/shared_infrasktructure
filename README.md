<!-- README.md -->
# shared-infrastructure

Общие VLM, embedding, Open WebUI, SearXNG, n8n и RabbitMQ для нескольких проектов,
а также изолированный CPU-only стенд оценки Docling Serve и Tesseract.
Данные и жизненный цикл приложений остаются независимыми. Docker уже должен
быть установлен; проект не переустанавливает Docker, драйверы или NVIDIA Toolkit.

## Структура

    shared-infrastructure/
    ├── compose.yaml
    ├── .env.example
    ├── .gitignore
    ├── .gitattributes
    ├── README.md
    ├── docling-eval/
    │   └── compose.yaml
    ├── searxng/
    │   └── settings.yml
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

    COMPOSE_PROFILES=ai-vlm,ai-embedding,ai-ui,ai-search

Можно отключить необязательный UI, убрав ai-ui. Для ai-ui нужен также ai-vlm.
SearXNG включается профилем ai-search и остаётся доступным только в ai-shared.
При отключённых inference-профилях проверка не требует NVIDIA.

Основные параметры:

| Переменная | Назначение |
|---|---|
| `COMPOSE_PROFILES` | Включённые AI-профили: `ai-vlm,ai-embedding,ai-ui,ai-search` |
| `SHARED_NETWORK_NAME` | Существующая external-сеть, обычно `ai-shared` |
| `SHARED_*_GPU_DEVICES` | GPU из серверного `.env`; Python их не выбирает |
| `SHARED_*_TP_SIZE`, `SHARED_*_DP_SIZE` | Должны соответствовать числу GPU: `TP × DP` |
| `SHARED_*_MAX_MODEL_LEN` | Явный контекст каждой модели |
| `RABBITMQ_REQUIRED_VHOSTS` | Существующие обязательные vhost через запятую; ничего не создаёт |
| `DOCLING_HOST_PORT` | Loopback-only host port отдельного Docling Compose project |
| `*_BIND_IP`, `*_HOST_PORT` | Публикация сервисов на host |

Обязательные секреты: `N8N_DB_PASSWORD`, `N8N_ENCRYPTION_KEY`,
`RABBITMQ_DEFAULT_PASS`, `DOCLING_API_KEY`. `OPEN_WEBUI_SECRET_KEY` нужен только
для UI, `SEARXNG_SECRET` — только для профиля ai-search. На новой установке
генерировать каждый секрет отдельно; вывод не сохранять в shell history или Git:

    openssl rand -hex 32
    umask 077
    cp .env.example .env
    chmod 600 .env

В `.env` заменить placeholders вручную. Действующие секреты существующей
установки не менять и `.env.example` поверх `.env` не копировать.

Рабочая модель — Qwen/Qwen3.8-27B, VLM использует GPU 2,3, embedding — GPU 1.
SHARED_VLM_TP_SIZE, SHARED_VLM_DP_SIZE и SHARED_VLM_MAX_MODEL_LEN задаются явно
из принятой серверной конфигурации, не подбираются скриптом.
Размеры очередей, MODEL_ARGS, revision, image, bind IP и порты сохранять
существующими. Все переменные перечислены в .env.example.

main у Hugging Face изменяем; точный SHA закрепляется владельцем отдельно.
Ни один скрипт здесь не выбирает и не скачивает VLM/embedding weights, не редактирует
.env и не создаёт compose.override.yaml. Единственный небольшой загружаемый
bootstrap-артефакт — закреплённый `docling-eval/tessdata/rus.traineddata`.
Доступны модели, обслуживаемые
настроенными VLM/embedding контейнерами; само наличие других весов в cache
не делает их отдельными API-моделями.

## Первый запуск

На Linux-сервере с systemd из существующего каталога:

    cd /projects/shared-infrastructure
    bash scripts/bootstrap.sh

Bootstrap проверяет, что Docker уже запущен и включён в автозапуск, создаёт
отсутствующую согласованную shared-сеть, проверяет/скачивает закреплённую русскую
модель Tesseract и запускает основной stack и отдельный project `docling-eval`.
Системные настройки он не меняет. При ошибке администратор отдельно выполняет
указанную команду `systemctl`, после чего повторяет bootstrap.

Пользователь базы n8n и начальный администратор RabbitMQ создаются штатными
образами при первом запуске пустых volumes. Имена, секреты и порядок этой
инициализации сохранены. При повторном запуске существующие пользователи,
пароли, базы и очереди не пересоздаются. Проектные пользователи/vhost брокера
по-прежнему добавляются отдельной согласованной административной операцией.

Обычный bootstrap использует `docker compose up --no-recreate` для обоих Compose
projects: он создаёт отсутствующие сервисы, но не применяет новую конфигурацию к
работающим контейнерам. Обновления выполняются отдельной явной процедурой ниже.

Проверка подготовки без изменения системы и запуска контейнеров:

    bash scripts/bootstrap.sh --check-only

При таком запуске отсутствующая сеть или отсутствующий/повреждённый
`rus.traineddata` считаются ошибкой: каталоги и файлы не создаются.

Bootstrap выводит только разрешённую сводку итоговых Compose-настроек: project,
включённые сервисы, сеть, порты и наличие secrets с маскированием значений.
Содержимое `.env` и полный config/inspect в diagnostics не попадают.

Русская tessdata закреплена на commit `87416418657359cb625c412a48b6e1d6d41c29bd`
репозитория `tesseract-ocr/tessdata_fast` и SHA-256
`e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225`.
Корректный файл повторно не скачивается; новый файл принимается атомарно после
проверки hash, получает права `644` и остаётся вне Git.

Docling использует корневой `.env` или тот же `--env-file`, который передан
bootstrap/check. Для отдельной валидации и эксплуатации из корня репозитория:

    python3 scripts/config-check.py docling-validate
    python3 scripts/config-check.py docling-prepare --check-only
    python3 scripts/config-check.py docling-check

После согласованного изменения только Docling выполнить целевое обновление:

    docker compose --env-file .env --project-name docling-eval -f docling-eval/compose.yaml up -d --no-deps docling
    python3 scripts/config-check.py docling-check

Rollback: вернуть прежний Docling Compose/image и закрытые deployment-параметры,
повторить эту целевую команду и check. Основной stack при этом не пересоздаётся.

Чтобы подготовить и поднять только Docling (не запускать общий bootstrap):

    python3 scripts/config-check.py docling-validate --env-file .env --show-settings
    python3 scripts/config-check.py docling-prepare --env-file .env
    python3 scripts/config-check.py docling-start --env-file .env --wait-timeout 900
    python3 scripts/config-check.py docling-check --env-file .env

Если ключ пока хранится в `docling-eval/.env`, вместо `.env` во всех четырёх
командах передать `docling-eval/.env`. Автоматически переносить секреты или
копировать образец `.env.example` поверх существующего `.env` не требуется.

## Перезагрузка сервера

Bootstrap только проверяет автозапуск Docker. Для всех сервисов сохранена политика
`restart: unless-stopped`. После штатной перезагрузки ранее работающие
контейнеры поднимаются Docker, VLM заново загружает выбранную модель. Веса
сохраняются в named volume huggingface-cache, данные остальных сервисов — в
их собственных постоянных volumes. Повторный bootstrap после reboot не нужен.

Сервис, который оператор явно остановил, остаётся остановленным; это штатная
семантика unless-stopped. После docker compose down контейнеры нужно создать
заново. Во время загрузки большой модели API ещё может быть неготов.

Проверка реального восстановления без `compose up` и без изменения конфигурации:

    systemctl is-enabled docker.service
    systemctl is-active docker.service
    python3 scripts/config-check.py reboot-snapshot --state-file /tmp/shared-reboot.json
    sudo systemctl restart docker.service
    python3 scripts/config-check.py reboot-verify --state-file /tmp/shared-reboot.json

Последняя команда ждёт health до 30 минут, требует те же container ID и named
volumes обоих projects, повторно проверяет API, RabbitMQ и Docling. Это эксплуатационная проверка:
выполнять её только в согласованное окно. Файл snapshot не содержит env или секреты.

Основание: [Docker restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/)
и [Docker systemd autostart](https://docs.docker.com/engine/install/linux-postinstall/).

## Проверки

Одна команда проверки работающего стека, без изменений:

    bash scripts/check.sh

Скрипт проверяет оба итоговых Compose, контейнеры, health, языки Tesseract
`rus/eng/osd` и логические имена из `/v1/models`. Для RabbitMQ проверяются
работающий broker, AMQP-порт, реальная
авторизация существующего администратора через Management API, наличие
`RABBITMQ_REQUIRED_VHOSTS` и права пользователя на них. Пользователи, пароли и
vhost не создаются и не изменяются. Секреты и полный config/inspect не печатаются.

Полная локальная и runtime-проверка одной командой (после установки dev-зависимостей):

    python3 -m pip install -r requirements-dev.txt
    bash scripts/check.sh --with-tests

Она последовательно запускает проверки Compose/контейнеров/API, Ruff и pytest.
Ошибка одного этапа не отменяет остальные; при любом отказе итоговый exit code — 1.

`healthy` означает успех container healthcheck, но не доказывает совпадение
работающей конфигурации с Compose. Проверка сообщает отдельно несовпавшие поля
`Image`, `Cmd`, `Entrypoint` или имена GPU-переменных; значения команд и secrets
не печатаются. Не заданные в Compose `command`/`entrypoint` наследуются из image
и сами по себе не считаются расхождением ([Compose specification](https://docs.docker.com/reference/compose-file/services/#entrypoint)).

Если общий bootstrap завершился после `[OK] docling` с ошибками других сервисов,
это не откатывает запущенный Docling. Для точной read-only диагностики:

    bash scripts/check.sh
    python3 scripts/config-check.py migration-plan

`migration-plan` проверяет старые helper-mounts и совместимость параметров;
контейнеры не меняет. Обнаруженное реальное расхождение устранять целевой
процедурой из раздела обновления, а не массовым пересозданием healthy-сервисов.

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
| SearXNG | http://searxng:8080 | не публикуется |
| Docling | http://docling-eval:5001 | 127.0.0.1:5001 |
| n8n | http://n8n:5678 | 5678 |
| RabbitMQ | rabbitmq:5672 | 5672, панель 15672 |

Приложения используют logical model aliases shared-vlm и shared-embedding.
Docling требует API key, отключает remote services и доступен с host только через
loopback. Его Compose project не управляет lifecycle остальных shared-сервисов.
На том же сервере нужный application-контейнер подключается к external ai-shared,
а собственная БД остаётся в private project network. На другом approved host
используются SHARED_PUBLIC_HOST и published ports. Published 0.0.0.0 ограничивается
firewall разрешённой LAN/VPN. API keys для inference не требуются.

n8n-db доступна только n8n через n8n-internal и не публикует host port.
Bootstrap admin RabbitMQ не используется приложениями. Для workflow n8n
сохраняются префиксы проекта вроде [PDRD] и [CONTRACT].

Минимальный Compose-фрагмент приложения на том же сервере:

```yaml
services:
  app:
    image: example/app:latest
    environment:
      VLM_BASE_URL: http://shared-vlm:8000/v1
      VLM_MODEL: shared-vlm
      EMBEDDING_BASE_URL: http://shared-embedding:8000/v1
      EMBEDDING_MODEL: shared-embedding
      RABBITMQ_HOST: rabbitmq
      RABBITMQ_PORT: "5672"
      # Логин, пароль и vhost приложения берутся из его собственного закрытого env.
    networks:
      - app-private
      - ai-shared

networks:
  app-private:
  ai-shared:
    external: true
    name: ai-shared
```

База приложения остаётся только в `app-private`; `n8n-db` подключать запрещено.

## Обновление и ручная смена модели

Установка кода не переключает работающую модель. После обновления репозитория и
**до перезагрузки Docker/сервера** выполнить согласованную миграцию старых mount
`vlm-entrypoint.py`, `deployment.py` и `open-webui-entrypoint.sh`:

    bash scripts/bootstrap.sh --check-only
    bash scripts/bootstrap.sh --update-ai-helpers

Режим сам находит только существующие контейнеры со старыми helper-mounts и
последовательно применяет новый `config-check.py` к `shared-vlm`,
`shared-embedding`, `open-webui`. Используются `--no-deps` и обычный Compose
reconcile без `--force-recreate`; базовые сервисы, сети, users и volumes не
затрагиваются. Если старых mounts нет, контейнеры не изменяются.

Rollback при ошибке: остановить процедуру, вернуть предыдущую версию кода,
затем применить её только к уже затронутому сервису командой
`docker compose up -d --no-deps <service>` и выполнить `bash scripts/check.sh`.
`down -v`, `--remove-orphans` и массовый `--force-recreate` запрещены.

Для дальнейшей смены модели — нормативный порядок
[Infrastructure §9](docs/INFRASTRUCTURE_INSTRUCTIONS.md#9-gpu-и-hardware-portability):
проверить совместимость, заранее подготовить snapshot в существующем cache,
сохранить backup, вручную изменить только согласованные значения .env,
проверить настройки и применить изменение только к VLM:

    python3 scripts/config-check.py validate --profile ai-vlm
    docker compose --profile ai-vlm up -d --no-deps shared-vlm
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
    python -m ruff check scripts tests
    python -m ruff format --check scripts tests
    python -B -m pytest -p no:cacheprovider -q

Для проверки приоритета настроек часть тестов вызывает только Compose config
на временных env-файлах, без Docker daemon и без запуска контейнеров.
CI запускает Ruff, тесты и проверяет синтаксис двух Bash-скриптов.
