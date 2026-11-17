"""Изолированные worker-процессы для разбора недоверенных медиа."""

from __future__ import annotations

from .client import extract_metadata_sandboxed, run_ocr_sandboxed

__all__ = ["extract_metadata_sandboxed", "run_ocr_sandboxed"]
