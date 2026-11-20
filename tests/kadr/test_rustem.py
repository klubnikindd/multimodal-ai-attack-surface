"""FR-5.2: русская морфология FTS — стеммер Портера и поиск по словоформам."""

from __future__ import annotations

from kadr.ai.rustem import stem_text, stem_word


def test_canonical_noun_and_adjective_stems():
    pairs = {
        "машина": "машин",
        "кота": "кот",
        "котов": "кот",
        "красивый": "красив",
        "красивая": "красив",
        "быстрый": "быстр",
        "домами": "дом",
        "окнами": "окн",
        "столы": "стол",
        "закаты": "закат",
        "пляжа": "пляж",
    }
    for word, expected in pairs.items():
        assert stem_word(word) == expected, f"{word}: {stem_word(word)} != {expected}"


def test_stem_text_extracts_only_russian_tokens():
    assert stem_text("Коты и кота") == "кот кот"
    assert "http://example.com" not in stem_text("ссылка http://example.com кот")


def test_fts_search_matches_inflected_forms(catalog):
    from kadr.search import _fts_query

    catalog.fts_upsert(1, tags="кот", caption="")
    catalog.fts_upsert(2, tags="коты", caption="")
    catalog.fts_upsert(3, tags="собака", caption="")
    # «кота» → стем «кот»; индекс хранит стемы «кот» для 1 и 2.
    assert set(catalog.fts_search(_fts_query("кота"))) == {1, 2}
