"""L0-метаданные: извлечение, даты, санитизация недоверенного текста."""

from __future__ import annotations

from .dates import DateResolution, resolve_taken_at
from .extractor import extract_metadata
from .sanitize import detect_injection, detect_pii, redact_pii, sanitize_text

__all__ = [
    "DateResolution",
    "detect_injection",
    "detect_pii",
    "extract_metadata",
    "redact_pii",
    "resolve_taken_at",
    "sanitize_text",
]
