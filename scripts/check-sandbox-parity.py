#!/usr/bin/env python3
"""Проверка политики песочницы. Это НЕ доказательство enforcement (§13.4)."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kadr.security.sandbox import load_policy  # noqa: E402


def main() -> int:
    policy = load_policy()
    result = policy.parity()
    worker = (REPO_ROOT / "kadr" / "workers" / "parser_worker.py").read_text(encoding="utf-8")
    tree = ast.parse(worker)
    apply_limits = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_apply_limits"
        ),
        None,
    )
    # Считаем enforcement только по фактическому вызову resource.setrlimit
    # внутри _apply_limits, а не по упоминанию в комментарии/строке.
    enforced = apply_limits is not None and any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setrlimit"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "resource"
        for node in ast.walk(apply_limits)
    )
    payload = {
        "policy_hash": policy.policy_hash(),
        "design_parity": result,
        "enforcement": {
            "parser_worker": "rlimits/timeouts/process-group" if enforced else "none",
            "seccomp_seatbelt_job_object": "M3 (not enforced in v0.1)",
            "native_vs_docker_metrics": "not claimed",
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result["equal"] and enforced else 1


if __name__ == "__main__":
    raise SystemExit(main())
