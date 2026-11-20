"""Smoke-тест харнесса спайка E: 200 векторов, recall должен быть полным."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_scale_bench_smoke(tmp_path):
    out = tmp_path / "result.json"
    proc = subprocess.run(  # noqa: S603 - фиксированный argv
        [
            sys.executable,
            str(REPO_ROOT / "tests" / "scale" / "bench_scale.py"),
            "--vectors",
            "200",
            "--dim",
            "16",
            "--backend",
            "sqlite-vec",
            "--queries",
            "2",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["backend"] == "sqlite-vec"
    assert data["index_status"] == "active"
    assert data["recall_at_10"] >= 0.99
    assert data["p95_ms"] > 0
