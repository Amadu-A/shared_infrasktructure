#!/usr/bin/env python3
# scripts/switch-vlm.py
"""Переключает VLM через серверный .env только после подтверждения оператора.

Не создаёт override, не скачивает веса и не меняет другие сервисы. Owned override
переносится отдельной операцией без recreate. Закрытые backups остаются на сервере.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path

import deployment as d

ROOT = Path(__file__).resolve().parent.parent
MARKER = "# Managed by scripts/switch-vlm.py. Do not edit manually."
GPU_PAIR = "2,3"

MODELS = {
    "1": {
        "name": "Qwen3.8-27B (текущий принятый deployment-профиль)",
        "id": "Qwen/Qwen3.8-27B",
        "revision": "main",
        "args": None,
        "tool_parser": "qwen3_coder",
    },
    "2": {
        "name": "Qwen3.5-122B-A10B-GPTQ-Int4 (тест)",
        "id": "Qwen/Qwen3.5-122B-A10B-GPTQ-Int4",
        "revision": "30cd92cba9707a9aba09d1e490ed4b66b78e9606",
        "tool_parser": "qwen3_coder",
        "args": [
            "--reasoning-parser", "qwen3",
            "--quantization", "moe_wna16",
        ],
    },
    "3": {
        "name": "Qwen3-VL-235B-A22B-Instruct-AWQ (тест)",
        "id": "QuantTrio/Qwen3-VL-235B-A22B-Instruct-AWQ",
        "revision": "dc4295875e8a6d61e60a7c4ee6406585d9c70e9c",
        "tool_parser": None,  # JSON-in-XML; compatible parser not verified.
        "args": [
            "--reasoning-parser", "qwen3",
        ],
    },
}

# Читает cache в существующем контейнере; отдельный контейнер/GPU не создаётся.
CACHE_PROBE = r'''
import json
from pathlib import Path

models = json.loads(__import__("sys").argv[1])
hub = Path("/root/.cache/huggingface/hub")
result = {}
for model in models:
    repo, revision = model["id"], model["revision"]
    directory = hub / ("models--" + repo.replace("/", "--"))
    rev = revision
    if revision == "main":
        ref = directory / "refs" / "main"
        rev = ref.read_text().strip() if ref.is_file() else ""
    snapshot = directory / "snapshots" / rev if rev else None
    ready = bool(snapshot and (snapshot / "config.json").is_file())
    size = 0
    detail = "нет локального snapshot"
    if ready:
        index = snapshot / "model.safetensors.index.json"
        if index.is_file():
            try:
                weights = json.loads(index.read_text())["weight_map"]
                shards = sorted(set(weights.values()))
                ready = bool(shards) and all(
                    (snapshot / shard).is_file()
                    and (snapshot / shard).stat().st_size > 0
                    for shard in shards
                )
                if ready:
                    size = sum((snapshot / shard).stat().st_size for shard in shards)
                detail = f"{len(shards)} файлов весов"
            except (ValueError, KeyError, OSError):
                ready = False
                detail = "некорректный индекс весов"
        else:
            shards = list(snapshot.glob("*.safetensors"))
            ready = len(shards) == 1 and shards[0].stat().st_size > 0
            if ready:
                size = shards[0].stat().st_size
            detail = "один файл весов" if ready else "не удалось проверить комплект весов"
    result[repo] = {"ready": ready, "size": size, "detail": detail}
result["__cached_repositories__"] = sorted(
    path.name.removeprefix("models--").replace("--", "/", 1)
    for path in hub.glob("models--*") if path.is_dir()
)
print(json.dumps(result))
'''

MANAGED = {
    "--model": "SHARED_VLM_MODEL_ID", "--revision": "SHARED_VLM_MODEL_REVISION",
    "--served-model-name": "SHARED_VLM_MODEL_ALIAS",
    "--tensor-parallel-size": "SHARED_VLM_TP_SIZE",
    "--data-parallel-size": "SHARED_VLM_DP_SIZE",
    "--max-model-len": "SHARED_VLM_MAX_MODEL_LEN",
    "--gpu-memory-utilization": "SHARED_VLM_GPU_MEMORY_UTILIZATION",
    "--max-num-seqs": "SHARED_VLM_MAX_NUM_SEQS",
    "--max-num-queued-reqs": "SHARED_VLM_MAX_NUM_QUEUED_REQS",
    "--max-num-queued-tokens": "SHARED_VLM_MAX_NUM_QUEUED_TOKENS",
}


def tool_calling_enabled(command: list[str], parser: str) -> bool:
    """Проверяет совместное наличие auto tools и принятого парсера."""
    return "--enable-auto-tool-choice" in command and d.value_after(command, "--tool-call-parser") == parser


def settings(service: dict) -> dict[str, str]:
    """Переносит все параметры VLM в dotenv, сохраняя дополнительные флаги."""
    command = list(service["command"])
    values = {}
    for flag, key in {**MANAGED, "--host": "", "--port": ""}.items():
        value = d.value_after(command, flag)
        if not value:
            raise d.DeploymentError("Команда VLM содержит неоднозначный или отсутствующий параметр.")
        position = command.index(flag)
        del command[position:position + 2]
        if key:
            values[key] = value
    values["SHARED_VLM_MODEL_ARGS"] = shlex.join(command)
    values["SHARED_VLM_GPU_DEVICES"] = str(service["environment"]["CUDA_VISIBLE_DEVICES"])
    values["SHARED_VLM_BATCH_INVARIANT"] = str(service["environment"].get("VLLM_BATCH_INVARIANT", "0"))
    return values


def selected_service(key: str, baseline: dict) -> dict:
    """Строит выбор на базе принятой конфигурации, не назначая GPU/TP заново."""
    selected = MODELS[key]
    if selected["tool_parser"] is None:
        raise d.DeploymentError("REQUIRES_OWNER_DECISION: parser этой модели не проверен.")
    service = copy.deepcopy(baseline)
    command = service["command"]
    if key == "1":
        if d.value_after(command, "--model") != selected["id"]:
            raise d.DeploymentError("Профиль прежней 27B не подбирается по тестовой модели; используйте сохранённый backup и инструкцию rollback.")
        return service
    for flag, value in (("--model", selected["id"]), ("--revision", selected["revision"])):
        position = command.index(flag)
        command[position + 1] = value
    if selected["args"] is not None:
        # Существующий тестовый профиль; его запуск требует отдельного подтверждения.
        common = []
        for flag in MANAGED:
            common.extend([flag, d.value_after(command, flag)])
        for flag in ("--host", "--port"):
            common.extend([flag, d.value_after(command, flag)])
        # Контекст, TP/DP и ограничения остаются из явного deployment, а не из menu.
        additional = []
        for position in range(0, len(selected["args"]), 2):
            flag, value = selected["args"][position:position + 2]
            if flag not in MANAGED:
                additional.extend([flag, value])
        service["command"] = common + ["--enable-auto-tool-choice", "--tool-call-parser",
                                        selected["tool_parser"], *additional]
    return service


def matches_model_runtime(command: list[str], selected: dict) -> bool:
    """Проверяет revision, alias, параллелизм и все параметры выбранной модели."""
    expected = selected.get("command")
    if expected is not None:
        return d.normalized_command(command) == d.normalized_command(expected)
    raise d.DeploymentError("Для сравнения нужен полный deployment-профиль, а не название модели.")


def equivalent_configs(left: dict, right: dict) -> bool:
    """Доказывает равенство всего Compose, нормализуя только порядок известных CLI-флагов."""
    a, b = copy.deepcopy(left), copy.deepcopy(right)
    for config in (a, b):
        for name in d.GPU_PLACEMENT:
            service = config.get("services", {}).get(name)
            if service:
                service["command"] = d.normalized_command(service.get("command") or [])
    return a == b


def render_env(original: bytes, values: dict[str, str]) -> bytes:
    """Меняет только управляемые строки; остальные байты dotenv сохраняет."""
    text = original.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    for value in values.values():
        if any(char in value for char in "\r\n'"):
            raise d.DeploymentError("Значение выбора нельзя безопасно записать в dotenv.")
    found = set()
    lines = []
    source_lines = iter(text.splitlines(keepends=True))
    for line in source_lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else ""
        # Не принимать похожую строку внутри чужого multiline secret за assignment.
        if match:
            value_text = line[match.end():].lstrip()
            if value_text[:1] in ("'", '"'):
                quote = value_text[0]
                content = value_text[1:]
                while True:
                    escaped = False
                    closed = False
                    for char in content:
                        if char == quote and not escaped:
                            closed = True
                            break
                        escaped = char == "\\" and not escaped
                    if closed:
                        break
                    continuation = next(source_lines, None)
                    if continuation is None:
                        raise d.DeploymentError("Исходный dotenv содержит незакрытую строку.")
                    line += continuation
                    content = continuation
        if key not in values:
            lines.append(line)
        elif key not in found:
            lines.append(f"{key}='{values[key]}'{newline}")
            found.add(key)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline
    lines.extend(f"{key}='{value}'{newline}" for key, value in values.items() if key not in found)
    return (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + "".join(lines).encode("utf-8")


def atomic_write(path: Path, content: bytes, metadata: os.stat_result) -> None:
    """Атомарно заменяет dotenv, сохраняя владельца и права исходного файла."""
    fd, name = tempfile.mkstemp(prefix=".env.bak-write-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
                current = os.fstat(stream.fileno())
                if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
                    os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def assert_safe(allow_legacy: bool = False) -> Path | None:
    """Блокирует неоднозначные Compose-файлы и неявную перезапись чужой конфигурации."""
    env_path = ROOT / ".env"
    if not env_path.is_file() or env_path.is_symlink():
        raise d.DeploymentError("Нужен обычный серверный .env; симлинк не изменяется.")
    text = env_path.read_text(encoding="utf-8-sig")
    if os.environ.get("COMPOSE_FILE") or re.search(r"(?m)^\s*(?:export\s+)?COMPOSE_FILE\s*=", text):
        raise d.DeploymentError("COMPOSE_FILE требует отдельного разбора перед переключением.")
    legacy = None
    for name in ("compose.override.yaml", "compose.override.yml", "docker-compose.override.yaml",
                 "docker-compose.override.yml"):
        path = ROOT / name
        if path.exists():
            if (not allow_legacy or path.is_symlink() or name != "compose.override.yaml"
                    or MARKER not in path.read_text(encoding="utf-8")):
                raise d.DeploymentError("Найден override; сначала нужна отдельная миграция, чужой файл не меняется.")
            legacy = path
    return legacy


def current_container(config: dict) -> dict:
    """Находит единственный работающий VLM без публикации его окружения."""
    current = d.container_data(config["name"]).get("shared-vlm")
    if not current or not current.get("State", {}).get("Running"):
        raise d.DeploymentError("Работающий VLM не найден; автоматический первый запуск запрещён.")
    if current["State"].get("Health", {}).get("Status") != "healthy":
        raise d.DeploymentError("Текущий VLM не healthy; сначала восстановите рабочую модель.")
    if not d.runtime_matches(config["services"]["shared-vlm"], current):
        raise d.DeploymentError("Runtime отличается от Compose; сначала разберите отличие без recreate.")
    return current


def wait_ready(service: dict, timeout: int) -> None:
    """Ждёт healthy принятой конфигурации с ограниченным временем ожидания."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        config = d.load_config(["ai-vlm"])
        actual = d.container_data(config["name"]).get("shared-vlm", {})
        state = actual.get("State", {})
        if state.get("Running") and state.get("Health", {}).get("Status") == "healthy":
            if not d.runtime_matches(service, actual):
                raise d.DeploymentError("После recreate запущена другая конфигурация.")
            d.http_check(d.endpoint(service, 8000, "/health"))
            return
        if state.get("Status") in ("exited", "dead"):
            raise d.DeploymentError("VLM завершился при запуске.")
        time.sleep(2)
    raise d.DeploymentError("Истекло время ожидания VLM.")


def apply_selection(values: dict[str, str], expected: dict, timeout: int) -> None:
    """Применяет подтверждённый выбор с закрытым backup и возвратом runtime при ошибке."""
    path = ROOT / ".env"
    original = path.read_bytes()
    metadata = path.stat()
    updated = render_env(original, values)
    conflicts = set(values).intersection(os.environ)
    if conflicts:
        raise d.DeploymentError("Переменные команды перекрывают выбор .env; уберите их перед переключением: " + ", ".join(sorted(conflicts)))
    lock = ROOT / ".env.bak-switch.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise d.DeploymentError("Другая операция переключения уже выполняется.") from exc
    os.close(fd)
    touched = False
    backup = ROOT / (".env.bak-" + uuid.uuid4().hex)
    try:
        # Повторная проверка после получения блокировки предотвращает потерю чужой записи.
        if path.read_bytes() != original:
            raise d.DeploymentError(".env изменился параллельно; операция отменена.")
        with backup.open("xb") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), 0o600)
            stream.write(original)
        atomic_write(path, updated, metadata)
        config = d.load_config(["ai-vlm"])
        d.validate_config(config)
        if not equivalent_configs({"services": {"shared-vlm": config["services"]["shared-vlm"]}}, {"services": {"shared-vlm": expected}}):
            raise d.DeploymentError("Итоговая конфигурация отличается от подтверждённого выбора.")
        d.check_gpus(config)
        touched = True
        d.run(*d.compose_args(["ai-vlm"]), "up", "-d", "--no-deps", "--force-recreate", "shared-vlm")
        wait_ready(expected, timeout)
        url = d.endpoint(expected, 8000, "/v1")
        d.run(sys.executable, str(ROOT / "scripts/check-vlm-tool-calling.py"), "--url", url)
        print("Переключение проверено; закрытый backup сохранён:", backup.name)
    except BaseException:
        atomic_write(path, original, metadata)
        if touched:
            try:
                previous = d.load_config(["ai-vlm"])
                d.validate_config(previous)
                d.run(*d.compose_args(["ai-vlm"]), "up", "-d", "--no-deps", "--force-recreate", "shared-vlm")
                wait_ready(previous["services"]["shared-vlm"], timeout)
                d.run(sys.executable, str(ROOT / "scripts/check-vlm-tool-calling.py"),
                      "--url", d.endpoint(previous["services"]["shared-vlm"], 8000, "/v1"))
            except BaseException as rollback_error:
                raise d.DeploymentError("Автоматический rollback не подтверждён. Сохранён закрытый backup; нужна ручная проверка VLM.") from rollback_error
        raise
    finally:
        lock.unlink(missing_ok=True)


def migrate_current(legacy: Path, dry_run: bool) -> None:
    """Переносит owned override без recreate, доказывая равенство всего Compose JSON."""
    config = d.load_config(["ai-vlm"])
    d.validate_config(config)
    current_container(config)
    values = settings(config["services"]["shared-vlm"])
    if set(values).intersection(os.environ):
        raise d.DeploymentError("Уберите process overrides управляемых VLM-полей перед миграцией.")
    path = ROOT / ".env"
    original = path.read_bytes()
    metadata = path.stat()
    updated = render_env(original, values)
    if dry_run:
        print("План: перенести настройки owned override в .env; контейнеры не пересоздаются.")
        return
    if input("Перенести текущую конфигурацию без recreate? Введите YES: ").strip() != "YES":
        print("Без изменений.")
        return
    archive = ROOT / (".env.bak-compose-override-" + uuid.uuid4().hex)
    backup = ROOT / (".env.bak-" + uuid.uuid4().hex)
    lock = ROOT / ".env.bak-switch.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        if path.read_bytes() != original:
            raise d.DeploymentError(".env изменился параллельно; миграция отменена.")
        with backup.open("xb") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), 0o600)
            stream.write(original)
        atomic_write(path, updated, metadata)
        legacy.rename(archive)
        after = d.load_config(["ai-vlm"])
        d.validate_config(after)
        if not equivalent_configs(after, config):
            raise d.DeploymentError("Миграция изменила Compose; восстановлена прежняя конфигурация.")
        print("Миграция проверена без recreate; архив:", archive.name)
    except BaseException:
        atomic_write(path, original, metadata)
        if archive.exists():
            archive.rename(legacy)
        raise
    finally:
        lock.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    """Показывает выбор; запись и recreate возможны только после явного YES."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--migrate-current", action="store_true")
    parser.add_argument("--approved-model", help="Exact model ID отдельно утверждённого тестового профиля")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    if args.timeout < 1:
        parser.error("--timeout должен быть положительным")
    legacy = assert_safe(args.migrate_current)
    if args.migrate_current:
        if not legacy:
            raise d.DeploymentError("Owned override для миграции не найден.")
        migrate_current(legacy, args.dry_run)
        return 0
    config = d.load_config(["ai-vlm"])
    d.validate_config(config)
    baseline = config["services"]["shared-vlm"]
    current = current_container(config)
    print("Текущая физическая модель:", d.value_after(baseline["command"], "--model"))
    print("GPU:", baseline["environment"]["CUDA_VISIBLE_DEVICES"], "; logical alias: shared-vlm")
    if args.dry_run:
        print("Проверка завершена без записи файлов, скачивания и recreate.")
        return 0
    print("0. Оставить текущую модель")
    for key, model in MODELS.items():
        print(f"{key}. {model['name']}" + (" — выбор заблокирован" if model["tool_parser"] is None else ""))
    choice = input("Номер (0–3): ").strip()
    if not choice or choice == "0":
        print("Без изменений.")
        return 0
    if choice not in MODELS:
        raise d.DeploymentError("Неизвестный номер; ничего не изменено.")
    if choice != "1" and args.approved_model != MODELS[choice]["id"]:
        raise d.DeploymentError("REQUIRES_OWNER_DECISION: тестовая модель требует отдельного утверждения и --approved-model с её exact ID.")
    expected = selected_service(choice, baseline)
    if expected == baseline:
        print("Модель и полный набор параметров уже совпадают; ничего не изменено.")
        return 0
    if not re.fullmatch(r"[0-9a-f]{40}", d.value_after(baseline["command"], "--revision")):
        raise d.DeploymentError("Перед сменой закрепите принятую рабочую revision точным SHA: main не гарантирует точный rollback.")
    cache = json.loads(d.run("docker", "exec", current["Id"], "python3", "-B", "-c", CACHE_PROBE,
                            json.dumps([{"id": MODELS[choice]["id"], "revision": MODELS[choice]["revision"]}])).stdout)
    if not cache.get(MODELS[choice]["id"], {}).get("ready"):
        raise d.DeploymentError("Полный snapshot выбранной модели не подтверждён; скачивание не выполняется.")
    print("Параметры выбранного deployment:")
    for flag in MANAGED:
        print(f"  {flag}: {d.value_after(expected['command'], flag)}")
    for flag in ("--reasoning-parser", "--tool-call-parser", "--quantization", "--spec-method", "--spec-tokens"):
        print(f"  {flag}: {d.value_after(expected['command'], flag) or 'отключено'}")
    print("  GPU:", expected["environment"]["CUDA_VISIBLE_DEVICES"])
    print("Будет пересоздан только shared-vlm. Активные запросы прервутся.")
    print("После health выполняются text/vision/tools проверки; при ошибке возвращается прежняя модель.")
    print("Новый выбор сохраняется только в .env; embedding, n8n и RabbitMQ не переключаются.")
    if input("Подтвердить переключение и rollback при ошибке? Введите YES: ").strip() != "YES":
        print("Без изменений.")
        return 0
    apply_selection(settings(expected), expected, args.timeout)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("Отменено; проверьте сообщение о rollback, если recreate уже начался.", file=sys.stderr)
        sys.exit(130)
    except d.DeploymentError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)
    except (OSError, ValueError, KeyError):
        print("Операция не завершена. Проверьте безопасную диагностику; секреты не выводятся.", file=sys.stderr)
        sys.exit(2)
