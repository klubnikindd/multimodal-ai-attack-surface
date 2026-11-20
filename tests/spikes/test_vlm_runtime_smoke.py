"""Smoke-тест VLM-runtime (P6-03): без бинарей — честный not_measured."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_vlm_runtime_honest_not_measured(monkeypatch):
    monkeypatch.delenv("KADR_GGUF", raising=False)
    monkeypatch.delenv("KADR_MMPROJ", raising=False)
    monkeypatch.delenv("KADR_LLAMA_SERVER", raising=False)
    proc = subprocess.run(  # noqa: S603 - фиксированный argv
        [sys.executable, str(REPO_ROOT / "tests" / "spikes" / "vlm_runtime.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["status"] == "not_measured"
    assert data["reason"]
