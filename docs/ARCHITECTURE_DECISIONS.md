<!-- docs/ARCHITECTURE_DECISIONS.md -->
# Решения shared-infrastructure

Дата согласования: 2026-10-27. Это project-specific источник; переносимый bundle
для бизнес-приложений продолжает начинаться с LLM_CONTEXT.md.

| Решение | Статус и основание |
|---|---|
| Окружение команды > .env > разрешённый default | APPROVED: последнее уточнение владельца; отменяет прежнее согласование обратного приоритета |
| Рабочая модель Qwen/Qwen3.8-27B | APPROVED владельцем |
| VLM GPU 2,3; embedding GPU 1 | APPROVED, исходное ТЗ; сеть и порядок пользователей RabbitMQ не изменяются |
| VLM TP/DP/context | APPROVED: задавать явно из принятой серверной конфигурации; прежние 2/1/262144 не утверждены как defaults |
| UI-secret | APPROVED: проверять перед запуском самого UI; stack без UI не требует его ключа |
| Логи | APPROVED: local, пять файлов по 10 МБ на контейнер; применяется только при будущем согласованном recreate |

## Нерешённые эксплуатационные вопросы

- REQUIRES_OWNER_DECISION: принятый exact SHA весов и результаты проверки
  modality/tools/MTP, latency/throughput. main автоматически не заменяется SHA.
- REQUIRES_OWNER_DECISION: какие GitHub checks обязательны перед merge;
  workflow добавлен, настройки GitHub этим коммитом не меняются.
- Реальный runtime не исследован в рамках локальной разработки. Миграция owned
  override, закрепление revision и все recreate требуют отдельного подтверждения.

## Карта ответственности

- LLM_CONTEXT.md — единственный router чтения по текущей задаче.
- ENGINEERING_GUIDELINES.md — общие правила кода/config/testing/logging.
- FRONTEND_GUIDELINES.md — единый frontend checklist с принятыми исключениями.
- INFRASTRUCTURE_INSTRUCTIONS.md — lifecycle, доступ, operational order и DoD.
- services.yaml — intended machine-readable contracts, не факт runtime.
- compose.yaml — исполняемая конфигурация и разрешённые defaults.
- .env.example — каталог; .env — закрытые изменяемые параметры deployment.
- scripts/config-check.py — проверка итогового JSON Compose и guard перед запуском моделей/UI.
- README.md — операторские команды и ссылки на нормативные инструкции.

Путь файла обязателен при выдаче; комментарий располагается первой допустимой
строкой после shebang/служебного заголовка. Сохраняется существующий MUST из
Engineering без повреждения исполняемых файлов. Frontend читается по router,
само наличие frontend не расширяет текущую infrastructure-задачу.

## Упрощение 2026-10-28

APPROVED владельцем: ровно четыре файла scripts и два файла tests, без
переключателя моделей и лишних helper/entrypoint-файлов. Проверки объединены
в config-check.py. Существующие Docker, сеть, GPU, модельные параметры и порядок
создания пользователей не меняются. Дополнительные учётные записи не добавляются:
достаточна штатная первичная инициализация RabbitMQ и PostgreSQL n8n.

Автозапуск: включённый Docker и существующая restart: unless-stopped, веса/данные
в named volumes. Bootstrap занимается первоначальной настройкой и запуском,
check только проверяет. Скриптов, меняющих .env или выбирающих модель, больше нет.
Точные TP/DP/context остаются явными deployment-настройками.
