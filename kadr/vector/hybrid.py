"""Гибридный бэкенд: FTS5/метаданные сужают кандидатов, эмбеддинги реранжируют."""

from __future__ import annotations

from collections.abc import Sequence

from ..models import IndexStatus
from .brute_force import BruteForceVectorIndex


class HybridVectorIndex(BruteForceVectorIndex):
    backend = "hybrid"

    def search(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        candidate_ids: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        if candidate_ids is not None and not candidate_ids:
            return []
        return super().search(query_vector, k=k, candidate_ids=candidate_ids)

    def status(self) -> IndexStatus:
        base = super().status()
        if base.status == "active":
            base.reason = "FTS5/metadata narrowing + exact embedding rerank"
        return base


__all__ = ["HybridVectorIndex"]
