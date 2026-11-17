#!/usr/bin/env python3
"""Релизный гейт: PyInstaller one-file (-F) запрещён (D10/§11.2.1)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = REPO_ROOT / "packaging" / "kadr-engine.spec"


def main() -> int:
    if not SPEC.is_file():
        print(f"FAIL: spec not found: {SPEC}")
        return 1
    text = SPEC.read_text(encoding="utf-8")
    lowered = text.lower()
    forbidden = ["onefile=true", "onefile = true", "--onefile"]
    hits = [marker for marker in forbidden if marker in lowered]
    if hits:
        print(f"FAIL: one-file markers found: {hits}")
        return 1
    if "collect(" not in lowered or "exclude_binaries=true" not in lowered:
        print("FAIL: spec does not look like one-dir (COLLECT/exclude_binaries)")
        return 1
    print(f"OK: one-dir spec: {SPEC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
