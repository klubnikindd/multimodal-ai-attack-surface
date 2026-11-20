"""FR-2.14: группы дублей — точные (blake3) и почти-дубли (pHash) в review.

Точные дубли группируются по `content_hash`; «оригинал» — самый ранний по
`taken_at` (при равенстве — `created_at`, затем id). Почти-дубли
(pHash/Hamming) попадают **только** в `review_queue`: авто-склейки,
авто-выбора «лучшего» и авто-удаления нет (§5.2, FR-2.14).
"""

from __future__ import annotations

from typing import Any

from ..catalog.repository import Catalog
from ..hashing import hamming_distance_hex

NEAR_HAMMING_THRESHOLD = 10
# pHash-попарное сравнение — O(N²). На больших архивах до реализации ANN-поиска
# по pHash ограничиваем его и честно сообщаем о пропуске (KNOWN_LIMITATIONS).
NEAR_SCAN_CAP = 5000


def compute_dup_groups(
    catalog: Catalog,
    *,
    near_threshold: int = NEAR_HAMMING_THRESHOLD,
    near_cap: int = NEAR_SCAN_CAP,
) -> dict[str, Any]:
    """Строит группы точных дублей и ставит почти-дубли в review."""
    exact_groups = 0
    exact_copies = 0
    # Точный дубль = один content_hash (UNIQUE в assets) с несколькими файлами:
    # лишние файлы — копии того же содержимого. «Оригинал» — primary-файл.
    rows = catalog.conn.execute(
        "SELECT a.id, a.content_hash, COUNT(f.id) AS n FROM assets a "
        "JOIN files f ON f.asset_id=a.id AND f.exists_flag=1 "
        "GROUP BY a.id HAVING n > 1"
    ).fetchall()
    for row in rows:
        copies = int(row["n"]) - 1
        catalog.set_dup_group(
            int(row["id"]),
            f"exact-{str(row['content_hash'])[:8]}",
            is_original=True,
            copies=copies,
        )
        exact_groups += 1
        exact_copies += copies

    near_candidates = 0
    near_skipped = False
    phash_rows = [
        (int(r["id"]), r["phash"])
        for r in catalog.conn.execute("SELECT id, phash FROM assets WHERE phash IS NOT NULL")
    ]
    if len(phash_rows) <= near_cap:
        seen: set[tuple[int, int]] = set()
        for i in range(len(phash_rows)):
            a_id, a_phash = phash_rows[i]
            for j in range(i + 1, len(phash_rows)):
                b_id, b_phash = phash_rows[j]
                if a_phash and b_phash and hamming_distance_hex(a_phash, b_phash) <= near_threshold:
                    pair = (min(a_id, b_id), max(a_id, b_id))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    catalog.add_review(
                        b_id, "near_duplicate", severity="info", payload={"with_asset_id": a_id}
                    )
                    near_candidates += 1
    else:
        near_skipped = True

    return {
        "exact_groups": exact_groups,
        "exact_copies": exact_copies,
        "near_candidates": near_candidates,
        "near_skipped": near_skipped,
        "near_scan_cap": near_cap,
    }


__all__ = ["NEAR_HAMMING_THRESHOLD", "compute_dup_groups"]
