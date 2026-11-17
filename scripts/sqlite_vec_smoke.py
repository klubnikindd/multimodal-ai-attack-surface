#!/usr/bin/env python3
"""Smoke-тест sqlite-vec: загрузка, vec0, KNN с k = ?.

По умолчанию падает, если расширение недоступно: это гейт против «тихой»
деградации до FTS (ADR-0004, §12.3). Для локальной проверки без расширения
используйте --allow-fallback (тогда статус degraded считается ожидаемым).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kadr.catalog.repository import Catalog  # noqa: E402
from kadr.models import MediaMetadata  # noqa: E402
from kadr.vector.sqlite_vec import SqliteVecIndex  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-fallback", action="store_true")
    args = parser.parse_args()
    payload: dict[str, object] = {"available": False, "status": None, "results": []}
    with tempfile.TemporaryDirectory() as tmp:
        catalog = Catalog(Path(tmp) / "catalog.db")
        try:
            for index in range(1, 4):
                catalog.upsert_asset(f"hash-{index}", MediaMetadata(kind="photo"), size_bytes=1)
            index = SqliteVecIndex(catalog, model_id="smoke", dim=4)
            index.add(1, [1, 0, 0, 0])
            index.add(2, [0, 1, 0, 0])
            results = index.search([1, 0, 0, 0], k=1)
            payload = {
                "available": index.available,
                "status": index.status().to_dict(),
                "results": results,
            }
        except Exception as exc:
            # Не глотаем неожиданные ошибки: без traceback падение скрипта
            # неотличимо от штатной недоступности расширения.
            import traceback

            payload["error"] = f"{type(exc).__name__}: {exc}"
            payload["traceback"] = traceback.format_exc()
        finally:
            catalog.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    status = payload.get("status") or {}
    if payload.get("error"):
        print(f"sqlite-vec smoke failed: {payload['error']}", file=sys.stderr)
        return 1
    results = payload.get("results") or []
    if not results:
        print(
            "search returned no results "
            f"(status={status.get('status')!r}, reason={status.get('reason')!r})",
            file=sys.stderr,
        )
        return 1
    if not (payload.get("available") or args.allow_fallback):
        print("sqlite-vec unavailable and --allow-fallback not set", file=sys.stderr)
        return 1
    return 0 if results[0][0] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
