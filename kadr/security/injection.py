"""Детектор prompt injection для метаданных/OCR/ASR (T01/T02)."""

from __future__ import annotations

from ..metadata.sanitize import detect_injection, sanitize_text

__all__ = ["detect_injection", "sanitize_text"]
