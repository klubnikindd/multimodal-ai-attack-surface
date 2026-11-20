#!/usr/bin/env python3
# ruff: noqa: E402, I001 - импорты после sys.path-бутстрапа (запуск без установки пакета)
"""Спайк E (ТЗ v3.1 §13.1): масштаб векторного поиска 10K–500K.

Мерит на синтетических нормализованных векторах для каждого бэкенда:
  * build_seconds  — время сборки индекса (add_many);
  * db_bytes       — размер каталога после сборки;
  * p95_ms         — латентность поиска (k=10) по Q запросам;
  * recall@10      — против точного перебора (numpy ground truth).

Использование:
  python tests/scale/bench_scale.py --vectors 50000 --backend sqlite-vec
  python tests/scale/bench_scale.py --matrix --queries 20
  python tests/scale/bench_scale.py --vectors 300 --dim 16 --backend sqlite-vec --out /tmp/x.json

Порог переключения бэкендов (factory.auto_backend) и NFR-18 проверяются
владельцем по этим же данным; строки для BENCHMARKS.md печатает --markdown.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # noqa: E402 - kadr импортируется после sys.path
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
from kadr.catalog.repository import Catalog  # noqa: E402
from kadr.vector.ann_usearch import AnnVectorIndex  # noqa: E402
from kadr.vector.brute_force import BruteForceVectorIndex  # noqa: E402
from kadr.vector.hybrid import HybridVectorIndex  # noqa: E402
from kadr.vector.sqlite_vec import SqliteVecIndex  # noqa: E402

BACKENDS = {
    "sqlite-vec": SqliteVecIndex,
    "ann": AnnVectorIndex,
    "hybrid": HybridVectorIndex,
    "brute-force": BruteForceVectorIndex,
}
SIZES = (10_000, 50_000, 100_000, 200_000, 500_000)
DEFAULT_DIM = 128
K = 10


def _seed(catalog: Catalog, n: int) -> None:
    """Быстрая вставка ассетов одним executemany (это не предмет замера)."""
    now = datetime.now(timezone.utc).isoformat()
    rows = ((f"scale-{i}", now, now) for i in range(n))
    with catalog.conn:
        catalog.conn.executemany(
            "INSERT INTO assets(content_hash, kind, bytes, created_at, updated_at) "
            "VALUES (?, 'photo', 1, ?, ?)",
            rows,
        )


def run_bench(
    *,
    vectors: int,
    dim: int = DEFAULT_DIM,
    backend: str,
    queries: int = 20,
    seed: int = 42,
) -> dict:
    if backend not in BACKENDS:
        raise SystemExit(f"неизвестный backend {backend!r}: {sorted(BACKENDS)}")
    rng = np.random.default_rng(seed)
    matrix = rng.standard_normal((vectors, dim), dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix /= np.where(norms == 0, 1.0, norms)
    queries_matrix = rng.standard_normal((queries, dim), dtype=np.float32)
    queries_matrix /= np.linalg.norm(queries_matrix, axis=1, keepdims=True)

    with tempfile.TemporaryDirectory() as tmp:
        catalog = Catalog(Path(tmp) / "catalog.db")
        try:
            _seed(catalog, vectors)

            cls = BACKENDS[backend]
            index = cls(catalog, model_id="scale-bench", dim=dim)
            t0 = time.perf_counter()
            index.add_many((i + 1, vector.tolist()) for i, vector in enumerate(matrix))
            build_seconds = time.perf_counter() - t0

            # Ground truth: точный перебор (numpy); recall@10 — против него.
            sims = matrix @ queries_matrix.T  # N x Q
            truth = np.argsort(-sims, axis=0)[:K, :].T + 1  # Q x K, 1-based ids

            latencies_ms: list[float] = []
            hits = 0
            for qi in range(queries):
                t1 = time.perf_counter()
                results = index.search(queries_matrix[qi].tolist(), k=K)
                dt = (time.perf_counter() - t1) * 1000.0
                latencies_ms.append(dt)
                got = {asset_id for asset_id, _ in results}
                hits += len(got & set(truth[qi].tolist()))
            p95_ms = statistics.quantiles(latencies_ms, n=20)[18]
            recall_at_10 = hits / (queries * K)
            index.record_status(measured_p95_ms=round(p95_ms, 2))
            status = index.status()

            payload = {
                "device": os.environ.get("KADR_BENCH_DEVICE", "local"),
                "os": sys.platform,
                "cores": os.cpu_count(),
                "backend": backend,
                "vectors": vectors,
                "dim": dim,
                "queries": queries,
                "k": K,
                "build_seconds": round(build_seconds, 3),
                "db_bytes": catalog.path.stat().st_size,
                "p95_ms": round(p95_ms, 2),
                "mean_ms": round(statistics.fmean(latencies_ms), 2),
                "recall_at_10": round(recall_at_10, 4),
                "index_status": status.status,
                "index_reason": status.reason,
            }
        finally:
            catalog.close()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Спайк E: масштаб векторного поиска")
    parser.add_argument("--vectors", type=int, default=50_000)
    parser.add_argument("--dim", type=int, default=DEFAULT_DIM)
    parser.add_argument("--backend", default="sqlite-vec")
    parser.add_argument("--queries", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--matrix", action="store_true", help="все размеры и бэкенды")
    parser.add_argument("--out", default=None)
    parser.add_argument("--markdown", action="store_true", help="строки для BENCHMARKS.md")
    args = parser.parse_args(argv)

    runs = []
    if args.matrix:
        for size in SIZES:
            for backend in ("sqlite-vec", "ann"):
                if size >= 200_000 and backend == "sqlite-vec":
                    continue  # заведомо вне комфортной зоны; мерится на 100K
                print(f".. {backend} @ {size}", file=sys.stderr)
                runs.append(
                    run_bench(
                        vectors=size,
                        dim=args.dim,
                        backend=backend,
                        queries=args.queries,
                        seed=args.seed,
                    )
                )
    else:
        runs.append(
            run_bench(
                vectors=args.vectors,
                dim=args.dim,
                backend=args.backend,
                queries=args.queries,
                seed=args.seed,
            )
        )

    if args.markdown:
        for run in runs:
            print(
                "| {vectors} | {backend} | {p95_ms} | {recall_at_10} | {db_bytes} | "
                "{build_seconds} | {os}/{cores}c |".format(**run)
            )
    if args.out:
        Path(args.out).write_text(
            json.dumps(runs if len(runs) > 1 else runs[0], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        print(json.dumps(runs if len(runs) > 1 else runs[0], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
