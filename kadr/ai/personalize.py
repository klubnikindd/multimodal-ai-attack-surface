"""FR-3.12: персонализация — обратная связь пользователя в ранжирование и приор сцен."""

from __future__ import annotations

from collections.abc import Sequence


def rerank_by_rating(
    hits: Sequence[tuple[int, float]],
    ratings: dict[int, float],
    *,
    boost: float = 0.10,
) -> list[tuple[int, float]]:
    """Повышает позиции результатов с высоким рейтингом пользователя (0..5).

    hits: [(asset_id, score)] — исходное ранжирование поиска.
    ratings: asset_id -> рейтинг пользователя (0..5) или вес метки.
    """
    scored = [(aid, score + ratings.get(aid, 0.0) * boost) for aid, score in hits]
    return sorted(scored, key=lambda p: p[1], reverse=True)


def scene_prior(scene_counts: dict[str, int], *, alpha: float = 1.0) -> dict[str, float]:
    """Сглаженный (Лаплас) приор сцен по пользовательским частотам.

    Используется для коррекции уверенности L1 в пользу частых сцен пользователя.
    """
    vocab = list(scene_counts)
    total = sum(scene_counts.values()) + alpha * len(vocab)
    if total <= 0:
        return {s: 1.0 / len(vocab) for s in vocab}
    return {s: (c + alpha) / total for s, c in scene_counts.items()}


__all__ = ["rerank_by_rating", "scene_prior"]
