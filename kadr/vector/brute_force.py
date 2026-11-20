"""Точный brute-force индекс (fallback без расширений)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .base import VectorIndex, dot, normalize, unpack_vector


class BruteForceVectorIndex(VectorIndex):
    backend = "brute-force"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cache: dict[int, list[float]] | None = None

    def _load(self) -> dict[int, list[float]]:
        if self._cache is None:
            rows = self.catalog.get_embeddings(self.model_id, self.index_generation)
            self._cache = {}
            first = True
            for row in rows:
                row_dim = int(row["dim"])
                if first:
                    self.dim = row_dim
                    first = False
                elif row_dim != self.dim:
                    raise ValueError(f"embedding dim mismatch: {row_dim} != {self.dim}")
                self._cache[int(row["asset_id"])] = unpack_vector(row["vec"], row_dim)
        return self._cache

    def add(self, asset_id: int, vector: Sequence[float]) -> None:
        values = normalize(vector)
        self.dim = len(values)
        self.catalog.upsert_embedding(
            asset_id, self.model_id, values, index_generation=self.index_generation
        )
        if self._cache is not None:
            self._cache[asset_id] = values
        self.record_status()

    def add_many(self, rows: Iterable[tuple[int, Sequence[float]]]) -> int:
        count = 0
        for asset_id, vector in rows:
            values = normalize(vector)
            self.dim = len(values)
            self.catalog.upsert_embedding(
                asset_id, self.model_id, values, index_generation=self.index_generation
            )
            count += 1
        self._cache = None
        self.record_status()
        return count

    def remove(self, asset_id: int) -> None:
        self.catalog.conn.execute(
            "DELETE FROM embeddings WHERE asset_id=? AND model_id=? AND index_generation=?",
            (asset_id, self.model_id, self.index_generation),
        )
        if self._cache is not None:
            self._cache.pop(asset_id, None)
        self.record_status()

    def search(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        candidate_ids: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        if k <= 0:
            return []
        query = normalize(query_vector)
        cache = self._load()
        items: Iterable[tuple[int, list[float]]] = cache.items()
        if candidate_ids is not None:
            items = ((asset_id, vec) for asset_id, vec in cache.items() if asset_id in candidate_ids)
        scored = []
        for asset_id, vector in items:
            if len(vector) != len(query):
                continue
            scored.append((asset_id, dot(query, vector)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    def count(self) -> int:
        return self.catalog.count_embeddings(self.model_id, self.index_generation)


__all__ = ["BruteForceVectorIndex"]
