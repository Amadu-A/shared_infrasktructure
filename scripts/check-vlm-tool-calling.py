
#!/usr/bin/env python3
# scripts/check-vlm-tool-calling.py
"""Read-only API contract smoke tests: no supplied tool is actually executed."""

from __future__ import annotations

import argparse
import base64
import json
import struct
import sys
import urllib.error
import urllib.request
import zlib

TOOL = {
    "type": "function",
    "function": {
        "name": "check_status",
        "description": "Return a status for the named check (fake tool).",
        "parameters": {
            "type": "object",
            "properties": {"check": {"type": "string"}},
            "required": ["check"],
            "additionalProperties": False,
        },
    },
}


def request(url: str, payload: dict, timeout: int = 180) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                raise AssertionError(f"HTTP {response.status}")
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise AssertionError(
            f"HTTP {exc.code}: {exc.read(2048).decode('utf-8', 'replace')}"
        ) from exc


def message(reply: dict) -> dict:
    assert reply["choices"], "Missing choices"
    result = reply["choices"][0]["message"]
    assert result["role"] == "assistant"
    return result


def assert_text(reply: dict) -> str:
    msg = message(reply)
    assert not msg.get("tool_calls"), "Unexpected tool_calls"
    content = msg.get("content")
    assert isinstance(content, str) and content.strip(), f"Missing content: {msg}"
    assert "<tool_call>" not in content and "<function=" not in content, (
        f"Unparsed tool markup leaked into content: {content[:500]}"
    )
    return content


def parsed_tool_call(reply: dict) -> dict:
    calls = message(reply).get("tool_calls")
    assert isinstance(calls, list) and len(calls) == 1, f"Invalid calls: {calls}"
    call = calls[0]
    assert isinstance(call.get("id"), str) and call["id"]
    assert call.get("type") == "function"
    function = call.get("function")
    assert isinstance(function, dict) and function.get("name") == "check_status"
    arguments = function.get("arguments")
    assert isinstance(arguments, str)
    parsed = json.loads(arguments)
    assert parsed == {"check": "ready"}, f"Wrong arguments: {parsed!r}"
    return call


def base(model: str, messages: list[dict], **kwargs: object) -> dict:
    return {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 768,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
        **kwargs,
    }


def png_data_url() -> str:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xFFFFFFFF
        )

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 32 for _ in range(32))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">2I5B", 32, 32, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="shared-vlm")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--min-success", type=float, default=1.0)
    parser.add_argument("--long-words", type=int, default=0,
                        help="Optional long-context test; sends this many repetitions")
    parser.add_argument("--no-vision", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or not 0 <= args.min_success <= 1:
        parser.error("--repeats must be positive and --min-success within 0..1")

    failures: list[str] = []

    def check(name: str, fn: object) -> None:
        try:
            fn()
            print(f"PASS {name}", flush=True)
        except (AssertionError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
            failures.append(name)
            print(f"FAIL {name}: {exc}", flush=True)

    def post(payload: dict) -> dict:
        return request(args.url, payload)

    def text() -> None:
        assert_text(post(base(args.model, [
            {"role": "user", "content": "Ответь одним словом: РАБОТАЕТ"}
        ])))

    check("A: text without tools", text)

    succeeded: list[dict] = []
    tool_messages = [
        {
            "role": "system",
            "content": "When asked to call check_status, call the tool; do not answer as text.",
        },
        {"role": "user", "content": "Вызови функцию check_status с параметром check равным ready."},
    ]
    for i in range(args.repeats):
        try:
            result = post(base(args.model, tool_messages, tools=[TOOL], tool_choice="auto"))
            parsed_tool_call(result)
            succeeded.append(result)
            if len(succeeded) == 1:
                print("SANITIZED_REAL_TOOL_CALL_RESPONSE:")
                sanitized = json.loads(json.dumps(result))
                sanitized.pop("id", None)
                sanitized.pop("created", None)
                print(json.dumps(sanitized, ensure_ascii=False, indent=2))
        except (AssertionError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
            print(f"FAIL B repetition {i + 1}: {exc}")
    rate = len(succeeded) / args.repeats
    print(f"B: tool_choice=auto {len(succeeded)}/{args.repeats} = {rate:.1%}")
    if rate < args.min_success:
        failures.append("B: tool_choice=auto")
    else:
        print("PASS B: structured tool_calls + name + JSON arguments")

    def continuation() -> None:
        if not succeeded:
            raise AssertionError("No successful tool call to continue")
        call = parsed_tool_call(succeeded[0])
        reply = post(base(args.model, [
            *tool_messages,
            {"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": call["id"], "name": "check_status",
             "content": '{"check":"ready","status":"READY_CONFIRMED"}'},
            {"role": "user", "content": "Какой статус вернул инструмент? Ответь текстом."},
        ]))
        content = assert_text(reply)
        assert ("ready" in content.lower() or "готов" in content.lower()), content

    check("C: continuation after tool reply", continuation)

    def none() -> None:
        assert_text(post(base(args.model, [
            {"role": "user", "content": "Ответь ровно: ОК"}
        ], tools=[TOOL], tool_choice="none")))

    check("D: tool_choice=none", none)
    check("D: no tools", text)

    def vision() -> None:
        answer = assert_text(post(base(args.model, [
            {"role": "user", "content": [
                {"type": "text", "text": "Какого цвета изображение?"},
                {"type": "image_url", "image_url": {"url": png_data_url()}},
            ]},
        ])))
        assert "red" in answer.lower() or "крас" in answer.lower(), answer

    if args.no_vision:
        print("SKIP E: vision (explicitly disabled)")
    else:
        check("E: vision", vision)

    def reasoning() -> None:
        payload = base(args.model, [
            {"role": "user", "content": "Сколько будет 2 + 2? Ответь кратко."}
        ])
        payload["chat_template_kwargs"] = {"enable_thinking": True}
        payload["max_tokens"] = 2048
        answer = post(payload)
        msg = message(answer)
        assert isinstance(msg.get("content"), str) and msg["content"].strip()
        assert not msg.get("tool_calls")

    check("E: reasoning-mode response", reasoning)

    if args.long_words:
        def long_context() -> None:
            content = "начало " + ("проверка " * args.long_words) + " конец. Ответь: ГОТОВО"
            assert_text(post(base(args.model, [{"role": "user", "content": content}])))
        check("E: long context", long_context)
    else:
        print("SKIP E: long context (pass --long-words to enable)")

    print("Note: PDRD, Open WebUI and MTP regression require separate client/metrics checks.")
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    print("ALL EXECUTED API TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
