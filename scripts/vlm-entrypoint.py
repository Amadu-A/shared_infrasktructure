#!/usr/bin/env python3
# scripts/vlm-entrypoint.py
"""Проверяет обязательные параметры VLM/embedding до запуска модели в Docker.

Использует ту же валидацию, что bootstrap/check, затем заменяет себя штатным
`vllm serve` образа v0.29.0. GPU, TP/DP и контекст самостоятельно не выбирает.
"""

import os
import sys

import deployment as d


def main(argv: list[str] | None = None) -> int:
    """Отказывает до загрузки весов при неполном или противоречивом запуске."""
    args = sys.argv[1:] if argv is None else argv
    name = args[0] if args and args[0] in d.GPU_PLACEMENT else "shared-vlm"
    if args and args[0] in d.GPU_PLACEMENT:
        args = args[1:]
    service = {"command": args, "environment": dict(os.environ)}
    try:
        d.validate_config({"services": {name: service}})
    except d.DeploymentError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    os.execvp("vllm", ["vllm", "serve", *args])
    return 0


if __name__ == "__main__":
    sys.exit(main())
