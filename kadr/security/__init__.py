"""Защитный контур Kadr/MIAS: пути, PII, инъекции, песочница, auth."""

from __future__ import annotations

from .paths import (
    is_network_path,
    normalize_rel_path,
    safe_join,
    sanitize_segment,
    validate_destination,
    validate_scan_root,
)
from .pii import detect_pii, redact_pii

__all__ = [
    "detect_pii",
    "is_network_path",
    "normalize_rel_path",
    "redact_pii",
    "safe_join",
    "sanitize_segment",
    "validate_destination",
    "validate_scan_root",
]
