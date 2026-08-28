# Engineering Guidelines: Architecture, Code Style, Frontend and Configuration

> **Назначение:** единый набор инженерных правил для проектов, которые проектируются или сопровождаются с использованием `shared-infrastructure`.
>
> Документ предназначен как для разработчиков, так и для LLM / AI coding agents.
>
> Ключевые слова:
>
> - **MUST** — обязательное правило;
> - **SHOULD** — правило по умолчанию, отклонение требует понятной причины;
> - **MAY** — допустимый вариант.

---

## 1. Главный принцип

Архитектура проекта должна обеспечивать:

- явные границы ответственности;
- направление зависимостей от внешних деталей к бизнес-логике, а не наоборот;
- заменяемость инфраструктурных реализаций;
- тестируемость use-case'ов без реальной БД, HTTP, очередей, файлового хранилища или LLM;
- минимальную связанность между слоями;
- единообразие структуры между проектами;
- отсутствие бизнес-логики в transport/UI и infrastructure слоях;
- отсутствие секретов в Git;
- предсказуемое подключение shared infrastructure.

LLM MUST сначала определить границы приложения и зависимости, а уже потом предлагать структуру файлов или код.

---

# Часть I. Архитектура backend

## 2. Рекомендуемые слои

Для нетривиального backend-проекта SHOULD использоваться следующая логическая модель:

```text
┌───────────────────────────────────────┐
│ Transport / Delivery                 │
│ FastAPI / Django / CLI / worker      │
└──────────────────┬────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────┐
│ Application                           │
│ use-cases / orchestration / DTO       │
│ ports / interfaces                    │
└──────────────────┬────────────────────┘
                   │
                   ▼
┌───────────────────────────────────────┐
│ Domain                                │
│ entities / value objects / rules      │
│ domain exceptions                     │
└───────────────────────────────────────┘

Infrastructure ───────implements──────► Application ports
DB / MinIO / RabbitMQ / Ollama / SMTP / external APIs
```

Допустим более простой проект с тремя физическими слоями:

```text
transport -> services/use_cases -> repositories
```

Но даже в таком проекте MUST сохраняться то же направление зависимостей.

### 2.0. Обязательное указание относительного пути файла

При создании, изменении или демонстрации файла MUST быть явно указан его
относительный путь от корня проекта.

Для файлов, синтаксис которых позволяет комментарий, относительный путь
SHOULD также указываться первой строкой файла.

Пример Python:

```python
# app/application/use_cases/analyze_document.py

from __future__ import annotations
```
Пример JavaScript:
```
// app/web/static/js/analysis.js
...
```
Пример CSS:
```
/* app/web/static/css/blocks/_analysis.css */
...
```

### 2.1. Transport / Delivery

Примеры:

```text
api/
views/
web/
cli/
workers/
```

Transport отвечает за:

- HTTP routing;
- parsing запроса;
- transport-level validation;
- authentication / authorization context;
- cookies / headers / CSRF;
- mapping входных схем в command/DTO;
- вызов application use-case;
- преобразование результата или исключения в HTTP/HTML/CLI response.

Transport MUST NOT:

- выполнять SQL;
- содержать сложные бизнес-правила;
- выбирать алгоритм бизнес-операции;
- самостоятельно создавать глубокий граф зависимостей;
- дублировать одну и ту же бизнес-логику между HTML route и API route.

Если одна операция нужна HTML странице и API:

```text
HTML router ─┐
             ├──> same application use-case
API router ──┘
```

---

## 3. Application layer

Application layer описывает **что система делает**, но не **какой конкретно драйвер это выполняет**.

Примеры:

```text
application/
├── dto/
├── ports/
├── services/
└── use_cases/
```

Use-case SHOULD соответствовать одной законченной пользовательской или системной операции:

```text
UploadDocument
AnalyzeContract
CreateUser
CommitReview
GenerateReport
```

Application layer MAY:

- оркестрировать несколько репозиториев;
- применять правила процесса;
- обращаться к портам внешних систем;
- формировать application DTO;
- управлять атомарностью use-case;
- инициировать domain operations.

Application layer MUST NOT зависеть от:

- FastAPI `Request`, `Response`, `HTTPException`;
- Jinja2;
- конкретного RabbitMQ/MinIO/Ollama клиента;
- конкретного SMTP клиента;
- HTTP framework-specific объектов;
- CSS/JS/UI;
- прямых SQL-запросов.

---

## 4. Domain layer

Domain — наиболее независимая часть проекта.

В domain SHOULD находиться:

- entities;
- enums;
- value objects;
- чистые бизнес-правила;
- domain exceptions;
- вычисления, не требующие инфраструктуры.

Domain MUST NOT импортировать:

```text
FastAPI
Django HTTP layer
SQLAlchemy session
Celery
RabbitMQ client
MinIO client
Ollama client
requests/httpx
Jinja2
```

ORM-модели MAY быть отделены от domain-моделей в более строгой архитектуре.

Для небольших CRUD-проектов допустимо упростить модель, но бизнес-правила всё равно не должны зависеть от HTTP.

---

## 5. Infrastructure layer

Infrastructure содержит конкретные технические реализации:

```text
infrastructure/
├── database/
│   ├── models/
│   ├── repositories/
│   └── migrations/
├── messaging/
├── object_storage/
├── llm/
├── embeddings/
├── vector_store/
├── email/
└── external_api/
```

Infrastructure MAY зависеть от сторонних библиотек и SDK.

Infrastructure SHOULD реализовывать интерфейсы, объявленные со стороны application/domain.

Пример:

```python
# application/ports/object_storage.py

from typing import BinaryIO, Protocol


class ObjectStorage(Protocol):
    async def put(
        self,
        *,
        object_name: str,
        data: BinaryIO,
        size: int,
    ) -> None: ...

    async def delete(
        self,
        *,
        object_name: str,
    ) -> None: ...
```

```python
# infrastructure/object_storage/minio.py

class MinioObjectStorage:
    ...
```

Application знает `ObjectStorage`.

Application не должен быть обязан знать `MinioObjectStorage`.

---

# Часть II. SOLID и Dependency Injection

## 6. Dependency Inversion — обязательное правило

Высокоуровневая бизнес-логика MUST зависеть от контрактов, а не от конкретных реализаций.

Хорошо:

```python
class ReviewUseCase:
    def __init__(
        self,
        *,
        item_repository: ItemRepository,
        label_repository: LabelRepository,
    ) -> None:
        self._item_repository = item_repository
        self._label_repository = label_repository
```

Плохо:

```python
class ReviewUseCase:
    def __init__(self) -> None:
        self._item_repository = SqlAlchemyItemRepository()
```

И особенно плохо:

```python
async def execute(...):
    repository = SqlAlchemyItemRepository(...)
    client = Minio(...)
    llm = OllamaClient(...)
```

Use-case не должен сам собирать свою инфраструктуру.

---

## 7. Где объявлять интерфейсы

Интерфейс SHOULD находиться у потребителя зависимости.

Рекомендуется:

```text
application/ports/
```

или для простой структуры:

```text
services/interfaces/
repositories/interfaces/
```

Не рекомендуется помещать интерфейс только рядом с конкретной реализацией, если из-за этого application вынужден импортировать infrastructure.

### 7.1. Маленькие интерфейсы

Соблюдаем Interface Segregation Principle.

Плохо:

```python
class Repository(Protocol):
    async def get_user(...): ...
    async def save_user(...): ...
    async def get_document(...): ...
    async def save_document(...): ...
    async def search_vectors(...): ...
    async def send_email(...): ...
```

Хорошо:

```text
UserRepository
DocumentRepository
VectorSearch
EmailSender
ObjectStorage
TaskPublisher
```

Интерфейс должен описывать одну связанную ответственность.

---

## 8. Composition Root

Создание конкретных реализаций MUST быть сосредоточено в явной точке сборки зависимостей.

Для FastAPI рекомендуется:

```text
api/dependencies.py
core/container.py
core/dependencies.py
bootstrap.py
```

Роутер SHOULD получать уже собранный use-case:

```python
@router.post("/documents")
async def upload_document(
    command: UploadCommand,
    use_case: Annotated[
        UploadDocumentUseCase,
        Depends(get_upload_document_use_case),
    ],
):
    return await use_case.execute(command)
```

Плохо создавать набор репозиториев в каждом endpoint:

```python
@router.post("/documents")
async def upload_document(session=Depends(get_session)):
    document_repo = DocumentRepository(session)
    user_repo = UserRepository(session)
    storage = MinioStorage(...)
    service = UploadService(
        document_repo=document_repo,
        user_repo=user_repo,
        storage=storage,
    )
```

Допустимо, что composition root использует framework-specific DI.

Application layer при этом остаётся framework-agnostic.

---

## 9. SOLID в практических правилах

### S — Single Responsibility

Класс или модуль SHOULD иметь одну основную причину для изменения.

Признаки нарушения:

- один service одновременно валидирует файл, делает SQL, вызывает LLM и строит HTTP response;
- один JS-файл содержит API client, работу с DOM, бизнес-state и форматирование всех страниц;
- один CSS-файл содержит весь проект.

### O — Open/Closed

Новая инфраструктурная реализация SHOULD подключаться без переписывания use-case.

Пример:

```text
ObjectStorage
├── MinioObjectStorage
└── S3ObjectStorage
```

### L — Liskov Substitution

Fake/mock implementation MUST вести себя в рамках того же контракта.

Если fake нельзя подставить вместо production adapter без изменения use-case, контракт спроектирован плохо.

### I — Interface Segregation

Не создавать «god interface».

Порт должен быть минимальным и предметным.

### D — Dependency Inversion

Application зависит от abstraction.

Infrastructure зависит от application contract.

Не наоборот.

---

# Часть III. Работа с БД

## 10. SQLAlchemy только в Data Access / Infrastructure

Правило SHOULD формулироваться не как привязка к имени папки `src/crud`, а как архитектурная граница.

SQLAlchemy queries MUST находиться только в:

```text
infrastructure/database/repositories/
```

или, в упрощённом проекте:

```text
src/crud/
src/repositories/
```

Разрешено:

```python
select(...)
insert(...)
update(...)
delete(...)
session.execute(...)
func.count(...)
```

только внутри data-access/infrastructure слоя.

Запрещено в:

```text
api/
views/
application/
services/
domain/
```

Исключения:

- Alembic migrations;
- административные одноразовые scripts;
- maintenance/migration utilities.

Исключение MUST быть очевидно по расположению файла.

---

## 11. Repository contract

Repository должен выражать бизнес-нужную операцию, а не просто прятать SQLAlchemy API.

Хорошо:

```python
class DocumentRepository(Protocol):
    async def get_by_id(
        self,
        tenant_id: UUID,
        document_id: UUID,
    ) -> Document | None: ...

    async def add(
        self,
        document: Document,
    ) -> None: ...
```

Не стоит делать абстракцию вида:

```python
async def execute(self, statement: Any) -> Any:
    ...
```

Такая абстракция фактически протаскивает SQL наружу.

---

## 12. N+1 и загрузка данных

Repository layer отвечает за стратегию загрузки ORM-данных.

Transport и application не должны исправлять N+1 вручную.

Для SQLAlchemy использовать осознанно:

```text
selectinload
joinedload
contains_eager
```

Выбор зависит от cardinality и объёма данных.

Любая новая выборка, которая проходит по связанным объектам в цикле, SHOULD проверяться на N+1.

---

# Часть IV. Транзакции и Unit of Work

## 13. Граница транзакции

Не фиксируем правило «транзакцию всегда открывает HTTP router».

HTTP-запрос и бизнес-транзакция — разные понятия.

Граница атомарности SHOULD соответствовать application use-case.

Пример:

```text
POST /document
    ↓
UploadDocumentUseCase
    ↓
атомарно:
    document
    analysis_job
    outbox_event
```

Router не обязан знать, какие именно записи должны быть атомарны.

---

## 14. Рекомендуемый Unit of Work

Для сложных операций SHOULD использоваться Unit of Work.

```python
class UnitOfWork(Protocol):
    documents: DocumentRepository
    analyses: AnalysisRepository

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, *args: object) -> None: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
```

Use-case:

```python
class UploadDocumentUseCase:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        storage: ObjectStorage,
    ) -> None:
        self._uow = uow
        self._storage = storage

    async def execute(self, command: UploadCommand) -> UploadResult:
        async with self._uow:
            ...
            await self._uow.documents.add(document)
            await self._uow.analyses.add(job)
            await self._uow.commit()

        return result
```

Это даёт:

- правильную границу атомарности;
- тестируемость;
- отсутствие SQLAlchemy в application;
- возможность заменить persistence mechanism.

Для маленького проекта допускается упрощённая схема, но commit/rollback не должен случайно расползаться по множеству репозиториев.

---

# Часть V. DTO, validation и exceptions

## 15. Pydantic

Pydantic SHOULD использоваться на внешних границах:

- API request;
- API response;
- settings;
- parsing external payload.

Application/domain не обязаны зависеть от Pydantic.

Для внутренних DTO допустимы:

```text
dataclass
NamedTuple
plain typed class
Pydantic model — если это осознанно упрощает проект
```

---

## 16. Validation

Разделять:

### Transport validation

Пример:

```text
поле обязательно
строка не длиннее N
UUID корректный
файл имеет допустимый MIME type
```

### Domain/Application validation

Пример:

```text
документ нельзя анализировать повторно в текущем статусе
пользователь не может изменить объект другого tenant
операция недоступна после закрытия сессии
```

Бизнес-правила MUST NOT существовать только в Pydantic validator HTTP-схемы.

---

## 17. Exceptions

Domain/application SHOULD выбрасывать предметные исключения:

```text
DocumentNotFoundError
AnalysisAlreadyRunningError
PermissionDeniedError
InvalidReviewStateError
```

Transport переводит их в:

```text
404
409
403
422
```

Application MUST NOT выбрасывать `HTTPException`.

---

# Часть VI. Python code style

## 18. Общий стиль

Python code MUST соответствовать PEP 8.

Имена:

```text
functions / variables / modules -> snake_case
classes                       -> PascalCase
constants                     -> UPPER_CASE
```

Публичные application/repository methods MUST иметь type hints.

`Any` SHOULD использоваться только на внешней или динамической границе и не должен распространяться по application layer.

---

## 19. Размер функций и классов

Не вводится искусственный жёсткий лимит строк.

Вместо него правило:

> Если функция выполняет несколько логически независимых этапов, этапы SHOULD быть вынесены в отдельные функции или объекты.

Признаки необходимости декомпозиции:

- много уровней вложенности;
- несколько разных `try/except`;
- функция одновременно занимается I/O и бизнес-вычислениями;
- имя функции невозможно сформулировать одним действием;
- тест требует слишком много несвязанных fixture.

---

## 20. Imports

Импорты SHOULD группироваться:

```python
# standard library

# third party

# local application
```

Запрещены wildcard imports:

```python
from module import *
```

Циклические импорты SHOULD исправляться архитектурой, а не массовым переносом imports внутрь функций.

---

## 21. Mutable defaults

Запрещено:

```python
def foo(items=[]):
    ...
```

Использовать:

```python
def foo(items: list[str] | None = None):
    items = [] if items is None else items
```

Для dataclass:

```python
field(default_factory=list)
```

---

## 22. Async code

В async path MUST отсутствовать блокирующий I/O без явного offloading.

Проверять:

```text
requests
subprocess.run
time.sleep
sync DB drivers
тяжёлые CPU операции
```

Вместо этого:

```text
httpx.AsyncClient
async DB driver
asyncio.sleep
worker/thread/process pool для CPU/blocking
```

---

# Часть VII. Logging

## 23. Структурированные события

Логи SHOULD иметь стабильное имя события.

Пример:

```python
logger.info(
    "review_committed",
    extra={
        "event": "review_committed",
        "session_id": str(session_id),
        "rows": len(rows),
    },
)
```

Если выбран JSON logger — формат должен быть единым для проекта.

Рекомендуемые поля:

```text
event
request_id / correlation_id
user_id (если допустимо)
tenant_id
job_id / task_id
duration_ms
status
```

---

## 24. Что нельзя логировать

MUST NOT попадать в logs:

```text
password
SECRET_KEY
JWT
refresh token
session cookie
SMTP password
RabbitMQ password
API key
полный Authorization header
полный пользовательский документ без явной необходимости
```

---

# Часть VIII. Конфигурация: `.env.example` + `.env`

## 25. Главный принцип конфигурации

Используем модель:

```text
.env.example = committed baseline + полный каталог переменных
.env         = sparse local/private override
```

`.env.example` хранится в Git.

`.env` MUST находиться в `.gitignore`.

`.env` MUST NOT копировать весь `.env.example`.

---

## 26. Что хранить в `.env.example`

В `.env.example` SHOULD находиться:

- все поддерживаемые имена переменных;
- безопасные non-secret defaults;
- dev defaults;
- порты;
- hostnames;
- feature flags с безопасным значением;
- параметры batching/timeouts;
- пустые или явно фиктивные placeholders для secret values;
- комментарии к нетривиальным параметрам.

Пример:

```dotenv
APP_ENV=dev
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO

POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=app

SECRET_KEY=CHANGE_ME
POSTGRES_PASSWORD=CHANGE_ME
```

---

## 27. Что хранить в `.env`

`.env` SHOULD содержать только значения, которые нельзя или не следует фиксировать в Git.

Обычно это:

```dotenv
SECRET_KEY=...
POSTGRES_PASSWORD=...
SMTP_PASSWORD=...
RABBITMQ_PASSWORD=...
APP_ENV=prod
```

Допустимы и другие environment-specific overrides, если конкретное окружение действительно отличается от baseline.

Главное правило:

> Добавление обычной non-secret настройки в проект НЕ должно требовать ручного дописывания этой настройки в каждый существующий `.env`.

---

## 28. Pydantic Settings: обязательный паттерн

Для Python-проектов на `pydantic-settings` SHOULD использоваться layered loading:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(
            ".env.example",
            ".env",
        ),
        env_nested_delimiter="__",
        case_sensitive=False,
    )
```

Порядок:

```text
.env.example
      ↓
.env overrides only changed/private values
      ↓
process environment may override deployment values
```

Таким образом новая обычная переменная добавляется:

1. в Settings model;
2. в `.env.example`;

и не требует массового обновления `.env`.

---

## 29. Важное правило именования

Стандартное имя:

```text
.env.example
```

Опечатки вроде:

```text
.env.examlpe
.env_example
example.env
```

не использовать.

Инструменты, документация, CI и разработчики должны видеть одно каноническое имя.

---

## 30. Docker Compose и env-файлы

Нельзя предполагать, что Docker Compose автоматически применит тот же механизм, что Pydantic.

Для Compose-only проекта MUST быть явно определён один из вариантов:

### Вариант A — defaults в Compose

```yaml
environment:
  LOG_LEVEL: ${LOG_LEVEL:-INFO}
  DB_PASSWORD: ${DB_PASSWORD:?DB_PASSWORD must be set}
```

Тогда `.env` может быть sparse и содержать только обязательные secrets/overrides.

### Вариант B — явное layered env loading

Если `.env.example` должен быть реальным runtime baseline, все команды/скрипты должны одинаково использовать layered env files.

Нельзя иметь ситуацию, когда:

```text
локальный script читает два env-файла,
а docker compose up читает только один.
```

Поведение MUST быть одинаковым для:

```text
bootstrap
config validation
pull
up
test
CI
production deployment
```

---

## 31. Secrets в production

`.env` допустим как server-local secret override для небольшого deployment.

Если окружение предоставляет secret manager / Docker secrets / Kubernetes Secrets / CI secret storage, production SHOULD использовать их вместо хранения секретов в обычном файле.

Application code при этом не меняется: источник конфигурации остаётся внешним.

---

# Часть IX. Frontend architecture

## 32. Главный frontend-принцип

Frontend должен быть разделён по ответственности так же, как backend.

Нельзя превращать:

```text
style.css
app.js
index.html
```

в три монолитных файла со всем приложением.

---

## 33. HTML semantics

Использовать семантические элементы по назначению:

```html
<header>
<nav>
<main>
<section>
<article>
<aside>
<footer>
<form>
<label>
<button>
```

Не использовать `<div>` только потому, что он привычнее, если существует подходящий semantic element.

### Кнопка и ссылка

Действие:

```html
<button type="button">
```

Навигация:

```html
<a href="/profile">
```

Не делать ссылку через `onclick` на `<div>`.

---

## 34. Базовая HTML структура

Рекомендуется base template:

```html
<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >

    <link
        rel="stylesheet"
        href="/static/css/style.css"
    >

    <script
        src="/static/js/api.js"
        defer
    ></script>

    <script
        src="/static/js/app.js"
        defer
    ></script>

    {% block scripts %}{% endblock %}
</head>

<body class="page">
    <header class="header">
        ...
    </header>

    <main class="page__content">
        {% block content %}{% endblock %}
    </main>

    <footer class="footer">
        ...
    </footer>
</body>
</html>
```

Common scripts идут в base template.

Feature/page entry scripts подключаются через `scripts` block.

---

## 35. JavaScript scripts

Для обычных JS scripts MUST использоваться:

```html
<script
    src="/static/js/profile.js"
    defer
></script>
```

Scripts SHOULD размещаться в `<head>`.

Причины:

- HTML не блокируется выполнением script;
- порядок исполнения предсказуем;
- зависимости подключены централизованно;
- template не засоряется script tags внизу `body`.

Для `type="module"` браузер уже использует deferred semantics, но структура подключения всё равно должна оставаться централизованной.

---

## 36. Запрещённый inline JavaScript

Не использовать:

```html
<button onclick="save()">Save</button>
```

Использовать stable JS hook:

```html
<button
    type="button"
    data-save-document
>
    Save
</button>
```

```javascript
const saveButton = document.querySelector("[data-save-document]");

saveButton?.addEventListener("click", handleSave);
```

---

## 37. CSS entrypoint

HTML SHOULD подключать только один основной CSS entrypoint:

```html
<link
    rel="stylesheet"
    href="/static/css/style.css"
>
```

`style.css` — агрегатор imports.

Он SHOULD содержать минимум собственных selectors.

Пример:

```css
/* tokens / base */
@import url("./variables.css");
@import url("./global.css");

/* layout */
@import url("./blocks/page.css");
@import url("./blocks/header.css");
@import url("./blocks/sidebar.css");

/* reusable UI */
@import url("./blocks/button.css");
@import url("./blocks/form.css");
@import url("./blocks/modal.css");
@import url("./blocks/table.css");

/* feature blocks */
@import url("./blocks/upload.css");
@import url("./blocks/analysis-result.css");
@import url("./blocks/knowledge-base.css");

/* responsive overrides */
@import url("./responsive.css");
```

---

## 38. CSS modules — блоки, а не страницы

Целевая структура:

```text
static/
└── css/
    ├── style.css
    ├── variables.css
    ├── global.css
    ├── responsive.css
    └── blocks/
        ├── page.css
        ├── header.css
        ├── sidebar.css
        ├── button.css
        ├── form.css
        ├── modal.css
        ├── table.css
        ├── upload.css
        ├── analysis-result.css
        └── knowledge-base.css
```

Файл SHOULD соответствовать:

- reusable block;
- layout block;
- cohesive feature section;
- shared UI component.

Не создавать по умолчанию:

```text
index.css
profile-page.css
admin-page-all.css
everything.css
```

если файл просто собирает несвязанные стили всей страницы.

Page-specific stylesheet допустим только когда страница сама является отдельной изолированной feature area и файл всё равно сохраняет одну ответственность.

---

## 39. BEM

Базовое именование:

```text
block
block__element
block--modifier
block__element--modifier
```

Пример:

```html
<section class="analysis-card analysis-card--warning">
    <h2 class="analysis-card__title">
        Риск
    </h2>

    <p class="analysis-card__description">
        ...
    </p>
</section>
```

Не рекомендуется:

```css
#main .content div.item span.red.active
```

Предпочтительно:

```css
.analysis-card__description
```

---

## 40. State classes

Для UI-state MAY использоваться отдельные классы:

```text
is-hidden
is-loading
is-disabled
has-error
```

State class не заменяет BEM block, а описывает временное состояние.

---

## 41. CSS variables

Повторяющиеся design values SHOULD быть tokens:

```css
:root {
    --color-background: #ffffff;
    --color-text: #1f2937;
    --space-1: 0.25rem;
    --space-2: 0.5rem;
    --radius-md: 0.5rem;
}
```

Не дублировать один и тот же magic color/spacing десятки раз.

---

## 42. Inline CSS

Не использовать:

```html
<div style="margin-top: 17px; color: red;">
```

Исключение — действительно динамическое значение, которое невозможно разумно выразить классом/custom property.

Не размещать большие `<style>` blocks в templates.

---

# Часть X. Frontend JavaScript

## 43. Разделение JS

Рекомендуется:

```text
static/js/
├── api.js
├── app.js
├── auth.js
├── components/
│   ├── modal.js
│   └── notifications.js
└── features/
    ├── upload.js
    ├── analysis.js
    ├── history.js
    └── knowledge-base.js
```

`api.js`:

- HTTP requests;
- common error parsing;
- auth headers/cookies policy.

Feature module:

- DOM orchestration конкретной feature;
- feature state;
- event handlers.

Не смешивать все страницы в одном огромном `app.js`.

---

## 44. JS hooks отдельно от CSS styling

Для JS предпочтительно использовать:

```text
data-*
```

Пример:

```html
<button
    class="button button--primary"
    data-analysis-start
>
    Анализировать
</button>
```

CSS использует:

```text
.button
.button--primary
```

JS использует:

```text
[data-analysis-start]
```

Так визуальный refactoring не ломает JavaScript.

---

## 45. DOM safety

Не вставлять недоверенный пользовательский текст через `innerHTML`.

Предпочитать:

```javascript
element.textContent = value;
```

Если HTML действительно нужен, входные данные MUST быть sanitised подходящим инструментом.

---

## 46. Accessibility

Frontend SHOULD обеспечивать:

- связанный `label` для inputs;
- доступность keyboard navigation;
- `aria-label` для icon-only controls;
- `role="status"` / `aria-live` для динамического статуса, когда это нужно;
- видимый focus;
- корректный `disabled`;
- достаточную semantic structure;
- отсутствие кликабельных `<div>` вместо button/link.

ARIA не должна заменять корректный native semantic element.

---

# Часть XI. Templates

## 47. Base template и partials

Повторяющаяся разметка SHOULD выноситься:

```text
templates/
├── base.html
├── components/
│   ├── header.html
│   ├── modal.html
│   └── pagination.html
└── pages/
```

Нельзя копировать одинаковый header/modal/forms markup между страницами.

---

## 48. Templates не содержат business logic

Допустимо:

```jinja2
{% if user.is_authenticated %}
```

Не стоит переносить в Jinja сложные вычисления, выбор бизнес-стратегии или data-access.

Template получает подготовленный context/DTO.

---

# Часть XII. API и frontend

## 49. API layer

JS SHOULD обращаться к backend через выделенный API helper.

Плохо:

```javascript
fetch(...)
```

с разной обработкой ошибок в двадцати обработчиках.

Лучше:

```javascript
await apiRequest("/api/v1/documents", {
    method: "POST",
    body: formData,
});
```

Common API client должен централизовать:

- base URL;
- headers;
- credentials;
- JSON parsing;
- standard error shape.

---

# Часть XIII. Testing

## 50. Test pyramid по слоям

### Unit tests

Use-case/domain tests SHOULD использовать fakes:

```text
FakeDocumentRepository
FakeObjectStorage
FakeTaskPublisher
FakeClock
```

Они не требуют Docker или реальной БД.

### Integration tests

Проверяют:

- SQLAlchemy repositories;
- PostgreSQL;
- Qdrant;
- RabbitMQ integration;
- object storage adapter.

### API tests

Проверяют transport contract:

```text
status code
request validation
response schema
auth
error mapping
```

---

## 51. Архитектурные тесты

Для крупных проектов SHOULD добавить проверки границ.

Например:

```text
domain не импортирует infrastructure
application не импортирует FastAPI
services не содержат SQLAlchemy queries
```

Это можно проверять отдельным test/lint script.

---

# Часть XIV. Linting и quality gates

## 52. Перед merge

Python-проект SHOULD иметь автоматические проверки:

```text
ruff check
ruff format --check
pytest
type checking — если принят в проекте
```

Frontend при наличии toolchain SHOULD иметь аналогичные lint/format checks.

Не вводить инструмент только ради формальности; выбранные checks должны запускаться в CI.

---

# Часть XV. Правила для LLM / AI coding agents

## 53. Перед архитектурным планом

LLM MUST:

1. прочитать этот документ;
2. прочитать project-specific README/architecture docs;
3. прочитать `docs/LLM_CONTEXT.md`;
4. проверить `docs/services.yaml`, если затрагивается shared infrastructure;
5. определить shared vs project-specific dependencies;
6. определить application boundaries;
7. определить interfaces/ports;
8. только после этого предлагать файлы и код.

---

## 54. LLM не должен механически копировать старый проект

Reference project — пример, а не источник истины.

Если старый проект:

- нарушает dependency direction;
- создаёт infrastructure внутри use-case;
- передаёт ORM session слишком глубоко;
- имеет монолитный JS/CSS;
- содержит устаревший workaround;

LLM SHOULD следовать текущим правилам, а не повторять legacy pattern.

---

## 55. При изменении архитектуры

LLM MUST явно указать:

- какие слои затронуты;
- какие новые ports/interfaces появились;
- где composition root;
- где transaction boundary;
- где persistence;
- какие shared services используются;
- какие environment variables добавлены;
- нужны ли изменения `.env.example`;
- нужен ли новый secret/override в `.env`;
- какие тесты подтверждают изменение.

---

# Часть XVI. Что взять из существующих проектов

## 56. Паттерны, которые стоит сохранить

Из существующего подхода полезно стандартизировать:

### Backend

- application ports через `Protocol`;
- отдельные infrastructure adapters;
- отдельный DI/composition module;
- thin HTTP routers;
- DTO/use-case разделение;
- domain exceptions;
- repository isolation.

### Frontend

- один `style.css` entrypoint;
- CSS imports из отдельных modules;
- BEM;
- semantic HTML;
- `header/main/footer/section/aside`;
- scripts в `<head>` с `defer`;
- base template;
- page/feature scripts через template block;
- `data-*` hooks;
- accessibility attributes.

### Configuration

- baseline `.env.example`;
- sparse `.env`;
- Pydantic layered loading;
- secrets не попадают в Git.

---

## 57. Что не стоит копировать 1:1

Даже хороший reference implementation не становится абсолютным стандартом.

В новых проектах SHOULD улучшать следующие места:

1. **Не создавать repository implementations внутри use-case по умолчанию.**
   Использовать composition root.

2. **Не позволять application use-case импортировать SQLAlchemy/infrastructure**, если проект уже достаточно сложный для ports + adapters.

3. **Не считать HTTP router владельцем transaction boundary.**
   Использовать application-level UoW/use-case boundary.

4. **Не раздувать page-specific CSS и JS.**
   Выделять cohesive blocks/features.

5. **Не дублировать все `.env.example` значения в `.env`.**
   `.env` остаётся минимальным override.

---

# Часть XVII. PR Checklist

## 58. Backend

- [ ] Transport не содержит SQL.
- [ ] Transport не содержит бизнес-логику.
- [ ] Application не зависит от HTTP framework.
- [ ] Domain не зависит от infrastructure.
- [ ] SQLAlchemy находится только в data-access/infrastructure.
- [ ] External systems доступны через ports/interfaces.
- [ ] DI выполняется через constructor/function injection.
- [ ] Concrete adapters собираются в composition root.
- [ ] Нет скрытого создания клиентов/репозиториев внутри use-case.
- [ ] Transaction boundary соответствует use-case.
- [ ] Для сложной атомарности используется UoW.
- [ ] Исключения application/domain не являются `HTTPException`.
- [ ] Public methods типизированы.
- [ ] Logs не содержат secrets.

---

## 59. Configuration

- [ ] Новые non-secret settings добавлены в `.env.example`.
- [ ] `.env` не был расширен без необходимости.
- [ ] `.env` находится в `.gitignore`.
- [ ] В `.env.example` нет реальных secrets.
- [ ] Pydantic читает `.env.example` перед `.env`.
- [ ] Для Docker Compose поведение env precedence явно определено.
- [ ] Production secrets не печатаются в logs/diagnostics.

---

## 60. Frontend

- [ ] Используется semantic HTML.
- [ ] Повторяемая разметка вынесена в base/partials.
- [ ] BEM соблюдён.
- [ ] Нет больших inline `<style>`.
- [ ] Нет inline `onclick`.
- [ ] HTML подключает основной `style.css`.
- [ ] `style.css` агрегирует block/module CSS.
- [ ] CSS файлы соответствуют blocks/features, а не случайным страницам.
- [ ] Scripts подключены в `<head>` с `defer` или `type="module"`.
- [ ] Common JS и feature JS разделены.
- [ ] JS использует `data-*` hooks там, где это разумно.
- [ ] Dynamic status и controls доступны с keyboard/screen reader.
- [ ] Недоверенные данные не вставляются через raw `innerHTML`.

---

## 61. Tests

- [ ] Unit tests покрывают business use-case.
- [ ] Infrastructure adapters имеют integration tests при необходимости.
- [ ] API contract проверен.
- [ ] Ошибочные ветки проверены.
- [ ] Lint проходит.
- [ ] Форматирование проходит.
- [ ] Нет архитектурного обхода слоя «ради быстрого фикса».

---

# Часть XVIII. Интеграция правил в `shared-infrastructure`

## 62. Новый источник истины

Рекомендуемый путь этого файла:

```text
docs/ENGINEERING_GUIDELINES.md
```

Он должен быть обязательным source of truth для:

```text
architecture planning
backend code generation
frontend code generation
configuration design
DI/SOLID review
PR review
```

---

## 63. `docs/LLM_CONTEXT.md`

`docs/LLM_CONTEXT.md` SHOULD перестать быть только infrastructure entrypoint.

В начале нужен общий порядок:

```text
Перед планированием архитектуры или генерацией кода:

1. Прочитать docs/ENGINEERING_GUIDELINES.md.
2. Прочитать project-specific README/architecture docs.
3. Если затрагивается infrastructure:
   - прочитать docs/INFRASTRUCTURE_INSTRUCTIONS.md;
   - прочитать docs/services.yaml;
   - проверить runtime.
4. Не дублировать shared services.
5. Соблюдать DI/SOLID/frontend/configuration rules.
```

---

## 64. `README.md`

В дереве repository добавить:

```text
docs/ENGINEERING_GUIDELINES.md
```

В «Источники истины» добавить его и для developer, и для LLM.

Раздел `.env` SHOULD быть изменён.

Не:

```bash
cp .env.example .env
```

а:

```bash
cat > .env <<'EOF'
N8N_DB_PASSWORD=...
N8N_ENCRYPTION_KEY=...
RABBITMQ_DEFAULT_PASS=...
EOF
```

То есть `.env` — только private overrides.

---

## 65. `scripts/bootstrap.sh`

Bootstrap SHOULD:

- проверять наличие `.env`, если secrets обязательны;
- проверять только обязательные secret variables;
- НЕ требовать копировать весь `.env.example`;
- НЕ предполагать, что `.env` содержит все non-secret settings;
- валидировать Compose после загрузки sparse overrides.

Пример логики:

```bash
required_vars=(
  N8N_DB_PASSWORD
  N8N_ENCRYPTION_KEY
  RABBITMQ_DEFAULT_PASS
)

for var_name in "${required_vars[@]}"; do
    if [[ -z "${!var_name:-}" ]]; then
        echo "ERROR: ${var_name} is required"
        exit 2
    fi
done
```

---

## 66. `PROJECT_INTEGRATION.md`

В обязательное чтение добавить:

```text
docs/ENGINEERING_GUIDELINES.md
```

Также SHOULD использоваться только фактически существующие repository paths.

Если файл лежит в корне:

```text
PROJECT_INTEGRATION.md
```

README и другие docs не должны ссылаться на:

```text
docs/PROJECT_INTEGRATION.md
```

---

## 67. Repository-wide consistency rule

Любое новое правило MUST сопровождаться проверкой связанных файлов.

Например, добавили новый source of truth:

```text
docs/ENGINEERING_GUIDELINES.md
```

LLM/developer проверяет минимум:

```text
README.md
docs/LLM_CONTEXT.md
PROJECT_INTEGRATION.md
docs/INFRASTRUCTURE_INSTRUCTIONS.md
scripts/
CI
```

если они описывают тот же workflow.

Запрещено добавлять новый обязательный документ и не подключать его из LLM entrypoint / README.

---

# Итоговая модель

```text
                         ┌──────────────────────┐
                         │       Transport      │
                         │ HTTP / HTML / CLI    │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │     Application      │
                         │ use-cases + ports    │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │        Domain        │
                         │ rules + entities     │
                         └──────────────────────┘

Infrastructure implements Application ports:

PostgreSQL / SQLAlchemy
MinIO / S3
RabbitMQ
Ollama
Qdrant
SMTP
external APIs

Frontend:

base.html
   ├── style.css
   │     ├── global/tokens
   │     ├── layout blocks
   │     ├── UI blocks
   │     └── feature blocks
   │
   └── JS with defer
         ├── common API/app
         └── feature modules

Configuration:

.env.example  -> committed baseline
       +
.env          -> sparse private/environment override
```

Главная цель правил — не максимальное количество абстракций, а **правильное направление зависимостей, явные границы ответственности и возможность менять технические детали без переписывания бизнес-логики**.
