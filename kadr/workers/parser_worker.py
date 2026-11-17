"""Дочерний процесс разбора: rlimits, таймаут, без shell.

Запускается только из kadr.workers.client. Не является сетевым сервисом.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from ..constants import DEFAULT_OCR_LANGUAGE, OCR_LANGUAGE_PATTERN
from ..metadata.extractor import extract_metadata
from ..metadata.sanitize import sanitize_text
from ..metadata.serialization import metadata_to_payload


def _apply_limits(limits: dict[str, Any]) -> None:
    try:
        import resource

        cpu = int(limits.get("cpu_seconds") or 10)
        memory = int(limits.get("memory_mb") or 1024) * 1024 * 1024
        fsize = int(limits.get("file_size_mb") or 512) * 1024 * 1024
        nofile = int(limits.get("open_files") or 128)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        current_as, _ = resource.getrlimit(resource.RLIMIT_AS)
        address_limit = max(memory, current_as if current_as > 0 else 0)
        resource.setrlimit(resource.RLIMIT_AS, (address_limit, address_limit))
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))
    except Exception:
        # Windows/ограниченные контейнеры: остаётся таймаут и process group родителя.
        pass
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = int(limits.get("max_pixels") or 100_000_000)
    except Exception:
        pass


def _ocr(path: str, limits: dict[str, Any], lang: str = DEFAULT_OCR_LANGUAGE) -> dict[str, Any]:
    import shutil

    language = str(lang or DEFAULT_OCR_LANGUAGE).strip()
    if not OCR_LANGUAGE_PATTERN.fullmatch(language):
        return {"available": False, "text": "", "error": f"invalid ocr language: {lang!r}"}
    binary = shutil.which("tesseract")
    if not binary:
        return {"available": False, "text": "", "error": "tesseract not installed"}
    try:
        completed = subprocess.run(  # noqa: S603 - argv без shell
            [binary, path, "stdout", "-l", language],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(limits.get("timeout_seconds") or 10),
            check=False,
        )
        return {
            "available": True,
            "text": sanitize_text(completed.stdout, limit=20000),
            "error": completed.stderr[:300] if completed.returncode else None,
        }
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        return {"available": True, "text": "", "error": str(exc)}


def main() -> int:
    try:
        request = json.load(sys.stdin)
        limits = dict(request.get("limits") or {})
        options = dict(request.get("options") or {})
        _apply_limits(limits)
        task = str(request.get("task") or "metadata")
        path = str(request["path"])
        if task == "metadata":
            result = {
                "ok": True,
                "metadata": metadata_to_payload(extract_metadata(path)),
                "limits_applied": limits,
            }
        elif task == "ocr":
            result = {
                "ok": True,
                "ocr": _ocr(path, limits, str(options.get("lang") or DEFAULT_OCR_LANGUAGE)),
                "limits_applied": limits,
            }
        else:
            result = {"ok": False, "error": f"unknown task {task}"}
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.flush()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
