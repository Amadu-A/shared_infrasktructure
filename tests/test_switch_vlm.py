# tests/test_switch_vlm.py
"""Проверяет транзакцию выбора, миграцию, отмену и rollback без Docker runtime."""

import copy
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

import deployment as d


@pytest.fixture
def switch(tmp_path, monkeypatch):
    """Изолирует все записи переключателя и исключает реальный Docker."""
    path = Path(__file__).resolve().parents[1] / "scripts/switch-vlm.py"
    spec = importlib.util.spec_from_file_location("switch_vlm", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("OTHER='keep me'\n", encoding="utf-8")
    for key in (*module.MANAGED.values(), "SHARED_VLM_MODEL_ARGS", "SHARED_VLM_GPU_DEVICES", "SHARED_VLM_BATCH_INVARIANT", "COMPOSE_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(d, "run", lambda *a, **kw: pytest.fail("Незапланированный Docker-вызов"))
    return module


def test_primary_keeps_exact_revision_and_explicit_parameters(switch, config):
    """Основная модель сохраняет весь профиль, включая exact SHA и context из deployment."""
    baseline = config["services"]["shared-vlm"]
    assert switch.selected_service("1", baseline) == baseline
    selected = switch.selected_service("2", baseline)
    for flag in ("--tensor-parallel-size", "--data-parallel-size", "--max-model-len", "--max-num-queued-reqs"):
        assert d.value_after(selected["command"], flag) == d.value_after(baseline["command"], flag)


def test_unverified_parser_is_blocked(switch, config):
    """Непроверенная модель не становится доступной даже при существующем cache."""
    with pytest.raises(d.DeploymentError, match="REQUIRES_OWNER_DECISION"):
        switch.selected_service("3", config["services"]["shared-vlm"])


def test_cancel_and_dry_run_do_not_write(switch, config, container, monkeypatch):
    """Отмена и dry-run оставляют весь временный deployment побайтно неизменным."""
    monkeypatch.setattr(d, "load_config", lambda *a: config)
    monkeypatch.setattr(d, "container_data", lambda *a: {"shared-vlm": container})
    before = {p.name: p.read_bytes() for p in switch.ROOT.iterdir()}
    assert switch.main(["--dry-run"]) == 0
    monkeypatch.setattr("builtins.input", lambda prompt: "0")
    assert switch.main([]) == 0
    assert {p.name: p.read_bytes() for p in switch.ROOT.iterdir()} == before


def test_experimental_requires_separate_owner_approval(switch, config, container, monkeypatch):
    """Наличие test-профиля в меню не считается согласием владельца."""
    monkeypatch.setattr(d, "load_config", lambda *a: config)
    monkeypatch.setattr(d, "container_data", lambda *a: {"shared-vlm": container})
    monkeypatch.setattr("builtins.input", lambda prompt: "2")
    with pytest.raises(d.DeploymentError, match="REQUIRES_OWNER_DECISION"):
        switch.main([])


def test_foreign_override_and_compose_file_are_never_changed(switch, monkeypatch):
    """Чужой override и другой набор Compose-файлов блокируют запись."""
    override = switch.ROOT / "compose.override.yml"
    override.write_text("foreign\n", encoding="utf-8")
    with pytest.raises(d.DeploymentError):
        switch.assert_safe(True)
    assert override.read_text() == "foreign\n"
    override.unlink()
    monkeypatch.setenv("COMPOSE_FILE", "other.yaml")
    with pytest.raises(d.DeploymentError):
        switch.assert_safe()


def test_env_writer_preserves_unknown_keys_comments_crlf_and_bom(switch):
    """Запись выбора не удаляет неизвестные переменные, comments или формат строк."""
    original = b"\xef\xbb\xbf# comment\r\nOTHER='keep # $ value'\r\nSHARED_VLM_MODEL_ID=old\r\nSHARED_VLM_MODEL_ID=duplicate\r\n"
    result = switch.render_env(original, {"SHARED_VLM_MODEL_ID": "new"})
    assert result.startswith(b"\xef\xbb\xbf# comment\r\nOTHER='keep # $ value'\r\n")
    assert result.count(b"SHARED_VLM_MODEL_ID=") == 1
    assert b"SHARED_VLM_MODEL_ID='new'\r\n" in result
    with pytest.raises(d.DeploymentError):
        switch.render_env(original, {"SHARED_VLM_MODEL_ID": "bad\nvalue"})


def test_env_writer_preserves_assignment_inside_multiline_secret(switch):
    """Похожая на VLM-настройку строка внутри чужого секрета остаётся неизменной."""
    original = b"PRIVATE='first\nSHARED_VLM_MODEL_ID=not-an-assignment\nlast'\nSHARED_VLM_MODEL_ID=old\n"
    result = switch.render_env(original, {"SHARED_VLM_MODEL_ID": "new"})
    assert result.startswith(b"PRIVATE='first\nSHARED_VLM_MODEL_ID=not-an-assignment\nlast'\n")
    assert result.endswith(b"SHARED_VLM_MODEL_ID='new'\n")


@pytest.mark.parametrize("failure", ["config", "up", "health", "smoke", "none"])
def test_selection_transaction_and_runtime_rollback(switch, config, monkeypatch, failure):
    """Ошибка на каждом шаге возвращает .env; начатый recreate возвращает и runtime."""
    path = switch.ROOT / ".env"
    original = path.read_bytes()
    baseline = config["services"]["shared-vlm"]
    expected = switch.selected_service("2", baseline)
    values = switch.settings(expected)
    events = []
    def load(*args):
        """Считывает текущую фазу env-транзакции для настоящей проверки rollback."""
        selected = values["SHARED_VLM_MODEL_ID"] in path.read_text()
        if selected and failure == "config":
            raise d.DeploymentError("configuration failure")
        result = copy.deepcopy(config)
        result["services"]["shared-vlm"] = expected if selected else baseline
        return result
    def run(*args, **kwargs):
        """Фиксирует целевые recreate и имитирует ошибку запуска либо API-smoke."""
        events.append(args)
        selected = values["SHARED_VLM_MODEL_ID"] in path.read_text()
        if selected and ((failure == "up" and "up" in args) or (failure == "smoke" and any("check-vlm-tool-calling.py" in a for a in args))):
            raise d.DeploymentError("runtime failure")
        return subprocess.CompletedProcess(args, 0, "", "")
    def ready(service, timeout):
        """Подтверждает возврат прежнего профиля после failed health нового."""
        if service == expected and failure == "health":
            raise d.DeploymentError("health failure")
    monkeypatch.setattr(d, "load_config", load)
    monkeypatch.setattr(d, "run", run)
    monkeypatch.setattr(d, "check_gpus", lambda *a: None)
    monkeypatch.setattr(switch, "wait_ready", ready)
    if failure == "none":
        switch.apply_selection(values, expected, 1)
        assert values["SHARED_VLM_MODEL_ID"] in path.read_text()
    else:
        with pytest.raises(d.DeploymentError):
            switch.apply_selection(values, expected, 1)
        assert path.read_bytes() == original
    up = [args for args in events if "up" in args]
    assert len(up) == (0 if failure == "config" else 1 if failure == "none" else 2)
    assert all(args[-1] == "shared-vlm" and "--no-deps" in args for args in up)
    assert not (switch.ROOT / "compose.override.yaml").exists()
    assert not (switch.ROOT / ".env.bak-switch.lock").exists()
    backup = next(p for p in switch.ROOT.glob(".env.bak-*") if "write" not in p.name)
    assert backup.read_bytes() == original
    if os.name != "nt":
        assert backup.stat().st_mode & 0o077 == 0


def test_environment_conflict_rejected_before_write(switch, config, monkeypatch):
    """Командное значение не позволяет сохранить незаметно неработающий выбор в .env."""
    monkeypatch.setenv("SHARED_VLM_MODEL_ID", "command-wins")
    before = (switch.ROOT / ".env").read_bytes()
    service = config["services"]["shared-vlm"]
    with pytest.raises(d.DeploymentError):
        switch.apply_selection(switch.settings(service), service, 1)
    assert (switch.ROOT / ".env").read_bytes() == before
    assert len(list(switch.ROOT.iterdir())) == 1


@pytest.mark.parametrize("drift", [False, True])
def test_owned_migration_proves_equivalence_without_recreate(switch, config, container, monkeypatch, drift):
    """Миграция не вызывает up; при изменении любого другого сервиса всё возвращает."""
    legacy = switch.ROOT / "compose.override.yaml"
    legacy.write_text(switch.MARKER, encoding="utf-8")
    original = (switch.ROOT / ".env").read_bytes()
    def load(*args):
        """Разрешает контролируемое отличие после архивирования legacy-файла."""
        result = copy.deepcopy(config)
        if drift and not legacy.exists():
            result["name"] = "unexpected-project"
        return result
    monkeypatch.setattr(d, "load_config", load)
    monkeypatch.setattr(d, "container_data", lambda *a: {"shared-vlm": container})
    monkeypatch.setattr("builtins.input", lambda prompt: "YES")
    if drift:
        with pytest.raises(d.DeploymentError):
            switch.migrate_current(legacy, False)
        assert legacy.exists() and (switch.ROOT / ".env").read_bytes() == original
    else:
        switch.migrate_current(legacy, False)
        assert not legacy.exists()
        assert "SHARED_VLM_MODEL_ARGS" in (switch.ROOT / ".env").read_text()


def test_parallel_switch_does_not_remove_existing_lock(switch, config):
    """Параллельный запуск не меняет ни .env, ни lock работающего оператора."""
    lock = switch.ROOT / ".env.bak-switch.lock"
    lock.write_text("owned-by-other-operation", encoding="utf-8")
    before = (switch.ROOT / ".env").read_bytes()
    service = config["services"]["shared-vlm"]
    with pytest.raises(d.DeploymentError):
        switch.apply_selection(switch.settings(service), service, 1)
    assert lock.read_text() == "owned-by-other-operation"
    assert (switch.ROOT / ".env").read_bytes() == before


def test_wait_ready_has_finite_timeout(switch, config, monkeypatch):
    """Неhealthy-сервис не оставляет ожидание бесконечным."""
    clock = iter([0, 2])
    monkeypatch.setattr(switch.time, "monotonic", lambda: next(clock))
    with pytest.raises(d.DeploymentError, match="время ожидания"):
        switch.wait_ready(config["services"]["shared-vlm"], 1)


def test_failed_atomic_write_never_starts_container(switch, config, monkeypatch):
    """Сбой записи возвращает исходный dotenv и не доходит до recreate."""
    path = switch.ROOT / ".env"
    original = path.read_bytes()
    atomic = switch.atomic_write
    attempts = []
    def fail_first(target, content, metadata):
        """Имитирует ошибку файловой системы до первой успешной замены."""
        attempts.append(content)
        if len(attempts) == 1:
            raise OSError("simulated write failure")
        atomic(target, content, metadata)
    monkeypatch.setattr(switch, "atomic_write", fail_first)
    service = switch.selected_service("2", config["services"]["shared-vlm"])
    with pytest.raises(OSError):
        switch.apply_selection(switch.settings(service), service, 1)
    assert path.read_bytes() == original
    assert not (switch.ROOT / ".env.bak-switch.lock").exists()
