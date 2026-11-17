#!/usr/bin/env python3
"""Переименовать one-dir sidecar под Tauri target-triple (§11.2.1)."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kadr.lifecycle import sidecar_binary_name, target_triple  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", default="dist/kadr-engine")
    parser.add_argument("--out", default="binaries")
    parser.add_argument("--triple", default=None)
    args = parser.parse_args()
    source = Path(args.dist)
    if not source.exists():
        print(f"FAIL: {source} does not exist; run pyinstaller packaging/kadr-engine.spec", file=sys.stderr)
        return 1
    triple = args.triple or target_triple()
    name = sidecar_binary_name("kadr-engine", triple=triple)
    out = Path(args.out) / name
    out.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(source, out)
    else:
        shutil.copy2(source, out)
    print(f"OK: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
