#!/usr/bin/env python3
"""model-integrity (§12.3): sha256 каждого файла enabled-модели.

Порядок проверки для каждого файла:
  1. локальный файл models/<id>/<name> → сравнение sha256 с манифестом;
  2. файла нет локально → HEAD resolve-URL запиненной ревизии на HF, ETag
     ответа (LFS oid = sha256) сравнивается с манифестом. Файлы весов в git
     не хранятся, поэтому CI проверяет по сети; локальный прогон без сети —
     fail-closed (это соответствует §12.3: «любое расхождение — падение»).
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
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


def hf_etag(repo_url: str, revision: str, filename: str, *, timeout: float = 60.0) -> str | None:
    """LFS oid (sha256) файла на HF: ETag ответа resolve-URL запиненной ревизии."""
    url = f"{repo_url.rstrip('/')}/resolve/{revision}/{filename}"
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "kadr-model-integrity/1"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        etag = response.headers.get("ETag") or ""
    cleaned = etag.strip().strip('"')
    return cleaned.lower() or None


def main() -> int:
    data = load_manifest()
    report = validate_manifest(data)
    issues = list(report.issues)
    checked: list[dict[str, str]] = []
    for model in data.get("models", []):
        if not isinstance(model, dict) or not model.get("enabled"):
            continue
        model_id = str(model["id"])
        model_dir = REPO_ROOT / "models" / model_id
        repo_url = str(model.get("license_source_url") or "")
        revision = str(model.get("revision") or "")
        for file in model.get("files", []):
            name = str(file.get("name"))
            expected = str(file.get("sha256", "")).lower()
            path = model_dir / name
            if path.is_file():
                actual = sha256_file(path)
                source = "local"
            elif repo_url and revision:
                try:
                    actual = hf_etag(repo_url, revision, name)
                    source = "hf-etag"
                except Exception as exc:  # noqa: BLE001 - fail-closed по §12.3
                    issues.append(f"{model_id}: не удалось проверить {name} (HF недоступен: {exc})")
                    continue
            else:
                issues.append(f"{model_id}: missing file {path} и нет revision/url для проверки")
                continue
            if actual and actual == expected:
                checked.append({"model": model_id, "file": name, "sha256": actual, "source": source})
            else:
                issues.append(
                    f"{model_id}: sha256 mismatch for {name} (expected {expected[:12]}…, got {str(actual)[:12]}…)"
                )
    print(
        json.dumps(
            {"ok": not issues, "checked": checked, "issues": issues, "warnings": report.warnings},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
