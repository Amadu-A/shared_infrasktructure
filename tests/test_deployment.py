# tests/test_deployment.py
"""Регрессия конфигурации, профилей, runtime и запрета побочных действий."""

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import deployment as d

ROOT = Path(__file__).resolve().parents[1]






def test_no_gpu_for_base_stack(monkeypatch):
    """Стек без inference не обращается к NVIDIA."""
    monkeypatch.setattr(d, "run", lambda *a, **kw: pytest.fail("NVIDIA вызвана без AI"))
    d.check_gpus({"services": {}})


def test_required_service_cannot_be_skipped(config, capsys):
    """Выбранный отсутствующий VLM приводит к ошибке, а не успешному SKIP."""
    assert not d.check_services(config, {})
    assert "[FAIL] shared-vlm" in capsys.readouterr().out


@pytest.mark.parametrize("flag,value", [("--revision", "wrong"), ("--tensor-parallel-size", "1"),
                                       ("--data-parallel-size", "2"), ("--max-model-len", "4096"),
                                       ("--tool-call-parser", "wrong"), ("--served-model-name", "wrong")])
def test_runtime_compares_all_critical_flags(config, container, flag, value):
    """Неверные revision/TP/DP/context/parser/alias не принимаются за no-op."""
    service = copy.deepcopy(config["services"]["shared-vlm"])
    service["command"][service["command"].index(flag) + 1] = value
    assert not d.runtime_matches(service, container)


def test_command_order_is_not_runtime_drift(config, container):
    """Перестановка известных независимых flags не требует recreate."""
    service = copy.deepcopy(config["services"]["shared-vlm"])
    service["command"] = service["command"][2:] + service["command"][:2]
    assert d.runtime_matches(service, container)


@pytest.mark.parametrize("key,value", [("CUDA_VISIBLE_DEVICES", "0"), ("CUDA_DEVICE_ORDER", "FASTEST_FIRST"), ("VLLM_BATCH_INVARIANT", "1")])
def test_runtime_compares_gpu_and_inference_environment(config, container, key, value):
    """GPU и значимые режимы inference проверяются до no-op."""
    service = copy.deepcopy(config["services"]["shared-vlm"])
    service["environment"][key] = value
    assert not d.runtime_matches(service, container)


@pytest.mark.parametrize("flag", ["--tensor-parallel-size", "--data-parallel-size", "--max-model-len"])
def test_missing_explicit_vlm_parameters_fail(config, flag):
    """Обязательные параметры VLM не получают неподтверждённые defaults."""
    command = config["services"]["shared-vlm"]["command"]
    command[command.index(flag) + 1] = ""
    with pytest.raises(d.DeploymentError):
        d.validate_config(config)


@pytest.mark.parametrize("secret", [None, "", "  ", "CHANGE_ME", "CHANGE_ME_other"])
def test_ui_secret_only_for_selected_ui(secret):
    """Отсутствующий UI не требует ключа; выбранный UI требует настоящий secret."""
    d.validate_config({"services": {}})
    with pytest.raises(d.DeploymentError):
        d.validate_config({"services": {"open-webui": {"environment": {"WEBUI_SECRET_KEY": secret}}}})


def test_invalid_configuration_has_no_mutations(monkeypatch, config):
    """Bootstrap не создаёт даже сеть, если обязательная конфигурация некорректна."""
    config["services"]["shared-vlm"]["environment"]["CUDA_VISIBLE_DEVICES"] = "0"
    monkeypatch.setattr(d, "load_config", lambda *a: config)
    monkeypatch.setattr(d, "run", lambda *a, **kw: pytest.fail("Побочный вызов до валидации"))
    assert d.main(["bootstrap"]) == 2


def test_check_only_never_creates_network(monkeypatch):
    """Отсутствующая сеть в read-only режиме даёт ошибку без network create."""
    commands = []
    def fake_run(*args, **kwargs):
        """Разрешает только info и неуспешный inspect сети."""
        commands.append(args)
        return subprocess.CompletedProcess(args, 1 if "inspect" in args else 0, "", "")
    monkeypatch.setattr(d, "load_config", lambda *a: {"name": "shared", "services": {}, "networks": {"ai-shared": {"name": "ai-shared"}}})
    monkeypatch.setattr(d, "run", fake_run)
    assert d.main(["bootstrap", "--check-only"]) == 2
    assert not any("create" in args for args in commands)


def test_runtime_ai_is_checked_without_current_profile(monkeypatch, config, container):
    """Уже работающий VLM добавляется к проверке даже без ai-vlm в новой команде."""
    seen = []
    def load(profiles, env_file):
        """Имитирует выбранный base stack и расширение по обнаруженному runtime."""
        seen.append(profiles)
        return config if profiles else {"name": "shared", "services": {}, "networks": config["networks"]}
    monkeypatch.setattr(d, "load_config", load)
    monkeypatch.setattr(d, "container_data", lambda project: {"shared-vlm": container})
    monkeypatch.setattr(d, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "1\n2\n3\n", ""))
    monkeypatch.setattr(d, "http_check", lambda url, *a: b'{"data":[{"id":"shared-vlm"}]}' if url.endswith("/models") else b"{}")
    assert d.main(["check"]) == 0
    assert seen == [[], ["ai-vlm"]]


def test_secret_is_not_exposed_in_docker_errors(monkeypatch):
    """Закрытый stderr Compose не попадает в публичную ошибку."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 1, "", "PRIVATE_SENTINEL"))
    with pytest.raises(d.DeploymentError) as error:
        d.run("docker", "compose", "config")
    assert "PRIVATE_SENTINEL" not in str(error.value)


@pytest.mark.parametrize("policy,valid", [({"Type": "json-file", "Config": {}}, False),
    ({"Type": "local", "Config": {"max-size": "10m", "max-file": "0"}}, False),
    ({"Type": "local", "Config": {"max-size": "10m", "max-file": "5"}}, True)])
def test_bounded_logging_requires_finite_limits(policy, valid):
    """Наличие driver само по себе не считается доказательством конечной политики."""
    assert d.bounded_logging({"HostConfig": {"LogConfig": policy}}) is valid


@pytest.fixture
def render(tmp_path):
    """Вызывает только Compose config с временным env, без пользовательского .env."""
    def invoke(values=None, process=None, profiles=()):
        """Возвращает JSON после штатной интерполяции Compose, не публикуя secrets."""
        if not shutil.which("docker"):
            pytest.fail("Для compose-набора требуется Docker Compose CLI")
        catalog = (ROOT / ".env.example").read_text(encoding="utf-8")
        names = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", catalog, re.M))
        env = {k: v for k, v in os.environ.items() if k not in names and not k.startswith("COMPOSE_")}
        base = {"N8N_DB_PASSWORD": "test-db-secret", "N8N_ENCRYPTION_KEY": "test-encryption-secret", "RABBITMQ_DEFAULT_PASS": "test-broker-secret"}
        base.update(values or {})
        env_file = tmp_path / "fixture.env"
        env_file.write_text("\n".join(f"{k}={v}" for k, v in base.items()), encoding="utf-8")
        env.update(process or {})
        args = ["docker", "compose", "--project-directory", str(tmp_path), "--env-file", str(env_file), "-f", str(ROOT / "compose.yaml")]
        for profile in profiles:
            args += ["--profile", profile]
        result = subprocess.run([*args, "config", "--format", "json"], env=env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise d.DeploymentError("Изолированный Compose отверг конфигурацию")
        return json.loads(result.stdout)
    return invoke


@pytest.mark.compose
def test_real_compose_precedence_defaults_and_profiles(render):
    """Проверяет полный приоритет команды/.env/default, пустой override и profiles."""
    values = {"SHARED_VLM_HOST_PORT": "8111", "SHARED_VLM_TP_SIZE": "2", "SHARED_VLM_DP_SIZE": "1", "SHARED_VLM_MAX_MODEL_LEN": "8192", "COMPOSE_PROFILES": "ai-vlm"}
    config = render(values, {"SHARED_VLM_HOST_PORT": "8222"})
    d.validate_config(config)
    vlm = config["services"]["shared-vlm"]
    assert d.endpoint(vlm, 8000, "/health") == "http://127.0.0.1:8222/health"
    assert d.value_after(vlm["command"], "--model") == "Qwen/Qwen3.8-27B"
    assert "--spec-method" in vlm["command"] and "--enable-auto-tool-choice" in vlm["command"]
    assert d.endpoint(render(values)["services"]["shared-vlm"], 8000, "/health").endswith(":8111/health")
    config = render(values, {"SHARED_VLM_HOST_PORT": ""})
    assert config["services"]["shared-vlm"]["ports"][0]["published"] == "8000"


@pytest.mark.compose
def test_real_compose_missing_required_and_ui_scope(render):
    """Пустой base-secret отвергается Compose, UI-secret не нужен отключённому UI."""
    with pytest.raises(d.DeploymentError):
        render(process={"N8N_DB_PASSWORD": ""})
    config = render()
    d.validate_config(config)
    assert "shared-vlm" not in config["services"]
    values = {"SHARED_VLM_TP_SIZE": "2", "SHARED_VLM_DP_SIZE": "1", "SHARED_VLM_MAX_MODEL_LEN": "8192"}
    config = render(values, profiles=("ai-vlm", "ai-ui"))
    assert config["services"]["open-webui"]["environment"]["WEBUI_SECRET_KEY"] == ""
    with pytest.raises(d.DeploymentError):
        d.validate_config(config)


def test_manifests_keep_network_private_db_and_finite_logs():
    """Сети, private DB и согласованная log policy остаются явно проверяемыми."""
    raw = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    assert raw["networks"]["ai-shared"] == {"external": True, "name": "${SHARED_NETWORK_NAME:-ai-shared}"}
    db = raw["services"]["n8n-db"]
    assert "ports" not in db and db["networks"] == ["n8n-internal"]
    for service in raw["services"].values():
        assert service["logging"]["driver"] == "local"
        assert service["logging"]["options"] == {"max-size": "${SHARED_LOG_MAX_SIZE:-10m}", "max-file": "${SHARED_LOG_MAX_FILE:-5}"}
    registry = yaml.safe_load((ROOT / "docs/services.yaml").read_text(encoding="utf-8"))
    assert registry["services"]["n8n-db"]["exposed_to_application_projects"] is False
    for name in d.GPU_PLACEMENT:
        assert registry["services"][name]["authentication"]["type"] == "none"


def test_catalog_covers_actual_compose_inputs():
    """Не считает container-escape переменные входами deployment или legacy имена мусором."""
    source = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    inputs = set(re.findall(r"(?<!\$)\$\{([A-Z][A-Z0-9_]*)", source))
    catalog = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M))
    assert inputs <= catalog
    assert 'ENABLE_OLLAMA_API: "false"' in source


@pytest.mark.compose
@pytest.mark.parametrize("profiles", [(), ("ai-vlm",), ("ai-embedding",), ("ai-vlm", "ai-embedding"),
    ("ai-vlm", "ai-ui"), ("ai-vlm", "ai-embedding", "ai-ui")])
def test_real_compose_profile_matrix(render, profiles):
    """Все допустимые комбинации профилей используют одни и те же defaults/guards."""
    config = render({"SHARED_VLM_TP_SIZE": "2", "SHARED_VLM_DP_SIZE": "1", "SHARED_VLM_MAX_MODEL_LEN": "8192", "OPEN_WEBUI_SECRET_KEY": "valid-test-key"}, profiles=profiles)
    d.validate_config(config)
    for name, profile in d.AI_PROFILES.items():
        assert (name in config["services"]) == (profile in profiles)
    if "shared-embedding" in config["services"]:
        assert config["services"]["shared-embedding"]["environment"]["CUDA_VISIBLE_DEVICES"] == "1"
        assert config["services"]["shared-embedding"]["entrypoint"][-1] == "shared-embedding"


@pytest.mark.compose
def test_process_profile_and_empty_mandatory_override(render):
    """Команда может отключить file-profile; пустое обязательное значение не скрывается."""
    values = {"COMPOSE_PROFILES": "ai-vlm", "SHARED_VLM_TP_SIZE": "2", "SHARED_VLM_DP_SIZE": "1", "SHARED_VLM_MAX_MODEL_LEN": "8192"}
    assert "shared-vlm" not in render(values, {"COMPOSE_PROFILES": ""})["services"]
    with pytest.raises(d.DeploymentError):
        d.validate_config(render(values, {"SHARED_VLM_TP_SIZE": ""}))
