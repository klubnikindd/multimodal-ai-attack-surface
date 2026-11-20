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


def hf_lfs_oid(
    repo_url: str, revision: str, filename: str, *, timeout: float = 60.0
) -> tuple[str | None, int | None]:
    """LFS oid (sha256) и размер файла — через tree API с expand=true.

    ETag CDN без ?download=true НЕ равен LFS oid (проверено на Qwen3-VL-2B:
    ETag давал неверный хэш), поэтому берём «lfs.oid» из tree API запиненной
    ревизии — это детерминированный sha256, совпадающий с файлом.
    """
    repo_id = repo_url.rstrip("/")
    for prefix in ("https://huggingface.co/", "http://huggingface.co/"):
        repo_id = repo_id.removeprefix(prefix)
    url = (
        f"https://huggingface.co/api/models/{repo_id}/tree/{revision}"
        "?recursive=true&expand=true"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "kadr-model-integrity/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        entries = json.loads(response.read().decode("utf-8"))
    for entry in entries:
        if entry.get("path") == filename and isinstance(entry.get("lfs"), dict):
            oid = str(entry["lfs"].get("oid") or "").lower() or None
            size = entry["lfs"].get("size")
            return oid, int(size) if size is not None else None
    return None, None


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
                actual_size = path.stat().st_size
                source = "local"
            elif repo_url and revision:
                try:
                    actual, remote_size = hf_lfs_oid(repo_url, revision, name)
                    actual_size = remote_size
                    source = "hf-lfs-oid"
                except Exception as exc:  # noqa: BLE001 - fail-closed по §12.3
                    issues.append(f"{model_id}: не удалось проверить {name} (HF недоступен: {exc})")
                    continue
            else:
                issues.append(f"{model_id}: missing file {path} и нет revision/url для проверки")
                continue
            size_ok = actual_size is None or actual_size == int(file.get("bytes") or 0)
            if actual and actual == expected and size_ok:
                checked.append({"model": model_id, "file": name, "sha256": actual, "source": source})
            elif actual and actual == expected and not size_ok:
                issues.append(
                    f"{model_id}: size mismatch for {name} (expected {file.get('bytes')}, got {actual_size})"
                )
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
