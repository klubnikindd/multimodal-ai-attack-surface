"""Smoke-тест харнесса спайка D: без устройств отвечает честным not_measured."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_vlm_bench_honest_not_measured():
    proc = subprocess.run(  # noqa: S603 - фиксированный argv
        [sys.executable, str(REPO_ROOT / "tests" / "spikes" / "vlm_bench.py")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "not_measured"
    assert data["reason"]
