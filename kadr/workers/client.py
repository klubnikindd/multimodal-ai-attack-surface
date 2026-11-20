"""Родительская сторона parser-worker: лимиты, таймаут, kill process group."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..constants import DEFAULT_OCR_LANGUAGE, KIND_BY_EXTENSION
from ..lifecycle import terminate_group
from ..metadata.dates import resolve_taken_at
from ..metadata.serialization import metadata_from_payload
from ..models import MediaMetadata
from ..security.sandbox import load_policy

REPO_ROOT = Path(__file__).resolve().parents[2]
if getattr(sys, "frozen", False):
    # В one-dir-бандле sys.executable — kadr-engine.exe; «-m» он не понимает,
    # поэтому worker вызывается скрытой CLI-командой (спайк C).
    _WORKER = [sys.executable, "worker-parser"]
else:
    _WORKER = [sys.executable, "-m", "kadr.workers.parser_worker"]
MAX_WORKER_STDOUT_BYTES = 16 * 1024 * 1024
MAX_WORKER_STDERR_BYTES = 1 * 1024 * 1024


class WorkerError(RuntimeError):
    pass


def _read_limited(stream, limit: int, name: str) -> bytes:
    """Читает не больше limit байт: вредоносный worker не должен съесть RAM."""
    stream.seek(0)
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise WorkerError(f"parser worker {name} exceeds {limit} bytes")
    return data


def run_worker(
    task: str,
    path: str | Path,
    *,
    limits: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = load_policy()
    effective = dict(limits or policy.limits)
    request = json.dumps(
        {"task": task, "path": str(path), "limits": effective, "options": dict(options or {})}
    ).encode("utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    timeout = float(effective.get("timeout_seconds") or 10) + 2.0
    # stdout/stderr — во временные файлы, а не PIPE: неограниченный вывод
    # сбойного worker не должен буферизоваться в памяти родителя.
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": stdout_file,
            "stderr": stderr_file,
            "env": env,
        }
        if os.name == "nt":  # pragma: no cover - Windows
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(_WORKER, **popen_kwargs)  # noqa: S603 - argv без shell
        try:
            process.communicate(request, timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_group(process, grace=1.0)
            raise WorkerError(f"parser worker timeout after {timeout:.1f}s") from None
        finally:
            if process.stdin is not None and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    terminate_group(process, grace=0.5)
        stdout = _read_limited(stdout_file, MAX_WORKER_STDOUT_BYTES, "stdout")
        stderr = _read_limited(stderr_file, MAX_WORKER_STDERR_BYTES, "stderr")
    if process.returncode not in {0, None}:
        # Worker вернул ненулевой код: пробуем достать причину из его JSON
        # (DecompressionBombError и пр.), а не только stderr (часто пустой).
        detail = stderr.decode("utf-8", errors="replace")[:300]
        try:
            parsed = json.loads(stdout.decode("utf-8", errors="replace"))
            if isinstance(parsed, dict) and parsed.get("error"):
                detail = str(parsed["error"])[:300]
        except (ValueError, UnicodeDecodeError):
            pass
        raise WorkerError(f"parser worker exited {process.returncode}: {detail}")
    if not stdout:
        raise WorkerError(
            f"parser worker empty output: {stderr.decode('utf-8', errors='replace')[:300]}"
        )
    text = stdout.decode("utf-8", errors="replace").strip()
    try:
        payload = json.loads(text)
    except ValueError:
        # На случай сторонних print в импортах: берём последнюю JSON-строку.
        for line in reversed(text.splitlines()):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
        else:
            raise WorkerError(f"parser worker invalid JSON: {text[:300]}") from None
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise WorkerError(
            str(payload.get("error") if isinstance(payload, dict) else "worker failed")
        )
    return payload


def _fallback_metadata(path: str | Path, error: str) -> MediaMetadata:
    file_path = Path(path)
    try:
        stat = file_path.stat()
        mtime = stat.st_mtime
        size = stat.st_size
    except OSError:
        mtime, size = None, 0
    resolution = resolve_taken_at(filename=file_path.name, mtime=mtime)
    return MediaMetadata(
        kind=KIND_BY_EXTENSION.get(file_path.suffix.lower(), "other"),
        media_class="unknown",
        taken_at=resolution.taken_at,
        taken_at_source=resolution.source,
        date_confidence=resolution.confidence,
        tz_offset=resolution.tz_offset,
        raw_json={"sandbox_error": error, "file": {"name": file_path.name, "size_bytes": size}},
        parse_errors=[f"parser worker: {error}"],
    )


def extract_metadata_sandboxed(path: str | Path) -> MediaMetadata:
    try:
        payload = run_worker("metadata", path)
        meta = metadata_from_payload(payload["metadata"])
        meta.raw_json["sandboxed"] = True
        return meta
    except (WorkerError, OSError, ValueError, KeyError) as exc:
        return _fallback_metadata(path, str(exc))


def run_ocr_sandboxed(path: str | Path, *, lang: str = DEFAULT_OCR_LANGUAGE) -> dict[str, Any]:
    try:
        return dict(run_worker("ocr", path, options={"lang": lang}).get("ocr") or {})
    except (WorkerError, OSError, ValueError) as exc:
        return {"available": False, "text": "", "error": str(exc)}


__all__ = ["WorkerError", "extract_metadata_sandboxed", "run_ocr_sandboxed", "run_worker"]
