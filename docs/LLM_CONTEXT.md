# LLM Project Development Context

Этот файл — **главная точка входа** для LLM / AI coding agent при создании,
планировании или существенном изменении проекта.

Папка `docs/` рассматривается как переносимый instruction bundle.
Программист может передать LLM эти файлы отдельно от репозитория
`shared-infrastructure`.

## Обязательный набор инструкций

LLM MUST считать связанным набором:

```text
docs/
├── LLM_CONTEXT.md
├── ENGINEERING_GUIDELINES.md
├── FRONTEND_GUIDELINES.md
├── INFRASTRUCTURE_INSTRUCTIONS.md
└── services.yaml
```

Если один из этих файлов отсутствует, LLM SHOULD явно сообщить об этом до
проектирования затрагиваемой области.

---

## 1. Обязательный порядок перед проектированием

Перед созданием структуры проекта, написанием кода или существенным изменением
архитектуры LLM MUST:

1. Прочитать `ENGINEERING_GUIDELINES.md`.
2. Если проект содержит frontend или задача затрагивает HTML/CSS/JavaScript/
   TypeScript/templates/browser UI — полностью прочитать
   `FRONTEND_GUIDELINES.md`.
3. Прочитать `INFRASTRUCTURE_INSTRUCTIONS.md`.
4. Прочитать `services.yaml`.
5. Изучить существующий проект, если он уже создан:
   - `README.md`;
   - `pyproject.toml`;
   - `compose.yaml` / `docker-compose.yml`;
   - `package.json`, если есть frontend;
   - дерево исходного кода;
   - tests;
   - migrations;
   - CI configuration;
   - project-specific architecture docs.
6. Определить границы приложения и ответственности компонентов.
7. Определить shared и project-specific dependencies.
8. Определить application use-cases, ports/interfaces и composition root.
9. Определить persistence и transaction boundaries.
10. Определить configuration model и environment variables.
11. Если есть frontend — определить template/CSS/JS structure в соответствии с
    `FRONTEND_GUIDELINES.md`.
12. Только после этого предлагать структуру файлов и реализацию.

LLM MUST NOT начинать с генерации большого количества файлов, пока не определены
границы системы и зависимости.

---

## 2. Приоритет правил

Использовать следующий порядок:

```text
явные требования пользователя
        ↓
project-specific требования и ограничения
        ↓
ENGINEERING_GUIDELINES.md
        ↓
FRONTEND_GUIDELINES.md — если затрагивается frontend
        ↓
INFRASTRUCTURE_INSTRUCTIONS.md
        ↓
services.yaml
        ↓
разумные framework defaults
```

Project-specific решение MAY отклоняться от общего guideline только если:

1. для отклонения есть техническая причина;
2. причина явно названа;
3. решение не создаёт скрытого архитектурного противоречия.

Если существующий код противоречит instruction bundle, LLM MUST:

1. указать противоречие;
2. объяснить влияние;
3. предложить способ привести проект к единому стандарту;
4. не распространять противоречивый pattern на новый код без объяснения.

---

## 3. Обязательное представление файлов

Когда LLM показывает создаваемый или изменяемый файл, он MUST явно указывать
его **относительный путь от корня проекта**.

Предпочтительный формат:

```text
# app/application/use_cases/analyze_document.py
```

```python
# app/application/use_cases/analyze_document.py

...
```

Для файлов, где `#` не является допустимым комментарием, путь указывается
непосредственно перед code block:

```text
Файл: app/web/templates/index.html
```

Нельзя показывать несколько безымянных code blocks, если непонятно, в какие
файлы их сохранять.

Если пользователь просит полный файл, LLM MUST показывать полный актуальный
контент файла, а не только fragment/diff.

---

## 4. Frontend preflight

Если проект или задача затрагивает frontend, LLM MUST до генерации или
изменения frontend-кода:

1. полностью прочитать `FRONTEND_GUIDELINES.md`;
2. изучить существующий base template;
3. изучить основной CSS entrypoint и структуру CSS modules/blocks;
4. определить принятую BEM-схему;
5. изучить common и feature JavaScript;
6. изучить frontend tests/toolchain, если они есть;
7. перед завершением выполнить frontend review checklist из
   `FRONTEND_GUIDELINES.md`.

LLM MUST NOT считать краткое упоминание frontend в
`ENGINEERING_GUIDELINES.md` заменой чтению `FRONTEND_GUIDELINES.md`.

---

## 5. Architecture planning

До реализации LLM SHOULD определить как минимум:

```text
Transport / Delivery
Application
Domain, если он нужен
Infrastructure / Data Access
Composition Root
Background jobs
External integrations
Frontend, если он нужен
Configuration
Observability
Tests
```

Для небольшого проекта физическая структура MAY быть проще, но направление
зависимостей из `ENGINEERING_GUIDELINES.md` сохраняется.

---

## 6. Infrastructure discovery

При создании или изменении `compose.yaml` приложения LLM MUST:

1. прочитать `INFRASTRUCTURE_INSTRUCTIONS.md`;
2. прочитать `services.yaml`;
3. определить, какие dependencies уже являются shared;
4. не дублировать сервисы с `managed_here: true` без документированной причины;
5. проверить фактическое состояние сервера.

`services.yaml` описывает intended topology, но не доказывает, что сервис
сейчас запущен.

---

## 7. Runtime verification

Если есть shell-доступ, выполнить:

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

Если shell-доступа нет, LLM MUST запросить необходимый вывод у пользователя.

LLM MUST NOT утверждать, что сервис запущен, только потому что он описан
в `services.yaml`.

---

## 8. Shared service endpoints

После подтверждения работы shared stack и подключения application container
к `ai-shared` использовать Docker DNS.

Основные shared services:

```text
VLM:       http://shared-vlm:8000/v1
Embedding: http://shared-embedding:8000/v1
n8n:       http://n8n:5678
RabbitMQ:  rabbitmq:5672
```

Transitional runtime:

```text
Ollama: http://ollama:11434
```

Ollama используется только существующими consumers, которые ещё не мигрировали
на shared vLLM.

Если проекту требуется LLM, VLM или embedding, LLM MUST сначала проверить
`services.yaml` и переиспользовать существующий shared endpoint.

LLM MUST NOT автоматически добавлять в business project:

```text
Ollama
vLLM
model server
отдельную копию общей embedding model
отдельную копию общей VLM
```

без явно документированной причины.

Business project MUST зависеть от logical service/model identity, а не от
физической GPU topology.

Например:

```dotenv
SHARED_VLM_BASE_URL=http://shared-vlm:8000/v1
SHARED_VLM_MODEL=shared-vlm

SHARED_EMBEDDING_BASE_URL=http://shared-embedding:8000/v1
SHARED_EMBEDDING_MODEL=shared-embedding
```

Physical model ID, GPU placement, Tensor Parallel, model context и concurrency
являются deployment configuration shared infrastructure.

Не использовать host ports для container-to-container communication.

Внутри Docker `localhost` означает текущий container.

---

## 9. Project-specific by default

Не выносить автоматически в shared:

```text
application PostgreSQL
application Qdrant
application Redis
Celery worker
Celery beat
frontend
backend
project migrations
project background jobs
```

Исключение требует явного архитектурного решения.

---

## 10. Configuration rules

Для Python/Pydantic проектов базовый pattern:

```python
SettingsConfigDict(
    env_file=(".env.example", ".env"),
    case_sensitive=False,
    env_nested_delimiter="__",
)
```

Порядок означает:

```text
.env.example -> committed baseline
.env         -> private/environment-specific override
```

`.env` SHOULD быть sparse.

Добавление новой non-secret настройки SHOULD требовать изменения
configuration schema и `.env.example`, но не всех существующих `.env`.

Для Docker Compose действует отдельное правило:
Compose не обязан автоматически читать `.env.example`.

Поэтому Compose MUST иметь безопасные defaults для non-secret values:

```yaml
ports:
  - "${API_BIND_IP:-127.0.0.1}:${API_HOST_PORT:-8000}:8000"
```

А обязательные secrets SHOULD быть required:

```yaml
environment:
  SECRET_KEY: ${SECRET_KEY:?SECRET_KEY must be set}
```

---

## 11. Output requirements

При предложении архитектурных изменений LLM SHOULD явно указать:

- какие files создаются или меняются;
- относительный путь каждого файла;
- какие application/domain boundaries вводятся;
- какие interfaces/ports используются;
- где собираются concrete dependencies;
- где проходит transaction boundary;
- какие services остаются shared;
- какие services принадлежат проекту;
- какие networks используются;
- какие host ports публикуются и зачем;
- какие volumes создаются;
- какие environment variables нужны;
- какие secrets обязательны;
- какие tests нужны;
- если затронут frontend — как соблюдены BEM, CSS/JS structure, semantic HTML,
  accessibility и frontend checklist;
- как проверить configuration;
- как проверить runtime после запуска.

При удалённой пошаговой настройке сервера команды SHOULD даваться небольшими
проверяемыми блоками, если результат первых команд влияет на дальнейшие шаги.
