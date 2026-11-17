"""Интерфейс VectorIndex. Домен не знает, какой бэкенд внутри."""

from __future__ import annotations

import math
import struct
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence

from ..catalog.repository import Catalog
from ..models import IndexStatus


def pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *[float(x) for x in vector])


def unpack_vector(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def normalize(vector: Sequence[float]) -> list[float]:
    values = [float(x) for x in vector]
    norm = math.sqrt(sum(x * x for x in values))
    if norm <= 1e-12:
        return values
    return [x / norm for x in values]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


class VectorIndex(ABC):
    """Контракт сменного индекса.

    ``candidate_ids`` — необязательное сужение (FTS5/метаданные), которое
    используется гибридным бэкендом; точный бэкенд тоже обязан его учитывать.
    """

    backend: str = "abstract"

    def __init__(
        self,
        catalog: Catalog,
        *,
        model_id: str = "concept-hash-v1",
        index_generation: int | None = None,
        dim: int = 256,
    ) -> None:
        self.catalog = catalog
        self.model_id = model_id
        self.index_generation = index_generation or catalog.current_index_generation(model_id)
        self.dim = dim

    # ------------------------------------------------------------------ data
    @abstractmethod
    def add(self, asset_id: int, vector: Sequence[float]) -> None: ...

    def add_many(self, rows: Iterable[tuple[int, Sequence[float]]]) -> int:
        count = 0
        for asset_id, vector in rows:
            self.add(asset_id, vector)
            count += 1
        return count

    @abstractmethod
    def remove(self, asset_id: int) -> None: ...

    @abstractmethod
    def search(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        candidate_ids: set[int] | None = None,
    ) -> list[tuple[int, float]]: ...

    @abstractmethod
    def count(self) -> int: ...

    def clear_index(self) -> None:  # noqa: B027 - намеренный no-op для бэкендов без индекса
        """Сбрасывает только построенный индекс.

        По умолчанию — no-op: бэкенд без отдельного индекса (brute-force)
        читает embeddings напрямую и перестраивать нечего. Embeddings при этом
        остаются источником истины (§7) и не удаляются.
        """

    def clear(self) -> None:
        self.clear_index()

    def clear_embeddings(self) -> None:
        self.catalog.conn.execute(
            "DELETE FROM embeddings WHERE model_id=? AND index_generation=?",
            (self.model_id, self.index_generation),
        )
        self.clear_index()

    # ------------------------------------------------------------------ status
    def status(self) -> IndexStatus:
        row = self.catalog.get_vector_state(self.backend, self.model_id, self.index_generation)
        if row is None:
            return IndexStatus(
                backend=self.backend,
                status="active",
                vector_count=self.count(),
                dim=self.dim,
                model_id=self.model_id,
                index_generation=self.index_generation,
            )
        return IndexStatus(
            backend=str(row["backend"]),
            status=str(row["status"]),
            vector_count=int(row["vector_count"]),
            dim=int(row["dim"]),
            model_id=str(row["model_id"]),
            index_generation=int(row["index_generation"]),
            reason=row["reason"],
            measured_p95_ms=row["measured_p95_ms"],
        )

    def record_status(
        self,
        *,
        status: str = "active",
        reason: str | None = None,
        measured_p95_ms: float | None = None,
        build_progress: float = 1.0,
    ) -> None:
        self.catalog.set_vector_state(
            self.backend,
            model_id=self.model_id,
            index_generation=self.index_generation,
            status=status,
            reason=reason,
            vector_count=self.count(),
            dim=self.dim,
            measured_p95_ms=measured_p95_ms,
            build_progress=build_progress,
        )

    def rebuild(self, rows: Iterable[tuple[int, Sequence[float]]] | None = None) -> int:
        self.clear_index()
        if rows is None:
            stored = self.catalog.get_embeddings(self.model_id, self.index_generation)
            rows = (
                (int(row["asset_id"]), unpack_vector(row["vec"], int(row["dim"]))) for row in stored
            )
        count = self.add_many(rows)
        self.record_status(status="active")
        return count

    def search_with_status(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        candidate_ids: set[int] | None = None,
    ) -> tuple[list[tuple[int, float]], IndexStatus]:
        results = self.search(query_vector, k=k, candidate_ids=candidate_ids)
        return results, self.status()


__all__ = ["VectorIndex", "dot", "normalize", "pack_vector", "unpack_vector"]
