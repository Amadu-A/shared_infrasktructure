# tests/test_vlm_tool_calling.py
"""Проверяет API-validator; старые требования создавать override удалены."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def smoke():
    """Загружает API-validator без HTTP-запросов и выполнения его main."""
    path = Path(__file__).resolve().parents[1] / "scripts/check-vlm-tool-calling.py"
    spec = importlib.util.spec_from_file_location("tool_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_validator_accepts_structured_function_call(smoke):
    """Принимает правильный tool call и отвергает испорченный JSON arguments."""
    call = {"id": "call_example", "type": "function",
            "function": {"name": "check_status", "arguments": '{"check":"ready"}'}}
    reply = {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}
    assert smoke.parsed_tool_call(reply) == call
    call["function"]["arguments"] = "{invalid-json"
    with pytest.raises(json.JSONDecodeError):
        smoke.parsed_tool_call(reply)


def test_api_validator_rejects_raw_xml_in_content(smoke):
    """Не принимает текстовую tool-разметку вместо structured tool_calls."""
    reply = {"choices": [{"message": {"role": "assistant", "content": "<tool_call>...</tool_call>", "tool_calls": None}}]}
    with pytest.raises(AssertionError):
        smoke.parsed_tool_call(reply)
    with pytest.raises(AssertionError):
        smoke.assert_text(reply)
