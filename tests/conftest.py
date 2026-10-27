# tests/conftest.py
"""Добавляет CLI-модули проекта в import path без установки production-пакета."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

@pytest.fixture
def config():
    """Создаёт изолированный intended VLM с явным тестовым размером контекста."""
    command = ["--model", "Qwen/Qwen3.8-27B", "--revision", "a" * 40,
               "--served-model-name", "shared-vlm", "--tensor-parallel-size", "2",
               "--data-parallel-size", "1", "--max-model-len", "8192",
               "--gpu-memory-utilization", "0.90", "--max-num-seqs", "8",
               "--max-num-queued-reqs", "32", "--max-num-queued-tokens", "32768",
               "--host", "0.0.0.0", "--port", "8000", "--reasoning-parser", "qwen3",
               "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder",
               "--spec-method", "mtp", "--spec-tokens", "3"]
    return {"name": "shared", "networks": {"ai-shared": {"name": "ai-shared"}}, "services": {
        "shared-vlm": {"image": "vllm/test:fixed", "command": command,
                       "environment": {"CUDA_VISIBLE_DEVICES": "2,3", "CUDA_DEVICE_ORDER": "PCI_BUS_ID", "VLLM_BATCH_INVARIANT": "0"},
                       "ports": [{"target": 8000, "published": "8111", "host_ip": "0.0.0.0"}]}}}


@pytest.fixture
def container(config):
    """Описывает healthy-контейнер только с данными из тестовой конфигурации."""
    service = config["services"]["shared-vlm"]
    return {"Id": "test-container", "Config": {"Image": service["image"], "Cmd": service["command"],
            "Env": [f"{k}={v}" for k, v in service["environment"].items()]},
            "State": {"Running": True, "Health": {"Status": "healthy"}},
            "HostConfig": {"LogConfig": {"Type": "local", "Config": {"max-file": "5", "max-size": "10m"}}}}
