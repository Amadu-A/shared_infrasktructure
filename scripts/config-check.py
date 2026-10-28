#!/usr/bin/env python3
# scripts/config-check.py
"""Проверки Compose, готовности сервисов и параметров перед запуском моделей.

Значения разрешает сам Compose: окружение команды > .env > разрешённые defaults.
Закрытые config/inspect остаются в памяти. Модели и пользователи здесь не меняются.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI_PROFILES = {"shared-vlm": "ai-vlm", "shared-embedding": "ai-embedding",
               "open-webui": "ai-ui"}
GPU_DEVICES = {"shared-vlm": "2,3", "shared-embedding": "1"}
SECRETS = {"n8n-db": ("POSTGRES_PASSWORD",),
           "n8n": ("DB_POSTGRESDB_PASSWORD", "N8N_ENCRYPTION_KEY"),
           "rabbitmq": ("RABBITMQ_DEFAULT_PASS",),
           "open-webui": ("WEBUI_SECRET_KEY",)}


class ConfigError(RuntimeError):
    """Понятная ошибка без значений секретов."""


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Выполняет проверку в каталоге проекта, скрывая закрытый вывод ошибки."""
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode:
        raise ConfigError("Команда проверки завершилась ошибкой; проверьте настройки локально.")
    return result


def compose_args(profiles: list[str], env_file: str | None) -> list[str]:
    """Передаёт Compose ровно те profiles/env-file, которые выбрал оператор."""
    args = ["docker", "compose"]
    if env_file:
        args += ["--env-file", env_file]
    for profile in profiles:
        args += ["--profile", profile]
    return args


def load_config(profiles: list[str], env_file: str | None) -> dict:
    """Читает итоговый JSON Compose без отдельного dotenv-парсера."""
    return json.loads(run(*compose_args(profiles, env_file),
                          "config", "--format", "json").stdout)


def require(name: str, value: object) -> None:
    """Отклоняет пустое обязательное значение и placeholder из каталога."""
    if not isinstance(value, str) or not value.strip() or value.strip().startswith("CHANGE_ME"):
        raise ConfigError(f"{name}: требуется непустое значение без CHANGE_ME.")


def argument(command: list[str], flag: str) -> str:
    """Возвращает единственный аргумент флага; дубликаты не принимает."""
    if command.count(flag) != 1 or command.index(flag) + 1 >= len(command):
        raise ConfigError(f"Отсутствует или повторяется параметр {flag}.")
    if any(item.startswith(flag + "=") for item in command):
        raise ConfigError(f"Параметр {flag} повторён в дополнительных аргументах.")
    value = command[command.index(flag) + 1]
    require(flag, value)
    return value


def validate(config: dict) -> None:
    """Проверяет секреты, logical aliases и явные GPU/TP/DP/context выбранных сервисов."""
    for name, service in config["services"].items():
        env = service.get("environment") or {}
        for key in SECRETS.get(name, ()):
            require(f"{name}.{key}", env.get(key))
        if name not in GPU_DEVICES:
            continue
        command = service.get("command") or []
        for flag in ("--model", "--revision"):
            argument(command, flag)
        if argument(command, "--served-model-name") != name:
            raise ConfigError(f"{name}: логический alias изменять нельзя.")
        if any(item == "--api-key" or item.startswith("--api-key=") for item in command):
            raise ConfigError(f"{name}: текущий API-контракт не требует API key.")
        devices = str(env.get("CUDA_VISIBLE_DEVICES", ""))
        try:
            tp = int(argument(command, "--tensor-parallel-size"))
            dp = int(argument(command, "--data-parallel-size"))
            context = int(argument(command, "--max-model-len"))
        except ValueError as exc:
            raise ConfigError(f"{name}: TP, DP и context должны быть положительными числами.") from exc
        if (devices != GPU_DEVICES[name] or min(tp, dp, context) <= 0
                or tp * dp != len(devices.split(","))):
            raise ConfigError(f"{name}: проверьте GPU {GPU_DEVICES[name]}, TP, DP и context.")


def guard(name: str, command: list[str]) -> int:
    """Проверяет параметры внутри контейнера до штатного запуска vLLM или WebUI."""
    if name not in AI_PROFILES:
        raise ConfigError("Неизвестный inference/UI сервис.")
    validate({"services": {name: {"environment": dict(os.environ), "command": command}}})
    if name == "open-webui":
        os.execvp("bash", ["bash", "/app/backend/start.sh", *command])
    else:
        os.execvp("vllm", ["vllm", "serve", *command])
    return 0


def check_gpus(config: dict) -> None:
    """Проверяет наличие NVIDIA только при использовании inference-сервисов."""
    expected = {gpu for name in config["services"] if name in GPU_DEVICES
                for gpu in GPU_DEVICES[name].split(",")}
    if expected:
        result = run("nvidia-smi", "--query-gpu=index", "--format=csv,noheader,nounits")
        if not expected.issubset(set(result.stdout.split())):
            raise ConfigError("Не найдены GPU выбранных inference-сервисов.")


def containers(project: str) -> dict[str, dict]:
    """Читает состояние только данного Compose-проекта без вывода credentials."""
    ids = run("docker", "ps", "-a", "--filter",
              f"label=com.docker.compose.project={project}", "--format", "{{.ID}}").stdout.split()
    result = {}
    for item in json.loads(run("docker", "inspect", *ids).stdout) if ids else []:
        name = item["Config"]["Labels"]["com.docker.compose.service"]
        if name in result:
            raise ConfigError(f"{name}: найдено несколько контейнеров; нужна ручная проверка.")
        result[name] = item
    return result


def url(service: dict, port: int, path: str) -> str:
    """Берёт опубликованный адрес из Compose, не дублируя default портов."""
    binding = next(item for item in service["ports"] if int(item["target"]) == port)
    host = binding.get("host_ip", "0.0.0.0")
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in host:
        host = "[" + host + "]"
    return f"http://{host}:{binding['published']}{path}"


def http_get(address: str) -> bytes:
    """Проверяет HTTP с конечным timeout и не выводит тело ответа."""
    with urllib.request.urlopen(address, timeout=10) as response:
        return response.read()


def check_services(config: dict, actual: dict) -> bool:
    """Проверяет ожидаемые контейнеры, health и доступность логических моделей."""
    success = True
    for name, service in config["services"].items():
        try:
            item = actual.get(name, {})
            state = item.get("State", {})
            if not state.get("Running") or state.get("Health", {}).get("Status") != "healthy":
                raise ConfigError("Ожидаемый контейнер отсутствует или ещё не healthy.")
            if name in AI_PROFILES:
                current = item["Config"]
                if (current.get("Image") != service.get("image")
                        or current.get("Cmd") != service.get("command")
                        or current.get("Entrypoint") != service.get("entrypoint")):
                    raise ConfigError("Контейнер использует другую конфигурацию запуска.")
            if name in GPU_DEVICES:
                env = dict(value.split("=", 1) for value in item["Config"]["Env"] if "=" in value)
                if any(env.get(key, "") != str(service["environment"].get(key, ""))
                       for key in ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "VLLM_BATCH_INVARIANT")):
                    raise ConfigError("Параметры GPU/inference отличаются от Compose.")
                http_get(url(service, 8000, "/health"))
                reply = json.loads(http_get(url(service, 8000, "/v1/models")))
                if name not in {model.get("id") for model in reply["data"]}:
                    raise ConfigError("API не публикует ожидаемое логическое имя модели.")
            elif name in ("open-webui", "n8n"):
                port, path = (8080, "/health") if name == "open-webui" else (5678, "/healthz")
                http_get(url(service, port, path))
            print(f"[OK] {name}")
        except (ConfigError, OSError, ValueError, KeyError, TypeError, StopIteration):
            success = False
            print(f"[FAIL] {name}: проверьте health, конфигурацию запуска и API.")
    return success


def main(argv: list[str] | None = None) -> int:
    """Проверяет конфигурацию; prepare создаёт сеть, check ничего не меняет."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv[:1] == ["guard"]:
            if len(argv) < 2:
                raise ConfigError("Не указан сервис для guard.")
            return guard(argv[1], argv[2:])
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("action", choices=("validate", "prepare", "check"))
        parser.add_argument("--profile", action="append", default=[])
        parser.add_argument("--env-file")
        parser.add_argument("--check-only", action="store_true")
        args = parser.parse_args(argv)
        config = load_config(args.profile, args.env_file)
        validate(config)
        if args.action == "validate":
            print("CONFIG OK")
            return 0
        run("docker", "info")
        actual = containers(config["name"]) if args.action == "check" else {}
        extra = [AI_PROFILES[name] for name, item in actual.items()
                 if name in AI_PROFILES and item["State"].get("Running")
                 and name not in config["services"]]
        if extra:
            config = load_config(args.profile + extra, args.env_file)
            validate(config)
        check_gpus(config)
        network = config["networks"]["ai-shared"]["name"]
        if run("docker", "network", "inspect", network, check=False).returncode:
            if args.action != "prepare" or args.check_only:
                raise ConfigError("Shared-сеть отсутствует; выполните первоначальную настройку.")
            run("docker", "network", "create", network)
        if args.action == "prepare":
            print("PREPARE OK")
            return 0
        success = check_services(config, actual)
        print("ALL CHECKS PASSED" if success else "ONE OR MORE CHECKS FAILED")
        return 0 if success else 1
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
        message = str(exc) if isinstance(exc, ConfigError) else "Не удалось проверить конфигурацию/Docker; проверьте локально."
        print(f"[ERROR] {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
