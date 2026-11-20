#!/usr/bin/env python3
"""PR-гейт sidecar-lifecycle (§11.2.4 v3.1): нет осиротевших процессов после выхода.

M0-минимум — три сценария:
  A. terminate_group убивает новую process group / job;
  B. single-instance lock не даёт поднять второй Engine;
  C. «внук» (дочерний процесс sidecar) умирает вместе с группой.

Полный набор сценариев §11.2.3 — релизный гейт M6 (см. план P7-07).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kadr.lifecycle import (  # noqa: E402
    single_instance_lock,
    spawn_sidecar,
    terminate_group,
    wait_for_no_orphans,
)

SLEEPER = "import time; time.sleep(120)"
GRANDCHILD_PARENT = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print('KADR_GRANDCHILD', child.pid, flush=True)\n"
    "time.sleep(120)\n"
)


def _wait_marker(process: Any, marker: str, *, timeout: float = 10.0) -> int:
    """Ждёт строку-sidecar и возвращает PID из неё (stdout вычитывается драйном)."""
    deadline = time.monotonic() + timeout
    seen = 0
    while time.monotonic() < deadline:
        lines = list(getattr(process, "kadr_lines", []))
        for line in lines[seen:]:
            if line.startswith(marker):
                return int(line.split()[1])
        seen = len(lines)
        time.sleep(0.02)
    raise TimeoutError(f"sidecar did not print {marker}")


def _scenario_process_group(payload: dict[str, Any]) -> None:
    process = spawn_sidecar([sys.executable, "-c", SLEEPER])
    pid = process.pid
    try:
        time.sleep(0.1)
        terminate_group(process, grace=0.5)
        alive = wait_for_no_orphans([pid], timeout=2.0)
    finally:
        if process.poll() is None:
            process.kill()
    payload["checks"].append({"name": "process_group_shutdown", "alive": alive})
    if alive:
        payload["ok"] = False


def _scenario_grandchild(payload: dict[str, Any]) -> None:
    process = spawn_sidecar([sys.executable, "-c", GRANDCHILD_PARENT])
    try:
        grandchild = _wait_marker(process, "KADR_GRANDCHILD")
        time.sleep(0.1)
        terminate_group(process, grace=1.0)
        alive = wait_for_no_orphans([process.pid, grandchild], timeout=3.0)
    finally:
        if process.poll() is None:
            process.kill()
    payload["checks"].append({"name": "grandchild_shutdown", "alive": alive})
    if alive:
        payload["ok"] = False


def _scenario_single_instance(payload: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "engine.lock"
        with single_instance_lock(lock) as first:
            with single_instance_lock(lock) as second:
                payload["checks"].append(
                    {"name": "single_instance_lock", "first": first, "second": second}
                )
                if not first or second:
                    payload["ok"] = False


def main() -> int:
    payload: dict[str, Any] = {"ok": True, "checks": []}
    _scenario_process_group(payload)
    _scenario_grandchild(payload)
    _scenario_single_instance(payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
