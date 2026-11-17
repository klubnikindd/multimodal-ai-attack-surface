#!/usr/bin/env python3
"""PR-гейт sidecar-lifecycle (§11.2.4): нет осиротевших процессов после выхода."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kadr.lifecycle import (  # noqa: E402
    single_instance_lock,
    spawn_sidecar,
    terminate_group,
    wait_for_no_orphans,
)


def main() -> int:
    payload: dict[str, object] = {"ok": True, "checks": []}
    process = spawn_sidecar([sys.executable, "-c", "import time; time.sleep(60)"])
    pid = process.pid
    try:
        time.sleep(0.1)
        terminate_group(process, grace=0.5)
        alive = wait_for_no_orphans([pid], timeout=2.0)
        payload["checks"].append({"name": "process_group_shutdown", "alive": alive})
        if alive:
            payload["ok"] = False
    finally:
        if process.poll() is None:
            process.kill()
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "engine.lock"
        with single_instance_lock(lock) as first:
            with single_instance_lock(lock) as second:
                payload["checks"].append({"name": "single_instance_lock", "first": first, "second": second})
                if not first or second:
                    payload["ok"] = False
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
