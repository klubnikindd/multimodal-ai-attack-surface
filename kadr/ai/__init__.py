"""AI-lite: эмбеддинги, zero-shot теги, OCR, классификация, VLM-контракт."""

from __future__ import annotations

from .classify import classify_asset
from .embeddings import EmbeddingBackend, get_embedding_backend
from .vlm import NightlyVLMPlanner, VLMValidation, validate_vlm_output

__all__ = [
    "EmbeddingBackend",
    "NightlyVLMPlanner",
    "VLMValidation",
    "classify_asset",
    "get_embedding_backend",
    "validate_vlm_output",
]
