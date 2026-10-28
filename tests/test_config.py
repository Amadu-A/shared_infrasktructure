# tests/test_config.py
"""Небольшая регрессия настроек Compose и отказа до побочных действий."""
import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("config_check", ROOT / "scripts/config-check.py")
config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(config)


@pytest.fixture
def render(tmp_path):
    """Разрешает настройки самим Compose с временным env, без серверного .env."""
    def invoke(values=None, process=None, profiles=()):
        """Возвращает config JSON; контейнеры и Docker daemon не используются."""
        if not shutil.which("docker"):
            pytest.skip("Для интерполяции нужен Docker Compose CLI")
        names = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M))
        env = {k: v for k, v in os.environ.items() if k not in names and not k.startswith("COMPOSE_")}
        env.update(process or {})
        settings = {"N8N_DB_PASSWORD": "test-db", "N8N_ENCRYPTION_KEY": "test-key", "RABBITMQ_DEFAULT_PASS": "test-broker"}
        settings.update(values or {})
        fixture = tmp_path / "fixture.env"
        fixture.write_text("\n".join(f"{k}={v}" for k, v in settings.items()), encoding="utf-8")
        args = ["docker", "compose", "--project-directory", str(tmp_path), "--env-file", str(fixture), "-f", str(ROOT / "compose.yaml")]
        for profile in profiles:
            args += ["--profile", profile]
        result = subprocess.run([*args, "config", "--format", "json"], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, "Compose отверг тестовую конфигурацию"
        return json.loads(result.stdout)
    return invoke


@pytest.mark.parametrize("profiles", [(), ("ai-vlm",), ("ai-embedding",), ("ai-vlm", "ai-embedding", "ai-ui")])
def test_profiles_and_command_precedence(render, profiles):
    """Команда выше env-файла; defaults и опциональные сервисы разрешает Compose."""
    values = {"SHARED_VLM_TP_SIZE": "2", "SHARED_VLM_DP_SIZE": "1", "SHARED_VLM_MAX_MODEL_LEN": "8192", "OPEN_WEBUI_SECRET_KEY": "test-ui", "SHARED_VLM_HOST_PORT": "8111"}
    resolved = render(values, {"SHARED_VLM_HOST_PORT": "8222"}, profiles)
    config.validate(resolved)
    for name, profile in config.AI_PROFILES.items():
        assert (name in resolved["services"]) == (profile in profiles)
    if "ai-vlm" in profiles:
        service = resolved["services"]["shared-vlm"]
        assert config.url(service, 8000, "/health") == "http://127.0.0.1:8222/health"
        assert config.argument(service["command"], "--model") == "Qwen/Qwen3.8-27B"
        assert render(values, profiles=profiles)["services"]["shared-vlm"]["ports"][0]["published"] == "8111"
        del values["SHARED_VLM_HOST_PORT"]
        assert render(values, profiles=profiles)["services"]["shared-vlm"]["ports"][0]["published"] == "8000"


def test_missing_required_setting_stops_before_changes(render, monkeypatch):
    """Отсутствующий TP не получает выдуманный default и блокирует prepare до Docker info."""
    resolved = render(profiles=("ai-vlm",))
    monkeypatch.setattr(config, "load_config", lambda *a: resolved)
    monkeypatch.setattr(config, "run", lambda *a, **kw: pytest.fail("Побочный вызов после неверных настроек"))
    assert config.main(["prepare"]) == 2


def test_no_nvidia_without_inference(monkeypatch):
    """Base stack не обращается к NVIDIA."""
    monkeypatch.setattr(config, "run", lambda *a, **kw: pytest.fail("Лишняя проверка NVIDIA"))
    config.check_gpus({"services": {}})


def test_missing_expected_container_is_failure():
    """Отсутствующий включённый VLM не считается успешным SKIP."""
    assert not config.check_services({"services": {"shared-vlm": {}}}, {})


@pytest.mark.parametrize("secret", [None, "", "CHANGE_ME"])
def test_ui_secret_guard_runs_before_application(monkeypatch, secret):
    """UI без ключа не запускается; остальные профили не требуют UI-secret."""
    config.validate({"services": {}})
    monkeypatch.delenv("WEBUI_SECRET_KEY", raising=False)
    if secret is not None:
        monkeypatch.setenv("WEBUI_SECRET_KEY", secret)
    monkeypatch.setattr(os, "execvp", lambda *a: pytest.fail("UI запущен без ключа"))
    assert config.main(["guard", "open-webui"]) == 2
