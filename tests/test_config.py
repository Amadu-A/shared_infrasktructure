# tests/test_config.py
"""Небольшая регрессия настроек Compose и отказа до побочных действий."""

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import urllib.error
from contextlib import nullcontext
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "config_check", ROOT / "scripts/config-check.py"
)
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


@pytest.fixture
def render(tmp_path):
    """Разрешает настройки самим Compose с временным env, без серверного .env."""

    def invoke(values=None, process=None, profiles=()):
        """Возвращает config JSON; контейнеры и Docker daemon не используются."""
        if not shutil.which("docker"):
            pytest.skip("Для интерполяции нужен Docker Compose CLI")
        names = set(
            re.findall(
                r"^([A-Z][A-Z0-9_]*)=",
                (ROOT / ".env.example").read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in names and not k.startswith("COMPOSE_")
        }
        env.update(process or {})
        settings = {
            "N8N_DB_PASSWORD": "test-db",
            "N8N_ENCRYPTION_KEY": "test-key",
            "RABBITMQ_DEFAULT_PASS": "test-broker",
            "SEARXNG_SECRET": "test-search",
        }
        settings.update(values or {})
        fixture = tmp_path / "fixture.env"
        fixture.write_text(
            "\n".join(f"{k}={v}" for k, v in settings.items()), encoding="utf-8"
        )
        args = [
            "docker",
            "compose",
            "--project-directory",
            str(tmp_path),
            "--env-file",
            str(fixture),
            "-f",
            str(ROOT / "compose.yaml"),
        ]
        for profile in profiles:
            args += ["--profile", profile]
        result = subprocess.run(
            [*args, "config", "--format", "json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, "Compose отверг тестовую конфигурацию"
        return json.loads(result.stdout)

    return invoke


@pytest.fixture
def render_docling(tmp_path):
    """Разрешает отдельный Docling Compose с тестовым secret и без daemon."""

    def invoke(values=None, process=None):
        """Возвращает JSON Docling Compose с изолированным project name."""
        if not shutil.which("docker"):
            pytest.skip("Для интерполяции нужен Docker Compose CLI")
        settings = {"DOCLING_API_KEY": "test-docling-key"}
        settings.update(values or {})
        fixture = tmp_path / "docling.env"
        fixture.write_text(
            "\n".join(f"{key}={value}" for key, value in settings.items()),
            encoding="utf-8",
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("DOCLING_") and not key.startswith("COMPOSE_")
        }
        env.update(process or {})
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                "docling-eval",
                "--project-directory",
                str(ROOT / "docling-eval"),
                "--env-file",
                str(fixture),
                "-f",
                str(ROOT / "docling-eval/compose.yaml"),
                "config",
                "--format",
                "json",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, "Compose отверг Docling-конфигурацию"
        return json.loads(result.stdout)

    return invoke


@pytest.mark.parametrize(
    "profiles",
    [
        (),
        ("ai-vlm",),
        ("ai-embedding",),
        ("ai-search",),
        ("ai-vlm", "ai-embedding", "ai-ui", "ai-search"),
    ],
)
def test_profiles_and_command_precedence(render, profiles):
    """Команда выше env-файла; defaults и опциональные сервисы разрешает Compose."""
    values = {
        "SHARED_VLM_TP_SIZE": "2",
        "SHARED_VLM_DP_SIZE": "1",
        "SHARED_VLM_MAX_MODEL_LEN": "8192",
        "OPEN_WEBUI_SECRET_KEY": "test-ui",
        "SHARED_VLM_HOST_PORT": "8111",
    }
    resolved = render(values, {"SHARED_VLM_HOST_PORT": "8222"}, profiles)
    config.validate(resolved)
    for name, profile in config.AI_PROFILES.items():
        assert (name in resolved["services"]) == (profile in profiles)
    if "ai-vlm" in profiles:
        service = resolved["services"]["shared-vlm"]
        assert config.url(service, 8000, "/health") == "http://127.0.0.1:8222/health"
        assert config.argument(service["command"], "--model") == "Qwen/Qwen3.8-27B"
        assert (
            render(values, profiles=profiles)["services"]["shared-vlm"]["ports"][0][
                "published"
            ]
            == "8111"
        )
        del values["SHARED_VLM_HOST_PORT"]
        assert (
            render(values, profiles=profiles)["services"]["shared-vlm"]["ports"][0][
                "published"
            ]
            == "8000"
        )
    if "ai-search" in profiles:
        assert "searxng" in resolved["services"]
    if "ai-ui" in profiles:
        catalog_image = re.search(
            r"^OPEN_WEBUI_IMAGE=(.+)$",
            (ROOT / ".env.example").read_text(encoding="utf-8"),
            re.MULTILINE,
        )[1].strip()
        assert resolved["services"]["open-webui"]["image"] == catalog_image


def test_docling_isolated_security_and_precedence(render_docling):
    """Docling остаётся отдельным, loopback-only и получает process override."""
    resolved = render_docling(
        {"DOCLING_HOST_PORT": "5111"}, {"DOCLING_HOST_PORT": "5222"}
    )
    config.validate_docling(resolved)
    service = resolved["services"]["docling"]
    assert resolved["name"] == "docling-eval"
    assert config.url(service, 5001, "/health") == "http://127.0.0.1:5222/health"
    assert service["environment"]["DOCLING_SERVE_ENABLE_REMOTE_SERVICES"] == "false"
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]


def test_docling_rejects_placeholder_secret(render_docling):
    """Каталожный placeholder API key не считается deployment-secret."""
    resolved = render_docling({"DOCLING_API_KEY": "CHANGE_ME"})
    with pytest.raises(config.ConfigError):
        config.validate_docling(resolved)


def test_docling_settings_do_not_print_secret(render_docling, capsys):
    """Сводка показывает адрес и наличие ключа, но не сам ключ."""
    resolved = render_docling({"DOCLING_API_KEY": "private-test-key"})
    config.print_settings(resolved)
    output = capsys.readouterr().out
    assert "private-test-key" not in output
    assert "<redacted: set>" in output
    assert "127.0.0.1" in output


def test_docling_rejects_additional_public_binding(render_docling):
    """Loopback endpoint не разрешает второй опубликованный порт."""
    resolved = render_docling()
    resolved["services"]["docling"]["ports"].append(
        {"target": 5001, "published": "5999", "host_ip": "0.0.0.0"}
    )
    with pytest.raises(config.ConfigError):
        config.validate_docling(resolved)


@pytest.mark.parametrize("bind", [{}, {"create_host_path": False}])
def test_docling_accepts_false_omitted_by_compose_v2(render_docling, bind):
    """Валидатор принимает оба JSON-представления create_host_path: false."""
    resolved = render_docling()
    resolved["services"]["docling"]["volumes"][0]["bind"] = bind
    config.validate_docling(resolved)


def test_docling_rejects_host_path_creation(render_docling):
    """Совместимость с Compose v2 не разрешает создавать каталог вместо tessdata."""
    resolved = render_docling()
    resolved["services"]["docling"]["volumes"][0]["bind"] = {"create_host_path": True}
    with pytest.raises(config.ConfigError):
        config.validate_docling(resolved)


def test_docling_tessdata_download_and_idempotency(tmp_path, monkeypatch):
    """Tessdata скачивается атомарно, проверяется и повторно не загружается."""
    payload = b"pinned-russian-tessdata"
    model = tmp_path / "tessdata/rus.traineddata"
    monkeypatch.setattr(config, "DOCLING_TESSDATA_PATH", model)
    monkeypatch.setattr(
        config, "DOCLING_TESSDATA_SHA256", hashlib.sha256(payload).hexdigest()
    )
    calls = 0

    def download(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OSError("temporary download failure")
        return io.BytesIO(payload)

    monkeypatch.setattr(config.urllib.request, "urlopen", download)
    config.prepare_docling_tessdata(check_only=False)
    assert model.read_bytes() == payload
    config.prepare_docling_tessdata(check_only=True)
    assert calls == 3
    model.write_bytes(b"corrupt")
    with pytest.raises(config.ConfigError):
        config.prepare_docling_tessdata(check_only=True)
    missing = tmp_path / "missing/tessdata/rus.traineddata"
    monkeypatch.setattr(config, "DOCLING_TESSDATA_PATH", missing)
    with pytest.raises(config.ConfigError):
        config.prepare_docling_tessdata(check_only=True)
    assert not missing.parent.exists()


def test_docling_bad_download_preserves_existing_file(tmp_path, monkeypatch):
    """Ошибка hash не заменяет существующий файл и не оставляет partial download."""
    model = tmp_path / "rus.traineddata"
    model.write_bytes(b"existing")
    monkeypatch.setattr(config, "DOCLING_TESSDATA_PATH", model)
    monkeypatch.setattr(config, "DOCLING_TESSDATA_SHA256", "0" * 64)
    monkeypatch.setattr(
        config.urllib.request, "urlopen", lambda *_a, **_kw: io.BytesIO(b"bad")
    )
    with pytest.raises(config.ConfigError):
        config.prepare_docling_tessdata(check_only=False)
    assert model.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [model]


@pytest.fixture
def docling_runtime():
    """Даёт согласованную пару Compose/inspect для проверки доступа Docling."""
    service = {
        "image": "docling:test",
        "environment": {"DOCLING_SERVE_API_KEY": "test-runtime-key"},
        "ports": [{"target": 5001, "published": 5001, "host_ip": "127.0.0.1"}],
    }
    item = {
        "Id": "docling-container",
        "Config": {
            "Image": "docling:test",
            "Env": ["DOCLING_SERVE_API_KEY=test-runtime-key"],
        },
        "HostConfig": {
            "PortBindings": {"5001/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5001"}]},
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "Mounts": [
            {
                "Destination": "/usr/share/tesseract/tessdata/rus.traineddata",
                "Type": "bind",
                "RW": False,
            }
        ],
    }
    return service, item


def test_docling_runtime_requires_all_tesseract_languages(monkeypatch, docling_runtime):
    """Runtime-check принимает только контейнер с rus, eng и osd."""
    service, item = docling_runtime
    monkeypatch.setattr(config, "http_get", lambda _address: b"ok")
    monkeypatch.setattr(
        config,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            _args, 0, stdout="List of available languages:\neng\nosd\nrus\n"
        ),
    )
    config.check_docling(service, item)
    monkeypatch.setattr(
        config,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            _args, 0, stdout="eng\nosd\n"
        ),
    )
    with pytest.raises(config.ConfigError):
        config.check_docling(service, item)


@pytest.mark.parametrize("drift", ["secret", "binding", "mount"])
def test_docling_runtime_rejects_security_drift(monkeypatch, docling_runtime, drift):
    """Старый контейнер с другим ключом/публичным портом/RW mount не проходит check."""
    service, item = docling_runtime
    if drift == "secret":
        item["Config"]["Env"] = ["DOCLING_SERVE_API_KEY=old-key"]
    elif drift == "binding":
        item["HostConfig"]["PortBindings"]["5001/tcp"][0]["HostIp"] = "0.0.0.0"
    else:
        item["Mounts"][0]["RW"] = True
    monkeypatch.setattr(
        config, "http_get", lambda _a: pytest.fail("API вызван до проверки доступа")
    )
    with pytest.raises(config.ConfigError):
        config.check_docling(service, item)


@pytest.mark.parametrize("check_only", [False, True])
def test_docling_start_uses_only_isolated_compose(
    render_docling, monkeypatch, check_only
):
    """Запуск изолирован; --check-only запрещает up и сохраняет read-only подготовку."""
    resolved = render_docling()
    monkeypatch.setattr(config, "load_docling_config", lambda _file: resolved)
    preparations = []
    monkeypatch.setattr(
        config,
        "prepare_docling_tessdata",
        lambda **kwargs: preparations.append(kwargs),
    )
    monkeypatch.setattr(config, "containers", lambda _project: {})
    monkeypatch.setattr(config, "check_services", lambda *_args: True)
    commands = []

    def record(*args, **_kwargs):
        """Записывает команды без обращения к Docker daemon."""
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="")

    monkeypatch.setattr(config, "run", record)
    argv = ["docling-start", "--env-file", "deployment.env"]
    if check_only:
        argv.append("--check-only")
    assert config.main(argv) == 0
    assert preparations == [{"check_only": check_only}]
    if check_only:
        assert all("up" not in command for command in commands)
        return
    startup = next(command for command in commands if "up" in command)
    assert startup[startup.index("--project-name") + 1] == "docling-eval"
    assert startup[startup.index("--env-file") + 1] == "deployment.env"
    assert startup[startup.index("-f") + 1] == str(config.DOCLING_COMPOSE_FILE)
    assert "--no-recreate" in startup
    assert "--remove-orphans" not in startup


def test_missing_required_setting_stops_before_changes(render, monkeypatch):
    """Отсутствующий TP не получает выдуманный default и блокирует prepare до Docker info."""
    resolved = render(profiles=("ai-vlm",))
    monkeypatch.setattr(config, "load_config", lambda *a: resolved)
    monkeypatch.setattr(
        config,
        "run",
        lambda *a, **kw: pytest.fail("Побочный вызов после неверных настроек"),
    )
    assert config.main(["prepare"]) == 2


def test_no_nvidia_without_inference(monkeypatch):
    """Base stack не обращается к NVIDIA."""
    monkeypatch.setattr(
        config, "run", lambda *a, **kw: pytest.fail("Лишняя проверка NVIDIA")
    )
    config.check_gpus({"services": {}})


def test_gpu_layout_comes_from_compose_not_python(render):
    """Любые существующие deployment GPU допустимы, если их число равно TP × DP."""
    resolved = render(
        {
            "SHARED_VLM_GPU_DEVICES": "4,5",
            "SHARED_VLM_TP_SIZE": "2",
            "SHARED_VLM_DP_SIZE": "1",
            "SHARED_VLM_MAX_MODEL_LEN": "8192",
        },
        profiles=("ai-vlm",),
    )
    config.validate(resolved)
    assert config.gpu_devices(resolved["services"]["shared-vlm"], "shared-vlm") == [
        "4",
        "5",
    ]


def test_missing_expected_container_is_failure():
    """Отсутствующий включённый VLM не считается успешным SKIP."""
    assert not config.check_services({"services": {"shared-vlm": {}}}, {})


@pytest.mark.parametrize("inherited", [None, [], ["image-default"]])
def test_startup_inherits_unspecified_command_and_entrypoint(inherited):
    """Отсутствующие в Compose startup-поля наследуются из image без ложного drift."""
    config.check_startup_config(
        "open-webui",
        {"image": "ui:test", "command": None, "entrypoint": None},
        {"Config": {"Image": "ui:test", "Cmd": inherited, "Entrypoint": inherited}},
    )


@pytest.mark.parametrize("empty", [None, []])
def test_startup_explicit_empty_accepts_docker_null_or_empty(empty):
    """Docker представляет явно пустые command/entrypoint как null или []."""
    config.check_startup_config(
        "shared-vlm",
        {"image": "vlm:test", "command": [], "entrypoint": []},
        {"Config": {"Image": "vlm:test", "Cmd": empty, "Entrypoint": empty}},
    )


@pytest.fixture
def healthy_vlm_runtime():
    """Создаёт согласованные Compose/inspect данные для runtime-регрессии VLM."""
    service = {
        "image": "vlm:test",
        "restart": "unless-stopped",
        "command": ["--model", "expected-model"],
        "entrypoint": ["python3", "guard"],
        "environment": {"CUDA_VISIBLE_DEVICES": "0"},
        "ports": [{"target": 8000, "published": "8000", "host_ip": "127.0.0.1"}],
    }
    item = {
        "Config": {
            "Image": "vlm:test",
            "Cmd": ["--model", "expected-model"],
            "Entrypoint": ["python3", "guard"],
            "Env": ["CUDA_VISIBLE_DEVICES=0"],
        },
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
    }
    return service, item


def test_healthy_vlm_still_checks_logical_model_api(monkeypatch, healthy_vlm_runtime):
    """Совпадающий healthy контейнер проходит только после проверки alias в API."""
    service, item = healthy_vlm_runtime
    addresses = []

    def response(address):
        """Запоминает запросы, не обращаясь к реальному inference endpoint."""
        addresses.append(address)
        return b'{"data":[{"id":"shared-vlm"}]}'

    monkeypatch.setattr(config, "http_get", response)
    assert config.check_services(
        {"services": {"shared-vlm": service}}, {"shared-vlm": item}
    )
    assert addresses == [
        "http://127.0.0.1:8000/health",
        "http://127.0.0.1:8000/v1/models",
    ]


@pytest.mark.parametrize(
    "field", ["Image", "Cmd", "Entrypoint", "CUDA_VISIBLE_DEVICES"]
)
def test_runtime_drift_reports_field_without_values(
    monkeypatch, capsys, healthy_vlm_runtime, field
):
    """Healthy не скрывает настоящий drift; диагностика не выводит command/env значения."""
    service, item = healthy_vlm_runtime
    sensitive = "sensitive-test-value"
    if field == "CUDA_VISIBLE_DEVICES":
        item["Config"]["Env"] = [f"CUDA_VISIBLE_DEVICES={sensitive}"]
    else:
        item["Config"][field] = sensitive if field == "Image" else [sensitive]
    monkeypatch.setattr(
        config, "http_get", lambda *_args: pytest.fail("Drift был проигнорирован")
    )
    assert not config.check_services(
        {"services": {"shared-vlm": service}}, {"shared-vlm": item}
    )
    output = capsys.readouterr().out
    assert field in output
    assert sensitive not in output


def test_healthy_webui_inherits_image_entrypoint(monkeypatch):
    """WebUI с image entrypoint и заданным Compose command не даёт ложный FAIL."""
    service = {
        "image": "ui:test",
        "command": ["python", "guard"],
        "restart": "unless-stopped",
        "ports": [{"target": 8080, "published": "3000", "host_ip": "127.0.0.1"}],
    }
    item = {
        "Config": {
            "Image": "ui:test",
            "Cmd": ["python", "guard"],
            "Entrypoint": ["image-entrypoint"],
        },
        "State": {"Running": True, "Health": {"Status": "healthy"}},
        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
    }
    monkeypatch.setattr(config, "http_get", lambda _address: b"ok")
    assert config.check_services(
        {"services": {"open-webui": service}}, {"open-webui": item}
    )


def test_http_failure_reports_only_path_and_status(monkeypatch):
    """HTTP-диагностика не показывает credentials из URL и текст чужой ошибки."""
    address = "http://user:private-password@localhost:8000/health"

    def fail(*_args, **_kwargs):
        """Имитирует HTTPError, содержащий закрытый URL."""
        raise urllib.error.HTTPError(address, 503, "private-error-body", {}, None)

    monkeypatch.setattr(config.urllib.request, "urlopen", fail)
    with pytest.raises(config.ConfigError) as error:
        config.http_get(address)
    assert str(error.value) == "HTTP /health: сервер вернул код 503."


@pytest.mark.skipif(os.name == "nt", reason="Проверка Linux shell wrapper")
def test_check_wrapper_continues_after_main_stack_failure(tmp_path):
    """Ошибка основного stack не отменяет Docling/Ruff/pytest и сохраняет exit 1."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Нужен Bash")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts/check.sh", scripts / "check.sh")
    binary = tmp_path / "bin"
    binary.mkdir()
    python = binary / "python3"
    python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CHECK_TEST_CALLS"\n'
        'if [ "$2" = "check" ]; then exit 1; fi\nexit 0\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    calls = tmp_path / "calls.txt"
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
    env["CHECK_TEST_CALLS"] = str(calls)
    result = subprocess.run(
        [bash, str(scripts / "check.sh"), "--with-tests"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "scripts/config-check.py check",
        "scripts/config-check.py docling-check",
        "-m ruff check scripts tests",
        "-m ruff format --check scripts tests",
        "-B -m pytest -p no:cacheprovider -q",
    ]


def test_legacy_helper_migration_targets_only_existing_ai_services():
    """Миграция распознаёт старые bind paths и не затрагивает базовые сервисы."""
    actual = {
        "shared-vlm": {
            "Mounts": [{"Type": "bind", "Source": "/repo/scripts/deployment.py"}],
            "Config": {
                "Image": "vllm:test",
                "Cmd": ["--model", "working"],
                "Env": ["GPU=2,3"],
            },
        },
        "open-webui": {
            "Mounts": [
                {"Type": "bind", "Source": "/repo/scripts/open-webui-entrypoint.sh"}
            ],
            "Config": {
                "Image": "webui:test",
                "Cmd": ["old-helper"],
                "Env": ["KEY=value"],
            },
        },
        "rabbitmq": {"Mounts": [{"Type": "volume", "Source": "shared_rabbitmq-data"}]},
    }
    assert config.legacy_services(actual) == ["shared-vlm", "open-webui"]
    desired = {
        "services": {
            "shared-vlm": {
                "image": "vllm:test",
                "command": ["--model", "working"],
                "environment": {"GPU": "2,3"},
            },
            "open-webui": {
                "image": "webui:test",
                "command": ["new-helper"],
                "environment": {"KEY": "value"},
            },
        }
    }
    assert config.migration_services(desired, actual) == ["shared-vlm", "open-webui"]
    desired["services"]["shared-vlm"]["command"][-1] = "different-model"
    with pytest.raises(config.ConfigError):
        config.migration_services(desired, actual)


def test_rabbitmq_checks_authentication_vhosts_and_permissions(monkeypatch):
    """RabbitMQ check использует существующие credentials и ничего не создаёт."""
    service = {
        "environment": {
            "RABBITMQ_DEFAULT_USER": "shared_admin",
            "RABBITMQ_DEFAULT_PASS": "secret",
            "RABBITMQ_REQUIRED_VHOSTS": "/,pdrd",
        },
        "ports": [
            {"target": 5672, "published": 5672, "host_ip": "0.0.0.0"},
            {"target": 15672, "published": 15672, "host_ip": "0.0.0.0"},
        ],
    }
    monkeypatch.setattr(config, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        config.socket, "create_connection", lambda *args, **kwargs: nullcontext()
    )

    def response(_service, path, user, password):
        assert user == "shared_admin" and password == "secret"
        if path == "/api/vhosts":
            return b'[{"name":"/"},{"name":"pdrd"}]'
        if path.endswith("/permissions"):
            return b'[{"vhost":"/"},{"vhost":"pdrd"}]'
        return b"{}"

    monkeypatch.setattr(config, "rabbitmq_get", response)
    config.check_rabbitmq(service, {"Id": "rabbit-container"})


def test_reboot_verification_preserves_ids_volumes_and_restart_policy():
    """Restart Docker отличается от recreate и сохраняет named volumes."""
    before = {
        "n8n": {
            "id": "same",
            "started_at": "before",
            "restart": "unless-stopped",
            "volumes": [["shared_n8n-data", "/home/node/.n8n"]],
        }
    }
    after = {"n8n": {**before["n8n"], "started_at": "after"}}
    config.verify_restart(before, after)
    after["n8n"]["id"] = "recreated"
    with pytest.raises(config.ConfigError):
        config.verify_restart(before, after)


@pytest.mark.parametrize("secret", [None, "", "CHANGE_ME"])
def test_ui_secret_guard_runs_before_application(monkeypatch, secret):
    """UI без ключа не запускается; остальные профили не требуют UI-secret."""
    config.validate({"services": {}})
    monkeypatch.delenv("WEBUI_SECRET_KEY", raising=False)
    if secret is not None:
        monkeypatch.setenv("WEBUI_SECRET_KEY", secret)
    monkeypatch.setattr(os, "execvp", lambda *a: pytest.fail("UI запущен без ключа"))
    assert config.main(["guard", "open-webui"]) == 2
