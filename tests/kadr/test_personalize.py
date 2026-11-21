"""FR-3.12: персонализация (реранкинг + приор сцен)."""

from kadr.ai.personalize import rerank_by_rating, scene_prior


def test_rerank_by_rating() -> None:
    hits = [(1, 0.9), (2, 0.8), (3, 0.7)]
    ratings = {3: 5.0, 1: 1.0}
    out = rerank_by_rating(hits, ratings, boost=0.1)
    # asset 3 (rating 5) -> 0.7 + 0.5 = 1.2; asset 1 -> 0.9 + 0.1 = 1.0; asset 2 -> 0.8
    assert out[0][0] == 3
    assert out[1][0] == 1
    assert out[2][0] == 2


def test_scene_prior_sums_to_one() -> None:
    counts = {"landscape": 8, "portrait": 2}
    prior = scene_prior(counts, alpha=1.0)
    assert abs(sum(prior.values()) - 1.0) < 1e-6
    assert prior["landscape"] > prior["portrait"]
