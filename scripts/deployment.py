#!/usr/bin/env python3
# scripts/deployment.py
"""Общие проверки итоговой конфигурации Compose без собственного dotenv-парсера.

Окружение команды > .env > разрешённые defaults. Закрытый Compose JSON и inspect
не выводятся. Bootstrap может создать только указанную сеть; check ничего не меняет.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AI_PROFILES = {"shared-vlm": "ai-vlm", "shared-embedding": "ai-embedding", "open-webui": "ai-ui"}
GPU_PLACEMENT = {"shared-vlm": "2,3", "shared-embedding": "1"}


class DeploymentError(RuntimeError):
    """Сообщает безопасную причину отказа без значений секретов."""


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Выполняет команду в корне проекта без раскрытия её закрытого вывода."""
    try:
        result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise DeploymentError("Не удалось выполнить требуемую программу.") from exc
    if check and result.returncode:
        raise DeploymentError("Команда Docker завершилась ошибкой; проверьте конфигурацию локально без публикации секретов.")
    return result


def compose_args(profiles: list[str] | None = None, env_file: str | None = None) -> list[str]:
    """Сохраняет штатный выбор файлов и приоритет настроек Docker Compose."""
    args = ["docker", "compose"]
    if env_file:
        args.extend(["--env-file", env_file])
    for profile in profiles or []:
        args.extend(["--profile", profile])
    return args


def load_config(profiles: list[str] | None = None, env_file: str | None = None) -> dict:
    """Получает выбранные сервисы и разрешённые значения из самого Compose."""
    result = run(*compose_args(profiles, env_file), "config", "--format", "json")
    try:
        config = json.loads(result.stdout)
        if not isinstance(config.get("services"), dict) or not config.get("name"):
            raise ValueError
        return config
    except (ValueError, TypeError, AttributeError) as exc:
        raise DeploymentError("Compose не вернул корректную конфигурацию.") from exc


def value_after(command: list[str], flag: str) -> str:
    """Возвращает единственный аргумент флага; неоднозначность отклоняет."""
    if command.count(flag) != 1:
        return ""
    position = command.index(flag) + 1
    return str(command[position]) if position < len(command) else ""


def require_value(name: str, value: object) -> None:
    """Отклоняет пустое обязательное значение и catalog-placeholder."""
    if not isinstance(value, str) or not value.strip() or value.strip().startswith("CHANGE_ME"):
        raise DeploymentError(f"{name}: требуется непустое значение без CHANGE_ME.")


def normalized_command(command: list[str]) -> tuple:
    """Сравнивает известные одиночные CLI-флаги без зависимости от их порядка."""
    boolean = {"--enable-auto-tool-choice"}
    valued = {"--model", "--revision", "--served-model-name", "--host", "--port",
              "--tensor-parallel-size", "--data-parallel-size", "--max-model-len",
              "--gpu-memory-utilization", "--max-num-seqs", "--max-num-queued-reqs",
              "--max-num-queued-tokens", "--tool-call-parser", "--reasoning-parser",
              "--spec-method", "--spec-tokens", "--quantization"}
    result = {}
    index = 0
    while index < len(command):
        flag = command[index]
        if flag in result:
            return ("unparsed", tuple(command))
        if flag in boolean:
            result[flag] = ""
            index += 1
        elif flag in valued and index + 1 < len(command):
            result[flag] = command[index + 1]
            index += 2
        else:
            return ("unparsed", tuple(command))
    return tuple(sorted(result.items()))


def gpu_layout(name: str, service: dict) -> list[str]:
    """Проверяет размещение GPU и соответствие их количества TP × DP."""
    devices = str(service.get("environment", {}).get("CUDA_VISIBLE_DEVICES", ""))
    ids = devices.split(",")
    command = service.get("command") or []
    try:
        tp = int(value_after(command, "--tensor-parallel-size"))
        dp = int(value_after(command, "--data-parallel-size"))
        valid = tp > 0 and dp > 0 and len(ids) == tp * dp
    except (ValueError, TypeError):
        valid = False
    if devices != GPU_PLACEMENT[name] or len(set(ids)) != len(ids) or not valid:
        raise DeploymentError(f"{name}: требуются GPU {GPU_PLACEMENT[name]} и согласованные TP/DP.")
    return ids


def validate_config(config: dict) -> None:
    """Проверяет выбранные сервисы по уже разрешённым Compose значениям."""
    for name, service in config["services"].items():
        env = service.get("environment") or {}
        if "logging" in service:
            policy = service["logging"]
            if not bounded_logging({"HostConfig": {"LogConfig": {"Type": policy.get("driver"), "Config": policy.get("options")}}}):
                raise DeploymentError(f"{name}: требуется конечная согласованная политика логов.")
        required = {
            "n8n-db": ("POSTGRES_PASSWORD",),
            "n8n": ("DB_POSTGRESDB_PASSWORD", "N8N_ENCRYPTION_KEY"),
            "rabbitmq": ("RABBITMQ_DEFAULT_PASS",),
            "open-webui": ("WEBUI_SECRET_KEY",),
        }.get(name, ())
        for key in required:
            require_value(f"{name}.{key}", env.get(key))
        if name in GPU_PLACEMENT:
            command = service.get("command") or []
            protected = ("--model", "--revision", "--served-model-name", "--tensor-parallel-size", "--data-parallel-size", "--max-model-len", "--host", "--port")
            if any(arg.startswith(flag + "=") for arg in command for flag in protected):
                raise DeploymentError(f"{name}: дополнительные аргументы повторяют deployment-параметры.")
            for flag in ("--model", "--revision", "--served-model-name", "--max-model-len"):
                require_value(f"{name}.{flag}", value_after(command, flag))
            if value_after(command, "--served-model-name") != name:
                raise DeploymentError(f"{name}: логический alias изменять нельзя.")
            if any(arg == "--api-key" or arg.startswith("--api-key=") for arg in command):
                raise DeploymentError(f"{name}: API-key не входит в текущий контракт.")
            try:
                if int(value_after(command, "--max-model-len")) < 1:
                    raise ValueError
            except ValueError as exc:
                raise DeploymentError(f"{name}: требуется положительный max-model-len из deployment.") from exc
            gpu_layout(name, service)
        if name == "open-webui" and "shared-vlm" not in config["services"]:
            raise DeploymentError("ai-ui требует также ai-vlm.")


def network_name(config: dict) -> str:
    """Берёт имя внешней сети из Compose, не вводя второго default."""
    try:
        return config["networks"]["ai-shared"]["name"]
    except KeyError as exc:
        raise DeploymentError("Не найдена согласованная внешняя сеть ai-shared.") from exc


def check_gpus(config: dict) -> None:
    """Проверяет NVIDIA только для ожидаемых GPU-сервисов."""
    needed = {gpu for name, service in config["services"].items()
              if name in GPU_PLACEMENT for gpu in gpu_layout(name, service)}
    if not needed:
        print("[SKIP] GPU-сервисы не выбраны.")
        return
    result = run("nvidia-smi", "--query-gpu=index", "--format=csv,noheader,nounits", check=False)
    if result.returncode or not needed.issubset({line.strip() for line in result.stdout.splitlines()}):
        raise DeploymentError("NVIDIA или требуемые GPU недоступны.")
    print("[OK] Требуемые GPU доступны.")


def container_data(project: str) -> dict[str, dict]:
    """Читает контейнеры нужного проекта; закрытый inspect не выводит."""
    ids = run("docker", "ps", "-a", "--filter",
              f"label=com.docker.compose.project={project}", "--format", "{{.ID}}")
    if not ids.stdout.strip():
        return {}
    try:
        result = {}
        for container in json.loads(run("docker", "inspect", *ids.stdout.split()).stdout):
            name = container["Config"]["Labels"].get("com.docker.compose.service", "")
            if name in result:
                raise DeploymentError("Найдено несколько контейнеров одного shared-сервиса.")
            result[name] = container
        return result
    except (ValueError, KeyError, TypeError) as exc:
        raise DeploymentError("Не удалось прочитать состояние контейнеров.") from exc


def endpoint(service: dict, target: int, path: str) -> str:
    """Строит локальный URL по опубликованному порту итогового Compose JSON."""
    for port in service.get("ports", []):
        if int(port["target"]) == target:
            host = port.get("host_ip", "0.0.0.0")
            host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)
            if ":" in host:
                host = f"[{host}]"
            return f"http://{host}:{port['published']}{path}"
    raise DeploymentError("Не найден ожидаемый опубликованный порт.")


def http_check(url: str, credentials: tuple[str, str] | None = None) -> bytes:
    """Проверяет HTTP с timeout, не раскрывая credentials или тело ошибки."""
    request = urllib.request.Request(url)
    if credentials:
        token = base64.b64encode(":".join(credentials).encode()).decode()
        request.add_header("Authorization", "Basic " + token)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise DeploymentError("HTTP-проверка сервиса не прошла.") from exc


def bounded_logging(container: dict) -> bool:
    """Проверяет конечные лимиты local/json-file без вывода прочих log options."""
    policy = container.get("HostConfig", {}).get("LogConfig", {})
    options = policy.get("Config") or {}
    if policy.get("Type") not in ("local", "json-file"):
        return False
    try:
        return (int(options.get("max-file", "0")) > 0
                and bool(re.fullmatch(r"[1-9][0-9]*[kmg]?", str(options.get("max-size", "")), re.I)))
    except (ValueError, TypeError):
        return False


def runtime_matches(service: dict, container: dict) -> bool:
    """Сравнивает image, command и важные настройки AI с контейнером."""
    actual = container.get("Config", {})
    env = dict(item.split("=", 1) for item in actual.get("Env", []) if "=" in item)
    desired = service.get("environment") or {}
    return (actual.get("Image") == service.get("image")
            and normalized_command(actual.get("Cmd") or []) == normalized_command(service.get("command") or [])
            and all(env.get(key, "") == str(desired.get(key, ""))
                    for key in ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "VLLM_BATCH_INVARIANT")))


def check_services(config: dict, containers: dict[str, dict]) -> bool:
    """Проверяет каждый ожидаемый сервис: readiness, API и конечные логи."""
    failed = False
    for name, service in config["services"].items():
        try:
            actual = containers.get(name)
            if not actual or not actual.get("State", {}).get("Running"):
                raise DeploymentError("Ожидаемый контейнер не запущен.")
            health = actual["State"].get("Health", {}).get("Status")
            if health and health != "healthy":
                raise DeploymentError("Контейнер ещё не healthy.")
            if name in GPU_PLACEMENT:
                if not runtime_matches(service, actual):
                    raise DeploymentError("Параметры AI отличаются от проверяемой конфигурации.")
                http_check(endpoint(service, 8000, "/health"))
                models = json.loads(http_check(endpoint(service, 8000, "/v1/models")))
                if name not in {model.get("id") for model in models.get("data", [])}:
                    raise DeploymentError("API не публикует logical alias.")
                http_check(endpoint(service, 8000, "/metrics"))
            elif name in ("open-webui", "n8n"):
                http_check(endpoint(service, 8080 if name == "open-webui" else 5678,
                                    "/health" if name == "open-webui" else "/healthz"))
            elif name == "n8n-db":
                env = service["environment"]
                run("docker", "exec", actual["Id"], "pg_isready",
                    "-U", env["POSTGRES_USER"], "-d", env["POSTGRES_DB"])
            elif name == "rabbitmq":
                run("docker", "exec", actual["Id"], "rabbitmq-diagnostics", "-q", "ping")
                env = service["environment"]
                http_check(endpoint(service, 15672, "/api/overview"),
                           (env["RABBITMQ_DEFAULT_USER"], env["RABBITMQ_DEFAULT_PASS"]))
            if not bounded_logging(actual):
                raise DeploymentError("Конечные ограничения логов не подтверждены.")
            print(f"[OK] {name}")
        except (DeploymentError, ValueError, KeyError, TypeError) as exc:
            failed = True
            # Выводим только наши безопасные сообщения, а не repr внешнего ответа.
            reason = str(exc) if isinstance(exc, DeploymentError) else "Некорректный ответ."
            print(f"[FAIL] {name}: {reason}")
    return not failed


def main(argv: list[str] | None = None) -> int:
    """Выполняет проверку; сеть создаётся только после успешного preflight."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "bootstrap", "check", "compare-runtime"))
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--env-file")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.profile, args.env_file)
        validate_config(config)
        if args.action == "validate":
            print("COMPOSE OK: проверены итоговые настройки; секреты не выводятся.")
            return 0
        run("docker", "info")
        containers = container_data(config["name"]) if args.action != "bootstrap" else {}
        extra = [AI_PROFILES[name] for name, data in containers.items()
                 if name in AI_PROFILES and data.get("State", {}).get("Running")
                 and name not in config["services"]]
        if extra:
            config = load_config(args.profile + extra, args.env_file)
            validate_config(config)
        check_gpus(config)
        network = network_name(config)
        exists = run("docker", "network", "inspect", network, check=False).returncode == 0
        if not exists:
            if args.action != "bootstrap" or args.check_only:
                raise DeploymentError("Ожидаемая shared-сеть отсутствует.")
            run("docker", "network", "create", network)
            print("[OK] Создана указанная в Compose shared-сеть.")
        if args.action == "bootstrap":
            print("BOOTSTRAP OK; контейнеры не запускались и не пересоздавались.")
            return 0
        if args.action == "compare-runtime":
            mismatched = [name for name, service in config["services"].items()
                          if name in GPU_PLACEMENT
                          and not runtime_matches(service, containers.get(name, {}))]
            if mismatched:
                raise DeploymentError("Runtime отличается: " + ", ".join(mismatched) + ". Ничего не изменено.")
            print("[OK] Параметры AI совпадают с Compose.")
            return 0
        success = check_services(config, containers)
        print("ALL CHECKS PASSED" if success else "ONE OR MORE CHECKS FAILED")
        return 0 if success else 1
    except DeploymentError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
