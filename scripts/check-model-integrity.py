#!/usr/bin/env python3
"""model-integrity: sha256/revision для enabled-моделей (§12.3).

В v0.1 enabled-моделей нет (веса disabled до M0-аудита), поэтому job проходит
с предупреждением. Как только модель включается, отсутствие файла/хэша — FAIL.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kadr.licensing import load_manifest, validate_manifest  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    data = load_manifest()
    report = validate_manifest(data)
    issues = list(report.issues)
    checked = []
    for model in data.get("models", []):
        if not isinstance(model, dict) or not model.get("enabled"):
            continue
        model_dir = REPO_ROOT / "models" / str(model["id"])
        for file in model.get("files", []):
            path = model_dir / str(file.get("name"))
            if not path.is_file():
                issues.append(f"{model['id']}: missing file {path}")
                continue
            actual = sha256_file(path)
            if actual.lower() != str(file.get("sha256", "")).lower():
                issues.append(f"{model['id']}: sha256 mismatch for {path.name}")
            checked.append({"model": model["id"], "file": path.name, "sha256": actual})
    print(json.dumps({"ok": not issues, "checked": checked, "issues": issues, "warnings": report.warnings}, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
