#!/usr/bin/env python3
# scripts/config-check.py
"""Проверки Compose, готовности сервисов и параметров перед запуском моделей.

Значения разрешает сам Compose: окружение команды > .env > разрешённые defaults.
Закрытые config/inspect остаются в памяти. Модели и пользователи здесь не меняются.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI_PROFILES = {
    "shared-vlm": "ai-vlm",
    "shared-embedding": "ai-embedding",
    "open-webui": "ai-ui",
}
INFERENCE_SERVICES = {"shared-vlm", "shared-embedding"}
LEGACY_HELPERS = ("vlm-entrypoint.py", "deployment.py", "open-webui-entrypoint.sh")
SERVICE_ORDER = ("shared-vlm", "shared-embedding", "open-webui")
SECRETS = {
    "n8n-db": ("POSTGRES_PASSWORD",),
    "n8n": ("DB_POSTGRESDB_PASSWORD", "N8N_ENCRYPTION_KEY"),
    "rabbitmq": ("RABBITMQ_DEFAULT_PASS",),
    "open-webui": ("WEBUI_SECRET_KEY",),
}


class ConfigError(RuntimeError):
    """Понятная ошибка без значений секретов."""


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Выполняет проверку в каталоге проекта, скрывая закрытый вывод ошибки."""
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
    if check and result.returncode:
        raise ConfigError(
            "Команда проверки завершилась ошибкой; проверьте настройки локально."
        )
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
    return json.loads(
        run(*compose_args(profiles, env_file), "config", "--format", "json").stdout
    )


def require(name: str, value: object) -> None:
    """Отклоняет пустое обязательное значение и placeholder из каталога."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.strip().startswith("CHANGE_ME")
    ):
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


def gpu_devices(service: dict, name: str) -> list[str]:
    """Читает назначенные GPU из итогового Compose, не выбирая их в Python."""
    raw = str((service.get("environment") or {}).get("CUDA_VISIBLE_DEVICES", ""))
    devices = [item.strip() for item in raw.split(",") if item.strip()]
    if not devices or any(not item.isdigit() for item in devices):
        raise ConfigError(
            f"{name}: GPU должны быть заданы числовым списком в deployment-настройках."
        )
    if len(devices) != len(set(devices)):
        raise ConfigError(f"{name}: список GPU содержит повторяющиеся индексы.")
    return devices


def validate(config: dict) -> None:
    """Проверяет секреты, logical aliases и явные GPU/TP/DP/context выбранных сервисов."""
    for name, service in config["services"].items():
        env = service.get("environment") or {}
        for key in SECRETS.get(name, ()):
            require(f"{name}.{key}", env.get(key))
        if name not in INFERENCE_SERVICES:
            continue
        command = service.get("command") or []
        for flag in ("--model", "--revision"):
            argument(command, flag)
        if argument(command, "--served-model-name") != name:
            raise ConfigError(f"{name}: логический alias изменять нельзя.")
        if any(
            item == "--api-key" or item.startswith("--api-key=") for item in command
        ):
            raise ConfigError(f"{name}: текущий API-контракт не требует API key.")
        devices = gpu_devices(service, name)
        try:
            tp = int(argument(command, "--tensor-parallel-size"))
            dp = int(argument(command, "--data-parallel-size"))
            context = int(argument(command, "--max-model-len"))
        except ValueError as exc:
            raise ConfigError(
                f"{name}: TP, DP и context должны быть положительными числами."
            ) from exc
        if min(tp, dp, context) <= 0 or tp * dp != len(devices):
            raise ConfigError(
                f"{name}: количество GPU должно совпадать с TP × DP; context должен быть положительным."
            )


def guard(name: str, command: list[str]) -> int:
    """Проверяет параметры внутри контейнера до штатного запуска vLLM или WebUI."""
    if name not in AI_PROFILES:
        raise ConfigError("Неизвестный inference/UI сервис.")
    validate(
        {"services": {name: {"environment": dict(os.environ), "command": command}}}
    )
    if name == "open-webui":
        os.execvp("bash", ["bash", "/app/backend/start.sh", *command])
    else:
        os.execvp("vllm", ["vllm", "serve", *command])
    return 0


def check_gpus(config: dict) -> None:
    """Проверяет наличие NVIDIA только при использовании inference-сервисов."""
    expected = {
        gpu
        for name, service in config["services"].items()
        if name in INFERENCE_SERVICES
        for gpu in gpu_devices(service, name)
    }
    if expected:
        result = run("nvidia-smi", "--query-gpu=index", "--format=csv,noheader,nounits")
        if not expected.issubset(set(result.stdout.split())):
            raise ConfigError("Не найдены GPU выбранных inference-сервисов.")


def containers(project: str) -> dict[str, dict]:
    """Читает состояние только данного Compose-проекта без вывода credentials."""
    ids = run(
        "docker",
        "ps",
        "-a",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{.ID}}",
    ).stdout.split()
    result = {}
    for item in json.loads(run("docker", "inspect", *ids).stdout) if ids else []:
        name = item["Config"]["Labels"]["com.docker.compose.service"]
        if name in result:
            raise ConfigError(
                f"{name}: найдено несколько контейнеров; нужна ручная проверка."
            )
        result[name] = item
    return result


def legacy_services(actual: dict[str, dict]) -> list[str]:
    """Находит только контейнеры со старыми host-bind helper-файлами."""
    result = []
    for name in SERVICE_ORDER:
        item = actual.get(name)
        if not item:
            continue
        sources = [
            str(mount.get("Source", "")).replace("\\", "/")
            for mount in item.get("Mounts", [])
            if mount.get("Type") == "bind"
        ]
        if any(
            source.endswith("/scripts/" + helper)
            for source in sources
            for helper in LEGACY_HELPERS
        ):
            result.append(name)
    return result


def migration_services(config: dict, actual: dict[str, dict]) -> list[str]:
    """Разрешает helper-миграцию, только если image/command/env не меняются."""
    result = legacy_services(actual)
    for name in result:
        desired = config.get("services", {}).get(name)
        if not desired:
            raise ConfigError(f"{name}: сервис не включён в итоговый Compose.")
        current = actual[name]["Config"]
        if current.get("Image") != desired.get("image"):
            raise ConfigError(f"{name}: helper-миграция не должна менять image.")
        if name in INFERENCE_SERVICES and current.get("Cmd") != desired.get("command"):
            raise ConfigError(
                f"{name}: модельные параметры отличаются; миграция запрещена."
            )
        current_env = dict(
            value.split("=", 1) for value in current.get("Env", []) if "=" in value
        )
        if any(
            current_env.get(key) != str(value)
            for key, value in (desired.get("environment") or {}).items()
        ):
            raise ConfigError(f"{name}: environment отличается; миграция запрещена.")
    return result


def url(service: dict, port: int, path: str) -> str:
    """Берёт опубликованный адрес из Compose, не дублируя default портов."""
    binding = next(item for item in service["ports"] if int(item["target"]) == port)
    host = binding.get("host_ip") or "0.0.0.0"
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
    if ":" in host:
        host = "[" + host + "]"
    return f"http://{host}:{binding['published']}{path}"


def http_get(address: str) -> bytes:
    """Проверяет HTTP с конечным timeout и не выводит тело ответа."""
    with urllib.request.urlopen(address, timeout=10) as response:
        return response.read()


def rabbitmq_get(service: dict, path: str, user: str, password: str) -> bytes:
    """Обращается к Management API с реальной Basic-авторизацией, не логируя секрет."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
    request = urllib.request.Request(url(service, 15672, path))
    request.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def check_rabbitmq(service: dict, item: dict) -> None:
    """Проверяет broker, AMQP-порт, авторизацию и доступ к обязательным vhost."""
    environment = service.get("environment") or {}
    user = str(environment.get("RABBITMQ_DEFAULT_USER", ""))
    password = str(environment.get("RABBITMQ_DEFAULT_PASS", ""))
    require("rabbitmq.RABBITMQ_DEFAULT_USER", user)
    require("rabbitmq.RABBITMQ_DEFAULT_PASS", password)
    run("docker", "exec", item["Id"], "rabbitmq-diagnostics", "-q", "check_running")

    binding = next(port for port in service["ports"] if int(port["target"]) == 5672)
    configured_host = binding.get("host_ip") or "0.0.0.0"
    host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(configured_host, configured_host)
    with socket.create_connection((host, int(binding["published"])), timeout=10):
        pass

    rabbitmq_get(service, "/api/overview", user, password)
    vhosts = {
        entry["name"]
        for entry in json.loads(rabbitmq_get(service, "/api/vhosts", user, password))
    }
    required = {
        name.strip()
        for name in str(environment.get("RABBITMQ_REQUIRED_VHOSTS", "/")).split(",")
        if name.strip()
    }
    if not required or not required.issubset(vhosts):
        raise ConfigError("RabbitMQ: отсутствует один из обязательных virtual hosts.")
    quoted_user = urllib.parse.quote(user, safe="")
    permissions = {
        entry["vhost"]
        for entry in json.loads(
            rabbitmq_get(
                service, f"/api/users/{quoted_user}/permissions", user, password
            )
        )
    }
    if not required.issubset(permissions):
        raise ConfigError(
            "RabbitMQ: у проверяемого пользователя нет прав на обязательный vhost."
        )


def check_services(config: dict, actual: dict) -> bool:
    """Проверяет ожидаемые контейнеры, health и доступность логических моделей."""
    success = True
    for name, service in config["services"].items():
        try:
            item = actual.get(name, {})
            state = item.get("State", {})
            if (
                not state.get("Running")
                or state.get("Health", {}).get("Status") != "healthy"
            ):
                raise ConfigError("Ожидаемый контейнер отсутствует или ещё не healthy.")
            if item.get("HostConfig", {}).get("RestartPolicy", {}).get(
                "Name"
            ) != service.get("restart"):
                raise ConfigError("Restart policy контейнера отличается от Compose.")
            if name in AI_PROFILES:
                current = item["Config"]
                if (
                    current.get("Image") != service.get("image")
                    or current.get("Cmd") != service.get("command")
                    or current.get("Entrypoint") != service.get("entrypoint")
                ):
                    raise ConfigError(
                        "Контейнер использует другую конфигурацию запуска."
                    )
            if name in INFERENCE_SERVICES:
                env = dict(
                    value.split("=", 1)
                    for value in item["Config"]["Env"]
                    if "=" in value
                )
                if any(
                    env.get(key, "") != str(service["environment"].get(key, ""))
                    for key in (
                        "CUDA_VISIBLE_DEVICES",
                        "CUDA_DEVICE_ORDER",
                        "VLLM_BATCH_INVARIANT",
                    )
                ):
                    raise ConfigError("Параметры GPU/inference отличаются от Compose.")
                http_get(url(service, 8000, "/health"))
                reply = json.loads(http_get(url(service, 8000, "/v1/models")))
                if name not in {model.get("id") for model in reply["data"]}:
                    raise ConfigError(
                        "API не публикует ожидаемое логическое имя модели."
                    )
            elif name in ("open-webui", "n8n"):
                port, path = (
                    (8080, "/health") if name == "open-webui" else (5678, "/healthz")
                )
                http_get(url(service, port, path))
            elif name == "rabbitmq":
                check_rabbitmq(service, item)
            print(f"[OK] {name}")
        except (ConfigError, OSError, ValueError, KeyError, TypeError, StopIteration):
            success = False
            print(f"[FAIL] {name}: проверьте health, конфигурацию запуска и API.")
    return success


def wait_healthy(project: str, expected: set[str], timeout: int) -> dict[str, dict]:
    """Ждёт восстановления прежних контейнеров после restart Docker без Compose up."""
    deadline = time.monotonic() + timeout
    while True:
        actual = containers(project)
        if all(
            item.get("State", {}).get("Running")
            and item.get("State", {}).get("Health", {}).get("Status") == "healthy"
            for name in expected
            if (item := actual.get(name))
        ) and expected.issubset(actual):
            return actual
        if time.monotonic() >= deadline:
            raise ConfigError(
                "Сервисы не восстановились после restart Docker за отведённое время."
            )
        time.sleep(10)


def restart_state(actual: dict[str, dict]) -> dict:
    """Фиксирует только ID, время старта, restart policy и named volumes, без env/secrets."""
    state = {}
    for name, item in actual.items():
        state[name] = {
            "id": item["Id"],
            "started_at": item["State"]["StartedAt"],
            "restart": item["HostConfig"]["RestartPolicy"]["Name"],
            "volumes": sorted(
                [mount["Name"], mount["Destination"]]
                for mount in item.get("Mounts", [])
                if mount.get("Type") == "volume"
            ),
        }
    return state


def verify_restart(before: dict, after: dict) -> None:
    """Доказывает restart без recreate и сохранение тех же named volumes."""
    if set(before) != set(after):
        raise ConfigError("После restart изменился набор контейнеров.")
    for name, old in before.items():
        new = after[name]
        if old["id"] != new["id"] or old["volumes"] != new["volumes"]:
            raise ConfigError(
                f"{name}: контейнер был пересоздан или сменились volumes."
            )
        if new["restart"] != "unless-stopped":
            raise ConfigError(f"{name}: restart policy должна быть unless-stopped.")
        if old["started_at"] == new["started_at"]:
            raise ConfigError(f"{name}: нет признака перезапуска после restart Docker.")


def main(argv: list[str] | None = None) -> int:
    """Проверяет конфигурацию; prepare создаёт сеть, check ничего не меняет."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv[:1] == ["guard"]:
            if len(argv) < 2:
                raise ConfigError("Не указан сервис для guard.")
            return guard(argv[1], argv[2:])
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument(
            "action",
            choices=(
                "validate",
                "prepare",
                "check",
                "migration-plan",
                "reboot-snapshot",
                "reboot-verify",
            ),
        )
        parser.add_argument("--profile", action="append", default=[])
        parser.add_argument("--env-file")
        parser.add_argument("--check-only", action="store_true")
        parser.add_argument("--state-file")
        parser.add_argument("--wait-timeout", type=int, default=1800)
        args = parser.parse_args(argv)
        if args.action in ("migration-plan", "reboot-snapshot", "reboot-verify"):
            args.profile = list(
                dict.fromkeys(args.profile + list(AI_PROFILES.values()))
            )
        config = load_config(args.profile, args.env_file)
        validate(config)
        if args.action == "validate":
            print("CONFIG OK")
            return 0
        run("docker", "info")
        runtime_actions = {
            "check",
            "migration-plan",
            "reboot-snapshot",
            "reboot-verify",
        }
        actual = containers(config["name"]) if args.action in runtime_actions else {}
        if args.action == "migration-plan":
            print(" ".join(migration_services(config, actual)))
            return 0
        extra = [
            AI_PROFILES[name]
            for name, item in actual.items()
            if name in AI_PROFILES
            and item["State"].get("Running")
            and name not in config["services"]
        ]
        if extra:
            config = load_config(args.profile + extra, args.env_file)
            validate(config)
        check_gpus(config)
        network = config["networks"]["ai-shared"]["name"]
        if run("docker", "network", "inspect", network, check=False).returncode:
            if args.action != "prepare" or args.check_only:
                raise ConfigError(
                    "Shared-сеть отсутствует; выполните первоначальную настройку."
                )
            run("docker", "network", "create", network)
        if args.action == "prepare":
            print("PREPARE OK")
            return 0
        if args.action == "reboot-verify":
            actual = wait_healthy(
                config["name"], set(config["services"]), args.wait_timeout
            )
        success = check_services(config, actual)
        if not success:
            print("ONE OR MORE CHECKS FAILED")
            return 1
        if args.action in ("reboot-snapshot", "reboot-verify"):
            if not args.state_file:
                raise ConfigError("Для reboot-проверки требуется --state-file.")
            path = Path(args.state_file)
            current = restart_state(actual)
            if args.action == "reboot-snapshot":
                path.write_text(json.dumps(current, indent=2), encoding="utf-8")
                print(f"REBOOT SNAPSHOT SAVED: {path}")
                return 0
            before = json.loads(path.read_text(encoding="utf-8"))
            verify_restart(before, current)
            print("REBOOT RECOVERY VERIFIED")
            return 0
        print("ALL CHECKS PASSED")
        return 0
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
        message = (
            str(exc)
            if isinstance(exc, ConfigError)
            else "Не удалось проверить конфигурацию/Docker; проверьте локально."
        )
        print(f"[ERROR] {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
