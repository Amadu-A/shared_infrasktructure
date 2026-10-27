# tests/test_contracts_and_docs.py
"""Проверяет исполняемый пример, Docker guard, bundle и concurrent API probe."""

import ast
import asyncio
import importlib.util
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    """Загружает CLI-скрипт без выполнения его main."""
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode", ["sync-success", "sync-error", "async-success", "async-error", "async-cancel"])
def test_documented_timing_preserves_outcome(mode, caplog):
    """Исполняет актуальный пример из документа, включая распространение отмены."""
    source = (ROOT / "docs/ENGINEERING_GUIDELINES.md").read_text(encoding="utf-8")
    blocks = [part.split("\n```", 1)[0] for part in source.split("```python\n")[1:]]
    example = next(block for block in blocks if "def log_execution_time(" in block)
    scope = {"__name__": "documented_timing"}
    exec(compile(example, "documented_timing", "exec"), scope)
    def operation():
        """Имитирует успех либо обычное исключение тестовой операции."""
        if mode.endswith("error"):
            raise ValueError("expected")
        return 42
    async def async_operation():
        """Имитирует async outcome без реального I/O и сна."""
        if mode.endswith("cancel"):
            raise asyncio.CancelledError()
        return operation()
    original = async_operation if mode.startswith("async") else operation
    wrapped = scope["log_execution_time"](operation="test-operation")(original)
    assert wrapped.__name__ == original.__name__
    caplog.set_level(logging.INFO)
    if mode.endswith("success"):
        assert (asyncio.run(wrapped()) if mode.startswith("async") else wrapped()) == 42
    else:
        with pytest.raises(asyncio.CancelledError if mode.endswith("cancel") else ValueError):
            if mode.startswith("async"):
                asyncio.run(wrapped())
            else:
                wrapped()
    records = [r for r in caplog.records if getattr(r, "event", "") == "operation_timing"]
    assert len(records) == 1
    assert records[0].status == ("success" if mode.endswith("success") else "error")
    assert records[0].operation == "test-operation" and records[0].duration_ms >= 0


def test_inference_guard_refuses_before_exec(config, monkeypatch):
    """Docker guard отказывает до vllm serve при отсутствующем обязательном TP."""
    guard = load_script("vlm-entrypoint.py")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")
    calls = []
    monkeypatch.setattr(os, "execvp", lambda *a: calls.append(a))
    command = list(config["services"]["shared-vlm"]["command"])
    assert guard.main(command) == 0
    assert calls[0][0] == "vllm" and calls[0][1][:2] == ["vllm", "serve"]
    calls.clear()
    command[command.index("--tensor-parallel-size") + 1] = ""
    assert guard.main(command) == 2
    assert not calls


@pytest.mark.skipif(sys.platform != "linux", reason="Bash guard выполняется в Linux CI; на Windows проверяется Python core")
@pytest.mark.parametrize("secret", [None, "", " ", "CHANGE_ME", "valid-test-secret"])
def test_ui_guard_before_original_start(secret):
    """Настоящий Bash guard блокирует ключ до старта и не раскрывает его."""
    env = {k: v for k, v in os.environ.items() if k != "WEBUI_SECRET_KEY"}
    if secret is not None:
        env["WEBUI_SECRET_KEY"] = secret
    # Перехват exec доказывает handoff без реального запуска приложения.
    script = 'exec() { printf "HANDOFF:%s" "$*"; }; source "$1"'
    result = subprocess.run(["bash", "--noprofile", "--norc", "-c", script, "guard-test", str(ROOT / "scripts/open-webui-entrypoint.sh")], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == (0 if secret == "valid-test-secret" else 2)
    assert ("HANDOFF:bash /app/backend/start.sh" in result.stdout) == (secret == "valid-test-secret")
    if secret and secret.strip():
        assert secret not in result.stdout + result.stderr


def test_probe_launches_four_overlapping_requests(monkeypatch):
    """Barrier обнаружит сериализацию трёх VLM и embedding запросов."""
    probe = load_script("check-shared-contracts.py")
    barrier = threading.Barrier(4)
    def post(url, payload):
        """Возвращает synthetic-ответ лишь после старта всех четырёх клиентов."""
        barrier.wait(timeout=5)
        return {"data": [{"embedding": [0.1, 0.2, 0.3]}]} if "input" in payload else {"choices": [{"message": {"content": "ОК"}}]}
    monkeypatch.setattr(probe, "post", post)
    assert probe.main(["--vlm-url", "http://vlm/v1", "--embedding-url", "http://embedding/v1", "--embedding-dimension", "3"]) == 0


@pytest.mark.parametrize("vector", [[], [float("nan")], [float("inf")], ["string"], [True]])
def test_embedding_validator_rejects_invalid_vectors(vector):
    """Неконечные, пустые и неверно типизированные embeddings не проходят smoke."""
    probe = load_script("check-shared-contracts.py")
    with pytest.raises(ValueError):
        probe.vector_dimension({"data": [{"embedding": vector}]})


def test_bundle_relative_links_and_single_checklist():
    """Проверяет существование локальных целей ссылок и единственный infra DoD."""
    sources = [ROOT / name for name in ("README.md", "PROJECT_INTEGRATION.md", "SERVICES.md")]
    sources += list((ROOT / "docs").glob("*.md"))
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if "://" in target or target.startswith("#"):
                continue
            assert (source.parent / target.split("#")[0]).is_file(), (source.name, target)
    infra = (ROOT / "docs/INFRASTRUCTURE_INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^## .*Definition of Done", infra, re.M)) == 1
    engineering = (ROOT / "docs/ENGINEERING_GUIDELINES.md").read_text(encoding="utf-8")
    checklist = engineering.split("## 60. Frontend", 1)[1].split("\n---", 1)[0]
    assert "FRONTEND_GUIDELINES.md" in checklist and "- [ ]" not in checklist


def test_python_functions_have_russian_documentation():
    """Предотвращает повторное появление undocumented Python-функций проекта."""
    for path in [*(ROOT / "scripts").glob("*.py"), *(ROOT / "tests").glob("*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                doc = ast.get_docstring(node)
                assert doc and re.search("[А-Яа-яЁё]", doc), (path.name, node.name)
