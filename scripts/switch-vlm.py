#!/usr/bin/env python3
# scripts/switch-vlm.py
"""Interactive, cache-only switcher for shared-infrastructure's VLM service."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OVERRIDE = ROOT / "compose.override.yaml"
MARKER = "# Managed by scripts/switch-vlm.py. Do not edit manually."
GPU_PAIR = "2,3"
MAX_MODEL_LEN = "262144"

MODELS = {
    "1": {
        "name": "Qwen3.8-27B (основная, конфигурация compose.yaml)",
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
            "--max-model-len", MAX_MODEL_LEN,
            "--gpu-memory-utilization", "0.90",
            "--max-num-seqs", "8",
            "--max-num-queued-reqs", "48",
            "--max-num-queued-tokens", "131072",
        ],
    },
    "3": {
        "name": "Qwen3-VL-235B-A22B-Instruct-AWQ (тест)",
        "id": "QuantTrio/Qwen3-VL-235B-A22B-Instruct-AWQ",
        "revision": "dc4295875e8a6d61e60a7c4ee6406585d9c70e9c",
        "tool_parser": None,  # JSON-in-XML; compatible parser not verified.
        "args": [
            "--reasoning-parser", "qwen3",
            "--max-model-len", MAX_MODEL_LEN,
            "--gpu-memory-utilization", "0.90",
            "--max-num-seqs", "8",
            "--max-num-queued-reqs", "48",
            "--max-num-queued-tokens", "131072",
        ],
    },
}

# Выполняется внутри одноразового контейнера, использующего тот же HF-volume.
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


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args), cwd=ROOT, text=True, capture_output=True, check=check
    )


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # Без -f: подхватывается compose.override.yaml и сохраняется выбор модели.
    return run("docker", "compose", "--profile", "ai-vlm", *args, check=check)


def value_after(command: list[str], flag: str) -> str:
    try:
        return command[command.index(flag) + 1]
    except (ValueError, IndexError):
        return "не определено"


def container_state() -> dict[str, str]:
    ps = run("docker", "compose", "-f", "compose.yaml", "--profile", "ai-vlm",
             "ps", "-a", "-q", "shared-vlm")
    container_id = ps.stdout.strip()
    if not container_id:
        return {"model": "не запущена", "gpu": "—", "context": "—",
                "health": "контейнер отсутствует", "tool_parser": "—",
                "tools_enabled": False, "command": []}
    data = json.loads(run("docker", "inspect", container_id).stdout)[0]
    cmd = data["Config"].get("Cmd") or []
    env = data["Config"].get("Env") or []
    gpu = next((item.partition("=")[2] for item in env
                if item.startswith("CUDA_VISIBLE_DEVICES=")), "не определено")
    state = data["State"]
    health = (state.get("Health") or {}).get("Status", state.get("Status", "unknown"))
    return {
        "model": value_after(cmd, "--model"),
        "gpu": gpu,
        "context": value_after(cmd, "--max-model-len"),
        "health": health,
        "tool_parser": value_after(cmd, "--tool-call-parser"),
        "tools_enabled": "--enable-auto-tool-choice" in cmd,
        "command": cmd,
    }


def check_local_cache() -> dict[str, dict]:
    names = [{"id": m["id"], "revision": m["revision"]} for m in MODELS.values()]
    process = run(
        "docker", "compose", "-f", "compose.yaml", "--profile", "ai-vlm",
        "run", "-T", "--rm", "--no-deps", "--entrypoint", "python3",
        "shared-vlm", "-c", CACHE_PROBE, json.dumps(names)
    )
    return json.loads(process.stdout)


def assert_safe() -> None:
    if not (ROOT / "compose.yaml").is_file():
        raise RuntimeError(f"Не найден {ROOT / 'compose.yaml'}")
    if OVERRIDE.exists() and MARKER not in OVERRIDE.read_text(encoding="utf-8"):
        raise RuntimeError(
            "Обнаружен сторонний compose.override.yaml; скрипт его не перезапишет."
        )
    if os.environ.get("COMPOSE_FILE"):
        raise RuntimeError(
            "COMPOSE_FILE задан в окружении; автоматический override может не работать."
        )
    env_path = ROOT / ".env"
    if env_path.is_file() and re.search(
        r"(?m)^\s*(?:export\s+)?COMPOSE_FILE\s*=", env_path.read_text()
    ):
        raise RuntimeError("COMPOSE_FILE присутствует в .env; необходимо проверить Compose-файлы.")
    # Проверяем исходную конфигурацию, не загружая автоматически override.
    run("docker", "compose", "-f", "compose.yaml", "--profile", "ai-vlm",
        "config", "--quiet")
    raw = run("docker", "compose", "-f", "compose.yaml", "--profile", "ai-vlm",
              "config", "--format", "json").stdout
    original = json.loads(raw)["services"]["shared-vlm"]
    if value_after(original["command"], "--model") != MODELS["1"]["id"]:
        raise RuntimeError("Исходная модель в compose.yaml/.env уже не Qwen3.8-27B. Отмена.")
    if original["environment"].get("CUDA_VISIBLE_DEVICES") != GPU_PAIR:
        raise RuntimeError("Исходная GPU-пара изменилась. Ожидались GPU 2,3. Отмена.")
    if value_after(original["command"], "--tensor-parallel-size") != "2":
        raise RuntimeError("TP исходной модели изменился. Ожидалось TP=2. Отмена.")
    if value_after(original["command"], "--max-model-len") != MAX_MODEL_LEN:
        raise RuntimeError(
            "У исходной 27B в compose.yaml/.env контекст не 262144. Отмена."
        )
    if not tool_calling_enabled(original["command"], "qwen3_coder"):
        raise RuntimeError("Основной compose.yaml не содержит Tool Calling для 27B.")


def tool_calling_enabled(command: list[str], parser: str) -> bool:
    return ("--enable-auto-tool-choice" in command
            and value_after(command, "--tool-call-parser") == parser)


def make_override(model: dict) -> str:
    parser = model["tool_parser"]
    if parser is None:
        raise ValueError("Tool Calling parser for this model has not been validated.")
    command = [
        "--model", model["id"],
        "--revision", model["revision"],
        "--served-model-name", "shared-vlm",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--tensor-parallel-size", "2",
        "--data-parallel-size", "1",
        "--enable-auto-tool-choice",
        "--tool-call-parser", parser,
        *model["args"],
    ]
    lines = [
        "# compose.override.yaml",
        MARKER,
        "services:",
        "  shared-vlm:",
        "    environment:",
        '      CUDA_VISIBLE_DEVICES: "2,3"',
        "    command:",
    ]
    lines.extend("      - " + json.dumps(arg) for arg in command)
    return "\n".join(lines) + "\n"


def print_check_commands() -> None:
    print("\nПроверка состояния:")
    print("  docker compose ps shared-vlm")
    print('  docker inspect "$(docker compose ps -q shared-vlm)" \\\n    --format \'{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}\'')
    print("  docker compose logs --tail=100 -f shared-vlm")
    print("  curl -fsS http://127.0.0.1:8000/v1/models | python3 -m json.tool")
    print("Ожидаем: healthy и HTTP 200. Имя модели в API останется shared-vlm.")
    print("Если контейнер unhealthy/exited, проверьте логи на OOM/KV cache.")
    print("Для проверки физической модели повторно запустите этот скрипт.")


def save_selection(key: str) -> None:
    if MODELS[key]["tool_parser"] is None:
        raise ValueError("Unsupported Tool Calling parser; selection rejected.")
    if key == "1":
        OVERRIDE.unlink(missing_ok=True)
    else:
        temporary = OVERRIDE.with_suffix(".yaml.tmp")
        temporary.write_text(make_override(MODELS[key]), encoding="utf-8")
        temporary.replace(OVERRIDE)


def matches_model_runtime(command: list[str], selected: dict) -> bool:
    """Compare the actual container args with the desired model settings."""
    if value_after(command, "--model") != selected["id"]:
        return False
    if value_after(command, "--max-model-len") != MAX_MODEL_LEN:
        return False
    if not tool_calling_enabled(command, selected["tool_parser"]):
        return False
    if selected["args"] is None:
        return True
    expected = selected["args"]
    for position in range(0, len(expected), 2):
        if value_after(command, expected[position]) != expected[position + 1]:
            return False
    return True


def main() -> int:
    assert_safe()
    current = container_state()
    print("=== Shared VLM: переключение моделей ===")
    print(f"Текущая модель: {current['model']}")
    print(f"GPU: {current['gpu']}; контекст: {current['context']}; "
          f"статус: {current['health']}; "
          f"tool_parser: {current['tool_parser']}; "
          f"auto_tools: {current['tools_enabled']}")
    print("Проверяю скачанные модели в постоянном Hugging Face cache...")
    cache = check_local_cache()
    print("\n0. Оставить текущую модель")
    for key, model in MODELS.items():
        info = cache.get(model["id"], {})
        state = "скачана" if info.get("ready") else "НЕ подтверждена в cache"
        size = f", веса ~{info['size'] / 1e9:.1f} GB" if info.get("ready") else ""
        active = " [ТЕКУЩИЙ КОНТЕЙНЕР]" if current["model"] == model["id"] else ""
        tool_status = (f"tools: {model['tool_parser']}"
                       if model["tool_parser"] else "tools: НЕТ — выбор заблокирован")
        print(f"{key}. {model['name']} — {state}{size}{active}; {tool_status}")

    listed = {m["id"] for m in MODELS.values()}
    extras = [name for name in cache.get("__cached_repositories__", []) if name not in listed]
    if extras:
        print("\nДругие модели в cache (не доступны для переключения VLM):")
        for name in extras:
            print(f"  • {name}")

    choice = input("\nНомер (0–3): ").strip()
    if choice == "0" or choice == "":
        print("Без изменений.")
        return 0
    if choice not in MODELS:
        print("Неверный номер. Ничего не изменено.")
        return 1
    selected = MODELS[choice]
    if selected["tool_parser"] is None:
        print("Для этой модели парсер Tool Calling не проверен.")
        print("Шаблон модели отличается от XML-парсера qwen3_coder.")
        print("Переключение запрещено до отдельного испытания парсера.")
        return 1
    if not cache.get(selected["id"], {}).get("ready"):
        print("Модель не подтверждена как полностью скачанная. Переключение отменено.")
        return 1
    if (current["health"] == "healthy"
            and matches_model_runtime(current["command"], selected)):
        effective = json.loads(compose("config", "--format", "json").stdout)
        effective_command = effective["services"]["shared-vlm"]["command"]
        if matches_model_runtime(effective_command, selected):
            print("Модель уже работает с нужным контекстом, параллелизмом "
                  "и Tool Calling. Ничего не изменено.")
            return 0
    if current["gpu"] not in (GPU_PAIR, "—"):
        print(f"Неожиданная GPU-пара: {current['gpu']}. Переключение отменено.")
        return 1

    print(f"\nВыбрано: {selected['id']}")
    print(f"Режим: GPU {GPU_PAIR}, TP=2, DP=1; "
          f"контекст={MAX_MODEL_LEN}; API-имя: shared-vlm; "
          f"Tool Calling: {selected['tool_parser']}")
    print("ВНИМАНИЕ: 262144 токена могут не поместиться в GPU KV cache; "
          "vLLM может завершиться с ошибкой при запуске.")
    print("ВНИМАНИЕ: контейнер shared-vlm будет пересоздан; активные запросы прервутся.")
    print("shared-embedding, n8n и RabbitMQ не переключаются. Веса не удаляются.")
    if input("Для подтверждения введите YES: ").strip() != "YES":
        print("Отменено.")
        return 0

    previous = OVERRIDE.read_bytes() if OVERRIDE.exists() else None
    try:
        save_selection(choice)
        compose("config", "--quiet")
        print("\nПересоздаю только shared-vlm...")
        result = compose("up", "-d", "--no-deps", "--force-recreate", "shared-vlm")
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())
    except (OSError, subprocess.CalledProcessError) as exc:
        if previous is None:
            OVERRIDE.unlink(missing_ok=True)
        else:
            OVERRIDE.write_bytes(previous)
        print(f"ОШИБКА переключения: {exc}", file=sys.stderr)
        print("Прежняя конфигурация восстановлена, но состояние контейнера надо проверить.")
        print_check_commands()
        return 2

    print("\nКоманда запуска выполнена; это ещё НЕ означает, что модель загрузилась в GPU.")
    print("Обычно требуется несколько минут, крупная модель может загружаться дольше.")
    print("У shared-vlm healthcheck start_period=15m — это льготный период, не обещание запуска.")
    print("Если модель не стартует, запустите скрипт снова и выберите 1 (Qwen3.8-27B).")
    print_check_commands()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nОтменено пользователем.")
        sys.exit(130)
    except (OSError, ValueError, KeyError, json.JSONDecodeError,
            subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print((exc.stderr or "").strip(), file=sys.stderr)
        sys.exit(2)
