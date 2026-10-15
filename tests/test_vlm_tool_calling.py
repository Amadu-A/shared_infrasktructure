
# tests/test_vlm_tool_calling.py
"""Offline contract tests for shared VLM configuration and model switching."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SWITCH = ROOT / "scripts" / "switch-vlm.py"
COMPOSE = ROOT / "compose.yaml"


def load_switch():
    spec = importlib.util.spec_from_file_location("switch_vlm", SWITCH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_base_compose_keeps_previous_features_and_enables_tools():
    content = COMPOSE.read_text(encoding="utf-8")
    vlm = content.split("  shared-vlm:", 1)[1].split("  shared-embedding:", 1)[0]
    embedding = content.split("  shared-embedding:", 1)[1].split("  open-webui:", 1)[0]
    assert "      - --enable-auto-tool-choice" in vlm
    assert "      - --tool-call-parser\n      - qwen3_coder" in vlm
    assert "      - --reasoning-parser\n      - qwen3" in vlm
    assert "      - --spec-method\n      - mtp" in vlm
    assert '      - "3"' in vlm
    assert "      - --max-model-len" in vlm
    assert "--enable-auto-tool-choice" not in embedding
    assert "--tool-call-parser" not in embedding


@pytest.mark.parametrize("key", ["1", "2"])
def test_supported_models_have_compatible_parsers(key):
    module = load_switch()
    assert module.MODELS[key]["tool_parser"] == "qwen3_coder"
    assert module.MAX_MODEL_LEN == "262144"


def test_override_preserves_tool_calling_and_gpu():
    module = load_switch()
    result = module.make_override(module.MODELS["2"])
    assert "--enable-auto-tool-choice" in result
    assert '      - "--tool-call-parser"\n      - "qwen3_coder"' in result
    assert '      CUDA_VISIBLE_DEVICES: "2,3"' in result
    assert "--reasoning-parser" in result
    assert "--quantization" in result
    assert "moe_wna16" in result
    assert "262144" in result
    assert "--spec-method" not in result
    assert "--spec-tokens" not in result


def test_unsupported_235b_selection_rejected(tmp_path, monkeypatch):
    module = load_switch()
    monkeypatch.setattr(module, "OVERRIDE", tmp_path / "compose.override.yaml")
    with pytest.raises(ValueError, match="Unsupported Tool Calling"):
        module.save_selection("3")
    with pytest.raises(ValueError, match="not been validated"):
        module.make_override(module.MODELS["3"])
    assert not module.OVERRIDE.exists()


def test_return_to_original_model_removes_only_owned_override(tmp_path, monkeypatch):
    module = load_switch()
    override = tmp_path / "compose.override.yaml"
    monkeypatch.setattr(module, "OVERRIDE", override)
    module.save_selection("2")
    assert module.MARKER in override.read_text(encoding="utf-8")
    module.save_selection("1")
    assert not override.exists()


def test_tool_calling_enabled_requires_both_flags():
    module = load_switch()
    assert module.tool_calling_enabled(
        ["--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder"],
        "qwen3_coder",
    )
    assert not module.tool_calling_enabled(
        ["--tool-call-parser", "qwen3_coder"], "qwen3_coder"
    )
    assert not module.tool_calling_enabled(
        ["--enable-auto-tool-choice", "--tool-call-parser", "hermes"],
        "qwen3_coder",
    )


def test_switch_checks_effective_baseline_config(tmp_path, monkeypatch):
    module = load_switch()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "OVERRIDE", tmp_path / "compose.override.yaml")
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    command = [
        "--model", "Qwen/Qwen3.8-27B",
        "--tensor-parallel-size", "2",
        "--max-model-len", "262144",
        "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder",
    ]
    configuration = {
        "services": {
            "shared-vlm": {
                "command": command,
                "environment": {"CUDA_VISIBLE_DEVICES": "2,3"},
            }
        }
    }

    def fake_run(*args, **_kwargs):
        text = json.dumps(configuration) if "--format" in args else ""
        return subprocess.CompletedProcess(args, 0, stdout=text, stderr="")

    monkeypatch.setattr(module, "run", fake_run)
    module.assert_safe()
    configuration["services"]["shared-vlm"]["command"] = [
        part for part in command if part != "--enable-auto-tool-choice"
    ]
    with pytest.raises(RuntimeError, match="Tool Calling"):
        module.assert_safe()


def test_api_validator_accepts_structured_function_call():
    spec = importlib.util.spec_from_file_location(
        "tool_call_smoke", ROOT / "scripts" / "check-vlm-tool-calling.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    call = {
        "id": "call_example",
        "type": "function",
        "function": {"name": "check_status", "arguments": '{"check":"ready"}'},
    }
    reply = {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}
    assert module.parsed_tool_call(reply) == call
    call["function"]["arguments"] = "{invalid-json"
    with pytest.raises(json.JSONDecodeError):
        module.parsed_tool_call(reply)


def test_api_validator_rejects_raw_xml_in_content():
    spec = importlib.util.spec_from_file_location(
        "tool_call_smoke", ROOT / "scripts" / "check-vlm-tool-calling.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    reply = {"choices": [{"message": {
        "role": "assistant", "content": "<tool_call>...</tool_call>",
        "tool_calls": None,
    }}]}
    with pytest.raises(AssertionError, match="Invalid calls"):
        module.parsed_tool_call(reply)
