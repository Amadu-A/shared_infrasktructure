# Project Integration

Этот файл — короткий указатель для разработчика.

Он **не является отдельным source of truth** и не должен дублировать
инженерные и инфраструктурные правила.

## С чего начинать новый проект

Перед планированием архитектуры или `compose.yaml` передайте LLM папку:

```text
docs/
├── LLM_CONTEXT.md
├── ENGINEERING_GUIDELINES.md
├── INFRASTRUCTURE_INSTRUCTIONS.md
└── services.yaml
```

Главная точка входа:

```text
docs/LLM_CONTEXT.md
```

LLM должна сначала прочитать её и выполнить указанный там порядок.

## Что описывают файлы

```text
docs/LLM_CONTEXT.md
    порядок работы LLM и обязательные проверки

docs/ENGINEERING_GUIDELINES.md
    backend architecture, DI, SOLID, code style,
    frontend, configuration, tests

docs/INFRASTRUCTURE_INSTRUCTIONS.md
    Docker, shared services, networks, ports,
    environment variables, runtime verification

docs/services.yaml
    machine-readable intended topology shared infrastructure
```

## Важное правило

`docs/services.yaml` не доказывает, что service сейчас запущен.

Перед infrastructure changes нужно проверить runtime:

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

Project-specific implementation details SHOULD находиться в документации
самого application repository, а не дублироваться здесь.
