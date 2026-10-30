# tests/test_vlm_contract.py
"""Контракты VLM, постоянные данные и отсутствие потерянных ссылок Compose."""

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_vlm_tool_response_contract():
    """Structured tool_calls принимаются, сырой XML вместо ответа отклоняется."""
    spec = importlib.util.spec_from_file_location(
        "vlm_smoke", ROOT / "scripts/check-vlm-tool-calling.py"
    )
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    call = {
        "id": "test-call",
        "type": "function",
        "function": {"name": "check_status", "arguments": '{"check":"ready"}'},
    }
    reply = {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}
    assert smoke.parsed_tool_call(reply) == call
    with pytest.raises(AssertionError):
        smoke.assert_text(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "<tool_call>raw</tool_call>",
                        }
                    }
                ]
            }
        )


def test_restart_cache_network_and_existing_accounts():
    """Сохраняются restart policy, HF cache, сети и штатная первичная инициализация."""
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    for service in compose["services"].values():
        assert service["restart"] == "unless-stopped"
        for volume in service.get("volumes", []):
            if volume.startswith("./"):
                assert (ROOT / volume.split(":")[0]).is_file()
    for name in ("shared-vlm", "shared-embedding"):
        assert (
            "huggingface-cache:/root/.cache/huggingface"
            in compose["services"][name]["volumes"]
        )
        assert compose["services"][name]["entrypoint"][-2:] == ["guard", name]
    assert (
        compose["networks"]["ai-shared"]["name"] == "${SHARED_NETWORK_NAME:-ai-shared}"
    )
    assert compose["services"]["n8n-db"]["networks"] == ["n8n-internal"]
    assert "ports" not in compose["services"]["n8n-db"]
    assert (
        compose["services"]["rabbitmq"]["environment"]["RABBITMQ_DEFAULT_USER"]
        == "${RABBITMQ_DEFAULT_USER:-shared_admin}"
    )
    assert (
        compose["services"]["rabbitmq"]["environment"]["RABBITMQ_REQUIRED_VHOSTS"]
        == "${RABBITMQ_REQUIRED_VHOSTS:-/}"
    )
    assert (
        compose["services"]["n8n-db"]["environment"]["POSTGRES_USER"]
        == "${N8N_DB_USER:-n8n}"
    )
    assert compose["services"]["searxng"]["profiles"] == ["ai-search"]
    assert (ROOT / "searxng/settings.yml").is_file()

    docling = yaml.safe_load(
        (ROOT / "docling-eval/compose.yaml").read_text(encoding="utf-8")
    )
    service = docling["services"]["docling"]
    assert docling["name"] == "docling-eval"
    assert service["restart"] == "unless-stopped"
    assert service["ports"] == ["127.0.0.1:${DOCLING_HOST_PORT:-5001}:5001"]
    assert service["cap_drop"] == ["ALL"]
    assert service["volumes"][0]["read_only"] is True
    assert service["volumes"][0]["bind"]["create_host_path"] is False
    assert docling["networks"]["ai-shared"]["name"] == (
        "${SHARED_NETWORK_NAME:-ai-shared}"
    )
    registry = yaml.safe_load((ROOT / "docs/services.yaml").read_text(encoding="utf-8"))
    assert set(registry["services"]) == set(compose["services"]) | {"docling"}
    assert registry["services"]["docling"]["compose_project_name"] == "docling-eval"

    bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
    assert "--no-recreate" in bootstrap
    assert "systemctl enable --now" not in bootstrap
    assert "docling-start" in bootstrap
