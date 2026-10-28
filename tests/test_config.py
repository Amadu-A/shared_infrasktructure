# tests/test_config.py
"""Небольшая регрессия настроек Compose и отказа до побочных действий."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
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


@pytest.mark.parametrize(
    "profiles",
    [(), ("ai-vlm",), ("ai-embedding",), ("ai-vlm", "ai-embedding", "ai-ui")],
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
