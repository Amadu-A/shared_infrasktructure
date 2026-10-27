#!/usr/bin/env python3
# scripts/check-shared-contracts.py
"""Проверяет одновременные synthetic-запросы VLM и embedding без реальных данных.

Это проверка shared API, а не замена приёмке настоящих PDRD/OpenClaw/Open WebUI.
Модели, контейнеры и индексы скрипт не изменяет; Authorization не добавляет.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import sys
import urllib.request


def post(url: str, payload: dict) -> dict:
    """Отправляет synthetic-запрос с ограниченным временем ожидания."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def validate_text(reply: dict) -> None:
    """Отклоняет пустой ответ и утечку неразобранной tool-разметки."""
    content = reply["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip() or "<tool_call>" in content or "<function=" in content:
        raise ValueError("Некорректный text contract")


def vector_dimension(reply: dict) -> int:
    """Проверяет непустой конечный вектор, не публикуя сами embeddings."""
    vector = reply["data"][0]["embedding"]
    if not isinstance(vector, list) or not vector or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in vector
    ):
        raise ValueError("Некорректный embedding contract")
    return len(vector)


def main(argv: list[str] | None = None) -> int:
    """Запускает три VLM-запроса и embedding одновременно; печатает только итог."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vlm-url", required=True, help="Base URL с /v1")
    parser.add_argument("--embedding-url", required=True, help="Base URL с /v1")
    parser.add_argument("--embedding-dimension", type=int)
    args = parser.parse_args(argv)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            vlm = [executor.submit(post, args.vlm_url.rstrip("/") + "/chat/completions", {
                "model": "shared-vlm", "messages": [{"role": "user", "content": f"Проверка {index}: ответь ОК"}],
                "max_tokens": 32, "temperature": 0,
            }) for index in range(3)]
            embedding = executor.submit(post, args.embedding_url.rstrip("/") + "/embeddings", {
                "model": "shared-embedding", "input": "Проверка общей инфраструктуры",
            })
            for future in vlm:
                validate_text(future.result())
            dimension = vector_dimension(embedding.result())
            if args.embedding_dimension is not None and dimension != args.embedding_dimension:
                raise ValueError("Изменилась размерность embedding")
        print(f"PASS: три VLM-запроса и embedding; vector dimension={dimension}")
        return 0
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        print("FAIL: проверьте shared API локально; тела ответов не выводятся.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
