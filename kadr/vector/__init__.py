"""Сменный векторный бэкенд `VectorIndex` (FR-3.13, ADR-0004)."""

from __future__ import annotations

from .base import VectorIndex, pack_vector, unpack_vector
from .factory import create_vector_index

__all__ = ["VectorIndex", "create_vector_index", "pack_vector", "unpack_vector"]
