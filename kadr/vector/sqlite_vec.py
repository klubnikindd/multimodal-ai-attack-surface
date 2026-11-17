"""Адаптер sqlite-vec (M0-бэкенд; brute-force, не ANN)."""

from __future__ import annotations

import os
import re
import struct
from collections.abc import Iterable, Sequence

from ..models import IndexStatus
from .base import VectorIndex, normalize, unpack_vector
from .brute_force import BruteForceVectorIndex

PINNED_VERSION_PREFIX = "0.1."


def _load_extension(conn) -> tuple[bool, str | None, str | None]:
    """Загружает sqlite-vec. Возвращает (ok, version, reason)."""
    try:
        import sqlite_vec  # type: ignore
    except Exception:
        sqlite_vec = None
    if sqlite_vec is not None:
        try:
            version = getattr(sqlite_vec, "__version__", "unknown")
            if not str(version).startswith(PINNED_VERSION_PREFIX) and version != "unknown":
                return (
                    False,
                    str(version),
                    f"sqlite-vec version {version} is not pinned {PINNED_VERSION_PREFIX}x",
                )
            sqlite_vec.load(conn)
            return True, str(version), None
        except Exception as exc:
            return (
                False,
                getattr(sqlite_vec, "__version__", "unknown"),
                f"sqlite_vec.load failed: {exc}",
            )
    extension_path = os.environ.get("KADR_SQLITE_VEC_PATH")
    if extension_path:
        try:
            conn.enable_load_extension(True)
            conn.load_extension(extension_path)
            return True, "external", None
        except Exception as exc:
            return False, None, f"load_extension({extension_path}) failed: {exc}"
    return False, None, "sqlite-vec extension is not installed; using exact brute-force fallback"


class SqliteVecIndex(VectorIndex):
    backend = "sqlite-vec"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.available, self.extension_version, self.reason = _load_extension(self.catalog.conn)
        self.table = self._table_name()
        self._quoted_table = '"' + self.table.replace('"', '""') + '"'
        self.distance_metric = "cosine"
        self._fallback = BruteForceVectorIndex(
            self.catalog,
            model_id=self.model_id,
            index_generation=self.index_generation,
            dim=self.dim,
        )
        if self.available:
            self._ensure_table()
        else:
            self.record_status(status="degraded", reason=self.reason)

    def _table_name(self) -> str:
        safe = "".join(ch if ch.isalnum() else "_" for ch in self.model_id)[:40]
        return f"vec_index_{safe}_{self.index_generation}"

    def _ensure_table(self) -> bool:
        if not self.available:
            return False
        row = self.catalog.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (self.table,)
        ).fetchone()
        if row is not None:
            sql_text = str(row["sql"] or "")
            existing_dim = re.search(r"FLOAT\[(\d+)\]", sql_text, re.I)
            if "distance_metric" not in sql_text or (
                existing_dim is not None and int(existing_dim.group(1)) != int(self.dim)
            ):
                self.catalog.conn.execute(f"DROP TABLE IF EXISTS {self._quoted_table}")
        try:
            self.catalog.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {self._quoted_table} USING vec0("
                f"asset_id INTEGER PRIMARY KEY, embedding FLOAT[{self.dim}] distance_metric=cosine)"
            )
            self.distance_metric = "cosine"
            return True
        except Exception as exc:
            # Версия без distance_metric: не притворяемся, что score косинусный.
            try:
                self.catalog.conn.execute(f"DROP TABLE IF EXISTS {self._quoted_table}")
            except Exception:
                pass
            self.available = False
            self.reason = f"vec0 cosine table failed ({exc}); using exact fallback"
            self.record_status(status="degraded", reason=self.reason)
            return False

    @staticmethod
    def _serialize(vector: Sequence[float]) -> bytes:
        try:
            import sqlite_vec  # type: ignore

            return sqlite_vec.serialize_float32(list(vector))
        except Exception:
            return struct.pack(f"<{len(vector)}f", *[float(x) for x in vector])

    def add(self, asset_id: int, vector: Sequence[float]) -> None:
        values = normalize(vector)
        self.dim = len(values)
        self.catalog.upsert_embedding(
            asset_id, self.model_id, values, index_generation=self.index_generation
        )
        if self.available:
            if not self._ensure_table():
                return
            try:
                self.catalog.conn.execute(
                    f"INSERT OR REPLACE INTO {self._quoted_table}(asset_id, embedding) VALUES (?, ?)",
                    (asset_id, self._serialize(values)),
                )
            except Exception as exc:
                self.available = False
                self.reason = f"vec0 insert failed: {exc}"
                self.record_status(status="degraded", reason=self.reason)
                return
        if self.available:
            self.record_status(status="active")
        self._fallback._cache = None

    def add_many(self, rows: Iterable[tuple[int, Sequence[float]]]) -> int:
        count = 0
        for asset_id, vector in rows:
            self.add(asset_id, vector)
            count += 1
        return count

    def remove(self, asset_id: int) -> None:
        self.catalog.conn.execute(
            "DELETE FROM embeddings WHERE asset_id=? AND model_id=? AND index_generation=?",
            (asset_id, self.model_id, self.index_generation),
        )
        if self.available:
            try:
                self.catalog.conn.execute(
                    f"DELETE FROM {self._quoted_table} WHERE asset_id=?", (asset_id,)
                )
            except Exception:
                pass
        self._fallback._cache = None
        self.record_status(
            status="active" if self.available else "degraded",
            reason=None if self.available else self.reason,
        )

    def search(
        self,
        query_vector: Sequence[float],
        k: int = 10,
        candidate_ids: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        if k <= 0:
            return []
        if self.available:
            try:
                payload = self._serialize(normalize(query_vector))
                fetch_k = k
                if candidate_ids is not None:
                    # KNN возвращает top-k глобально, поэтому кандидаты могут не
                    # попасть в top-k. Пере-запрашиваем с запасом (но с жёстким
                    # потолком, чтобы не раздувать KNN на миллионах векторов), а
                    # при нехватке результатов делаем точный реранк по кандидатам.
                    fetch_k = min(max(k * 20, 100), 10_000, max(self.count(), k))
                results = self._search_sql(payload, fetch_k, candidate_ids)
                if results is not None:
                    if candidate_ids is None or len(results) >= min(k, len(candidate_ids)):
                        return results[:k]
                    return self._fallback.search(query_vector, k=k, candidate_ids=candidate_ids)
            except Exception as exc:
                self.available = False
                self.reason = f"vec0 KNN failed: {exc}"
                self.record_status(status="degraded", reason=self.reason)
        # Видимая деградация: результат тот же, но статус не active.
        return self._fallback.search(query_vector, k=k, candidate_ids=candidate_ids)

    def _search_sql(
        self, payload: bytes, k: int, candidate_ids: set[int] | None
    ) -> list[tuple[int, float]] | None:
        sql = (
            f"SELECT asset_id, distance FROM {self._quoted_table} "
            "WHERE embedding MATCH ? AND k = ? LIMIT ?"
        )
        try:
            rows = self.catalog.conn.execute(sql, (payload, k, k)).fetchall()
        except Exception:
            # Другой синтаксис KNN в части версий 0.1.x.
            try:
                rows = self.catalog.conn.execute(
                    f"SELECT asset_id, distance FROM {self._quoted_table} WHERE embedding MATCH ? LIMIT ?",
                    (payload, k),
                ).fetchall()
            except Exception:
                return None
        results = []
        for row in rows:
            asset_id = int(row["asset_id"])
            if candidate_ids is not None and asset_id not in candidate_ids:
                continue
            # distance: cosine distance; в score переводим в "больше — лучше".
            results.append((asset_id, 1.0 - float(row["distance"])))
        results.sort(key=lambda pair: pair[1], reverse=True)
        return results

    def count(self) -> int:
        if self.available:
            try:
                row = self.catalog.conn.execute(
                    f"SELECT COUNT(*) AS n FROM {self._quoted_table}"
                ).fetchone()
                return int(row["n"])
            except Exception:
                pass
        return self.catalog.count_embeddings(self.model_id, self.index_generation)

    def status(self) -> IndexStatus:
        row = self.catalog.get_vector_state(self.backend, self.model_id, self.index_generation)
        status = "degraded" if not self.available else (str(row["status"]) if row else "active")
        reason = self.reason if not self.available else (row["reason"] if row else None)
        return IndexStatus(
            backend=self.backend,
            status=status,
            vector_count=self.count(),
            dim=self.dim,
            model_id=self.model_id,
            index_generation=self.index_generation,
            reason=reason,
            measured_p95_ms=row["measured_p95_ms"] if row else None,
        )

    def rebuild(self, rows: Iterable[tuple[int, Sequence[float]]] | None = None) -> int:
        if self.available:
            try:
                self.catalog.conn.execute(f"DROP TABLE IF EXISTS {self._quoted_table}")
            except Exception:
                pass
            self._ensure_table()
        if rows is None:
            stored = self.catalog.get_embeddings(self.model_id, self.index_generation)
            rows = (
                (int(row["asset_id"]), unpack_vector(row["vec"], int(row["dim"]))) for row in stored
            )
        count = self.add_many(rows)
        self.record_status(
            status="active" if self.available else "degraded",
            reason=None if self.available else self.reason,
        )
        return count


__all__ = ["PINNED_VERSION_PREFIX", "SqliteVecIndex"]
