"""ANN-бэкенд (usearch/HNSW) с явной деградацией до точного поиска."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from typing import Any

from ..models import IndexStatus
from .base import VectorIndex, normalize, unpack_vector
from .brute_force import BruteForceVectorIndex

logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000
_RECOVERY_COOLDOWN_SECONDS = 30.0
# Тюнинг HNSW по спайку E (2026-11-20): при дефолтах recall@10 был 0.31–0.61,
# что не проходит NFR-18 (≥0.90). conn=32 / ef_add=200 / ef_search=256 дают
# recall@10 = 0.98 при ~2 мс на 20K векторов (см. docs/BENCHMARKS.md).
_ANN_CONNECTIVITY = 32
_ANN_EXPANSION_ADD = 200
_ANN_EXPANSION_SEARCH = 256


def _add_in_batches(index, keys: Sequence[int], vectors: Sequence[Sequence[float]]) -> None:
    """Добавляет векторы пачками: большие наборы не должны упираться в лимиты usearch."""
    import numpy as np

    if not keys:
        return
    keys_array = np.asarray(list(keys), dtype=np.int64)
    vectors_array = np.asarray(list(vectors), dtype=np.float32)
    for start in range(0, len(keys), _BATCH_SIZE):
        index.add(keys_array[start : start + _BATCH_SIZE], vectors_array[start : start + _BATCH_SIZE])


class AnnVectorIndex(VectorIndex):
    backend = "ann"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fallback = BruteForceVectorIndex(
            self.catalog,
            model_id=self.model_id,
            index_generation=self.index_generation,
            dim=self.dim,
        )
        self._index: Any = None
        self._index_cls: Any = None
        self.available = False
        self._failed = False
        self._last_recovery_attempt = 0.0
        self.reason: str | None = "usearch is not installed; using exact brute-force fallback"
        try:
            from usearch.index import Index
        except Exception as exc:
            self.reason = f"usearch unavailable: {exc}"
            self.record_status(status="degraded", reason=self.reason)
            return
        self._index_cls = Index
        self.available = True
        self.reason = None
        # Тяжёлое перестроение выполняется только при явно установленном
        # extra `ann`; Lite его не импортирует. Любой сбой ниже видимо
        # переводит бэкенд в degraded с exact-fallback.
        try:
            self._rebuild_from_db()
        except Exception as exc:
            logger.warning("usearch initial rebuild failed: %s", exc, exc_info=True)
            self._failed = True
            self.reason = f"usearch rebuild failed: {exc}"
            self.record_status(status="degraded", reason=self.reason)

    def _rebuild_from_db(self) -> None:
        rows = self.catalog.get_embeddings(self.model_id, self.index_generation)
        if not rows:
            return
        dims = {int(row["dim"]) for row in rows}
        if len(dims) != 1:
            raise ValueError(f"embedding dim mismatch in catalog: {sorted(dims)}")
        self.dim = dims.pop()
        # PK embeddings гарантирует уникальность asset_id, но дедупликация
        # защищает от нестандартной миграции/ручной правки каталога.
        by_asset: dict[int, list[float]] = {}
        for row in rows:
            by_asset[int(row["asset_id"])] = unpack_vector(row["vec"], self.dim)
        if not by_asset:
            return
        index = self._index_cls(ndim=self.dim, metric="cos", connectivity=_ANN_CONNECTIVITY, expansion_add=_ANN_EXPANSION_ADD, expansion_search=_ANN_EXPANSION_SEARCH)
        keys = list(by_asset)
        vectors = [by_asset[key] for key in keys]
        _add_in_batches(index, keys, vectors)
        self._index = index
        self._failed = False
        self.record_status(status="active", reason="usearch HNSW")

    def _recover(self) -> bool:
        """Пробует пересобрать индекс после транзиентного сбоя (с cooldown)."""
        if not self.available:
            return False
        now = time.monotonic()
        if now - self._last_recovery_attempt < _RECOVERY_COOLDOWN_SECONDS:
            return False
        self._last_recovery_attempt = now
        try:
            self._rebuild_from_db()
        except Exception as exc:
            logger.warning("usearch recovery failed: %s", exc, exc_info=True)
            self.reason = f"usearch rebuild failed: {exc}"
            self.record_status(status="degraded", reason=self.reason)
            return False
        self._failed = False
        self.reason = None
        return True

    def _ensure(self) -> bool:
        if not self.available:
            return False
        if self._index is None:
            self._index = self._index_cls(ndim=self.dim, metric="cos", connectivity=_ANN_CONNECTIVITY, expansion_add=_ANN_EXPANSION_ADD, expansion_search=_ANN_EXPANSION_SEARCH)
        return True

    def _index_size(self) -> int:
        if self._index is None:
            return 0
        try:
            return len(self._index)
        except Exception:
            return 0

    def _mark_failed(self, operation: str, exc: Exception) -> None:
        # Не выключаем бэкенд навсегда: один транзиентный сбой не должен
        # требовать перезапуска процесса. Восстановление — по cooldown.
        logger.warning("usearch %s failed: %s", operation, exc, exc_info=True)
        self._failed = True
        self._index = None
        self.reason = f"usearch {operation} failed: {exc}"
        self.record_status(status="degraded", reason=self.reason)

    def add(self, asset_id: int, vector: Sequence[float]) -> None:
        values = normalize(vector)
        if self._index is not None and self._index_size() and len(values) != self.dim:
            raise ValueError(
                f"embedding dim mismatch: {len(values)} != {self.dim} (index already built)"
            )
        self.dim = len(values)
        self.catalog.upsert_embedding(
            asset_id, self.model_id, values, index_generation=self.index_generation
        )
        if self._failed:
            # Не собираем частичный индекс: пока восстановление не удалось,
            # поиск идёт через exact-fallback по embeddings в каталоге.
            self._recover()
            self._fallback._cache = None
            return
        if self._ensure():
            try:
                import numpy as np

                # usearch требует ndarray; статус обновляется пачкой в add_many().
                self._index.add(
                    np.asarray([asset_id], dtype=np.int64),
                    np.asarray([values], dtype=np.float32),
                )
            except Exception as exc:
                self._mark_failed("add", exc)
        self._fallback._cache = None

    def add_many(self, rows: Iterable[tuple[int, Sequence[float]]]) -> int:
        count = 0
        for asset_id, vector in rows:
            self.add(asset_id, vector)
            count += 1
        self.record_status(
            status="active" if self.available else "degraded",
            reason=None if self.available else self.reason,
        )
        return count

    def remove(self, asset_id: int) -> None:
        self.catalog.conn.execute(
            "DELETE FROM embeddings WHERE asset_id=? AND model_id=? AND index_generation=?",
            (asset_id, self.model_id, self.index_generation),
        )
        if self._index is not None:
            try:
                self._index.remove(asset_id)
            except Exception as exc:
                logger.warning("usearch remove failed: %s", exc, exc_info=True)
        self._fallback._cache = None

    def search(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        candidate_ids: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        if k <= 0:
            return []
        if self._failed:
            self._recover()
        if self.available and self._index is not None:
            try:
                import numpy as np

                fetch_k = k if candidate_ids is None else max(k * 10, 100)
                query = np.asarray(normalize(query_vector), dtype=np.float32)
                matches = self._index.search(query, fetch_k)
                result = [(int(match.key), 1.0 - float(match.distance)) for match in matches]
                if candidate_ids is not None:
                    result = [
                        (asset_id, score) for asset_id, score in result if asset_id in candidate_ids
                    ]
                if result or candidate_ids is None:
                    return result[:k]
            except Exception as exc:
                self._mark_failed("search", exc)
        return self._fallback.search(query_vector, k=k, candidate_ids=candidate_ids)

    def count(self) -> int:
        size = self._index_size()
        if size:
            return size
        return self.catalog.count_embeddings(self.model_id, self.index_generation)

    def status(self) -> IndexStatus:
        row = self.catalog.get_vector_state(self.backend, self.model_id, self.index_generation)
        degraded = self._failed or not self.available
        status = "degraded" if degraded else "active"
        return IndexStatus(
            backend=self.backend,
            status=status,
            vector_count=self.count(),
            dim=self.dim,
            model_id=self.model_id,
            index_generation=self.index_generation,
            reason=self.reason if degraded else (row["reason"] if row else "usearch HNSW"),
            measured_p95_ms=row["measured_p95_ms"] if row else None,
        )

    def clear_index(self) -> None:
        self._index = None
        self._failed = False
        if self.available:
            self._index = self._index_cls(ndim=self.dim, metric="cos", connectivity=16)

    def rebuild(self, rows: Iterable[tuple[int, Sequence[float]]] | None = None) -> int:
        if rows is None:
            self._rebuild_from_db()
            return self.count()
        self.clear_index()
        return self.add_many(rows)


__all__ = ["AnnVectorIndex"]
