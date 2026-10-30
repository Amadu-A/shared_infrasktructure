#!/usr/bin/env python3
# scripts/config-check.py
"""Проверки Compose, сервисов и закреплённого OCR-артефакта Docling.

Значения разрешает сам Compose: окружение команды > .env > разрешённые defaults.
Закрытые config/inspect остаются в памяти. VLM weights и пользователи не меняются.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCLING_DIR = ROOT / "docling-eval"
DOCLING_COMPOSE_FILE = DOCLING_DIR / "compose.yaml"
DOCLING_TESSDATA_PATH = DOCLING_DIR / "tessdata" / "rus.traineddata"
DOCLING_TESSDATA_URL = (
    "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
    "87416418657359cb625c412a48b6e1d6d41c29bd/rus.traineddata"
)
DOCLING_TESSDATA_SHA256 = (
    "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225"
)
AI_PROFILES = {
    "shared-vlm": "ai-vlm",
    "shared-embedding": "ai-embedding",
    "open-webui": "ai-ui",
}
OPTIONAL_PROFILES = {**AI_PROFILES, "searxng": "ai-search"}
INFERENCE_SERVICES = {"shared-vlm", "shared-embedding"}
LEGACY_HELPERS = ("vlm-entrypoint.py", "deployment.py", "open-webui-entrypoint.sh")
SERVICE_ORDER = ("shared-vlm", "shared-embedding", "open-webui")
SECRETS = {
    "n8n-db": ("POSTGRES_PASSWORD",),
    "n8n": ("DB_POSTGRESDB_PASSWORD", "N8N_ENCRYPTION_KEY"),
    "rabbitmq": ("RABBITMQ_DEFAULT_PASS",),
    "open-webui": ("WEBUI_SECRET_KEY",),
    "searxng": ("SEARXNG_SECRET",),
    "docling": ("DOCLING_SERVE_API_KEY",),
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


def docling_compose_args(env_file: str | None) -> list[str]:
    """Строит изолированную Compose-команду Docling с тем же env precedence."""
    args = [
        "docker",
        "compose",
        "--project-name",
        "docling-eval",
        "--project-directory",
        str(DOCLING_DIR),
    ]
    effective_env_file = Path(env_file) if env_file else ROOT / ".env"
    if env_file or effective_env_file.is_file():
        args += ["--env-file", str(effective_env_file)]
    return [*args, "-f", str(DOCLING_COMPOSE_FILE)]


def load_config(profiles: list[str], env_file: str | None) -> dict:
    """Читает итоговый JSON Compose без отдельного dotenv-парсера."""
    return json.loads(
        run(*compose_args(profiles, env_file), "config", "--format", "json").stdout
    )


def load_docling_config(env_file: str | None) -> dict:
    """Читает итоговый JSON отдельного Docling Compose без dotenv-парсера."""
    return json.loads(
        run(*docling_compose_args(env_file), "config", "--format", "json").stdout
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


def validate_docling(config: dict) -> None:
    """Проверяет изоляцию, secret и read-only OCR-модель Docling Compose."""
    validate(config)
    if config.get("name") != "docling-eval" or set(config["services"]) != {"docling"}:
        raise ConfigError("Docling должен использовать отдельный project name.")
    service = config.get("services", {}).get("docling")
    if not isinstance(service, dict):
        raise ConfigError("Docling Compose не содержит обязательный сервис docling.")
    environment = service.get("environment") or {}
    if str(environment.get("DOCLING_SERVE_ENABLE_REMOTE_SERVICES", "")).lower() != (
        "false"
    ):
        raise ConfigError("Docling remote services должны быть отключены.")
    bindings = service.get("ports", [])
    if len(bindings) != 1 or any(
        item.get("host_ip") != "127.0.0.1" or int(item.get("target", 0)) != 5001
        for item in bindings
    ):
        raise ConfigError("Docling host port должен быть привязан к 127.0.0.1.")
    if environment.get("DOCLING_DEVICE") != "cpu":
        raise ConfigError("Docling evaluation должен использовать CPU.")
    mount = next(
        (
            item
            for item in service.get("volumes", [])
            if item.get("target") == "/usr/share/tesseract/tessdata/rus.traineddata"
        ),
        None,
    )
    if not mount or not mount.get("read_only") or mount.get("type") != "bind":
        raise ConfigError("Русская tessdata должна монтироваться read-only bind mount.")
    if Path(str(mount.get("source", ""))).resolve() != DOCLING_TESSDATA_PATH.resolve():
        raise ConfigError(
            "Docling должен монтировать только закреплённый rus.traineddata."
        )
    if (mount.get("bind") or {}).get("create_host_path") is not False:
        raise ConfigError(
            "Compose не должен подменять отсутствующую tessdata каталогом."
        )
    if "ALL" not in service.get("cap_drop", []):
        raise ConfigError("Docling должен запускаться без Linux capabilities.")
    if "no-new-privileges:true" not in service.get("security_opt", []):
        raise ConfigError("Docling должен запрещать повышение privileges.")
    network = config.get("networks", {}).get("ai-shared") or {}
    if not network.get("external"):
        raise ConfigError(
            "Docling должен использовать существующую external ai-shared."
        )


def print_settings(config: dict) -> None:
    """Показывает разрешённые поля итогового Compose и наличие secrets без их значений."""
    print(f"[SETTINGS] project={json.dumps(config['name'])}")
    print(f"[SETTINGS] services={','.join(sorted(config['services']))}")
    network = config.get("networks", {}).get("ai-shared", {}).get("name")
    print(f"[SETTINGS] SHARED_NETWORK_NAME={json.dumps(network)}")
    for name, service in config["services"].items():
        for port in service.get("ports", []):
            binding = {key: port.get(key) for key in ("host_ip", "published", "target")}
            print(f"[SETTINGS] {name}.port={json.dumps(binding)}")
        for key in SECRETS.get(name, ()):
            state = "set" if (service.get("environment") or {}).get(key) else "not set"
            print(f"[SETTINGS] {name}.{key}=<redacted: {state}>")


def file_sha256(path: Path) -> str:
    """Вычисляет SHA-256 локального model artifact потоково."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_docling_tessdata(check_only: bool) -> None:
    """Проверяет или атомарно скачивает закреплённую русскую модель Tesseract."""
    if (
        DOCLING_TESSDATA_PATH.is_file()
        and DOCLING_TESSDATA_PATH.stat().st_size > 0
        and file_sha256(DOCLING_TESSDATA_PATH) == DOCLING_TESSDATA_SHA256
    ):
        if not check_only:
            DOCLING_TESSDATA_PATH.chmod(0o644)
        print(f"DOCLING TESSDATA OK: {DOCLING_TESSDATA_SHA256}")
        return
    if check_only:
        raise ConfigError(
            "Docling rus.traineddata отсутствует или не совпадает с закреплённым SHA-256."
        )
    DOCLING_TESSDATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DOCLING_TESSDATA_PATH.with_name(
        f"{DOCLING_TESSDATA_PATH.name}.tmp.{os.getpid()}"
    )
    try:
        last_error: OSError | None = None
        for _attempt in range(3):
            try:
                with (
                    urllib.request.urlopen(
                        DOCLING_TESSDATA_URL, timeout=120
                    ) as response,
                    temporary.open("wb") as output,
                ):
                    shutil.copyfileobj(response, output)
                break
            except OSError as exc:
                last_error = exc
                temporary.unlink(missing_ok=True)
        else:
            raise ConfigError(
                "Не удалось скачать закреплённый Docling rus.traineddata после трёх попыток."
            ) from last_error
        if temporary.stat().st_size <= 0:
            raise ConfigError("Скачанный Docling rus.traineddata пуст.")
        actual = file_sha256(temporary)
        if actual != DOCLING_TESSDATA_SHA256:
            raise ConfigError(
                "SHA-256 скачанного Docling rus.traineddata не совпадает с закреплённым."
            )
        temporary.chmod(0o644)
        temporary.replace(DOCLING_TESSDATA_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"DOCLING TESSDATA DOWNLOADED: {DOCLING_TESSDATA_SHA256}")


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


def check_docling(service: dict, item: dict) -> None:
    """Проверяет фактический доступ, secret, health и языки Docling без recreate."""
    current = item.get("Config") or {}
    environment = dict(
        value.split("=", 1) for value in current.get("Env", []) if "=" in value
    )
    if current.get("Image") != service.get("image") or any(
        environment.get(key) != str(value)
        for key, value in (service.get("environment") or {}).items()
    ):
        raise ConfigError("Docling image/environment отличаются от Compose.")
    host = item.get("HostConfig") or {}
    binding = service["ports"][0]
    expected_ports = {
        "5001/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(binding["published"])}]
    }
    if host.get("PortBindings") != expected_ports:
        raise ConfigError("Docling фактически публикует другой host port/bind IP.")
    if "ALL" not in host.get("CapDrop", []) or not any(
        option in host.get("SecurityOpt", [])
        for option in ("no-new-privileges", "no-new-privileges:true")
    ):
        raise ConfigError(
            "Docling фактически запущен без согласованных security options."
        )
    if not any(
        mount.get("Destination") == "/usr/share/tesseract/tessdata/rus.traineddata"
        and mount.get("Type") == "bind"
        and mount.get("RW") is False
        for mount in item.get("Mounts", [])
    ):
        raise ConfigError(
            "Docling фактически не использует read-only tessdata bind mount."
        )
    http_get(url(service, 5001, "/health"))
    result = run(
        "docker",
        "exec",
        item["Id"],
        "tesseract",
        "--list-langs",
        check=False,
    )
    languages = set(result.stdout.split())
    if result.returncode or not {"rus", "eng", "osd"}.issubset(languages):
        raise ConfigError("Docling не видит обязательные языки Tesseract.")


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
            elif name == "docling":
                check_docling(service, item)
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
    """Координирует безопасные prepare/check для основного stack и Docling."""
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
                "docling-validate",
                "docling-prepare",
                "docling-start",
                "docling-check",
                "migration-plan",
                "reboot-snapshot",
                "reboot-verify",
            ),
        )
        parser.add_argument("--profile", action="append", default=[])
        parser.add_argument("--env-file")
        parser.add_argument("--check-only", action="store_true")
        parser.add_argument("--show-settings", action="store_true")
        parser.add_argument("--state-file")
        parser.add_argument("--wait-timeout", type=int, default=1800)
        args = parser.parse_args(argv)
        docling_actions = {
            "docling-validate",
            "docling-prepare",
            "docling-start",
            "docling-check",
        }
        if args.action in docling_actions:
            config = load_docling_config(args.env_file)
            validate_docling(config)
            if args.show_settings:
                print_settings(config)
            if args.action == "docling-validate":
                print("DOCLING CONFIG OK")
                return 0
            run("docker", "info")
            network = config["networks"]["ai-shared"]["name"]
            if run("docker", "network", "inspect", network, check=False).returncode:
                raise ConfigError(
                    "Shared-сеть Docling отсутствует; сначала выполните prepare основного stack."
                )
            prepare_docling_tessdata(
                check_only=args.check_only or args.action == "docling-check"
            )
            if args.action == "docling-prepare" or (
                args.action == "docling-start" and args.check_only
            ):
                print("DOCLING PREPARE OK")
                return 0
            if args.action == "docling-start":
                run(
                    *docling_compose_args(args.env_file),
                    "up",
                    "-d",
                    "--no-recreate",
                    "--wait",
                    "--wait-timeout",
                    str(args.wait_timeout),
                )
            actual = containers(config["name"])
            if not check_services(config, actual):
                print("ONE OR MORE CHECKS FAILED")
                return 1
            print("DOCLING CHECK PASSED")
            return 0
        if args.action in ("migration-plan", "reboot-snapshot", "reboot-verify"):
            args.profile = list(
                dict.fromkeys(args.profile + list(AI_PROFILES.values()))
            )
        config = load_config(args.profile, args.env_file)
        validate(config)
        if args.show_settings:
            print_settings(config)
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
            OPTIONAL_PROFILES[name]
            for name, item in actual.items()
            if name in OPTIONAL_PROFILES
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
            docling_config = load_docling_config(args.env_file)
            validate_docling(docling_config)
            prepare_docling_tessdata(check_only=True)
            if args.action == "reboot-verify":
                docling_actual = wait_healthy(
                    docling_config["name"],
                    set(docling_config["services"]),
                    args.wait_timeout,
                )
            else:
                docling_actual = containers(docling_config["name"])
            if not check_services(docling_config, docling_actual):
                print("ONE OR MORE CHECKS FAILED")
                return 1
            if not args.state_file:
                raise ConfigError("Для reboot-проверки требуется --state-file.")
            path = Path(args.state_file)
            current = restart_state(actual)
            current.update(
                {
                    f"docling-eval/{name}": state
                    for name, state in restart_state(docling_actual).items()
                }
            )
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
