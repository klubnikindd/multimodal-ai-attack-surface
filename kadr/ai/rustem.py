"""Русский стеммер Портера (Snowball Russian) для FTS-морфологии (FR-5.2).

Реализация канонического алгоритма snowball «russian»: RV/R2-регионы, снятие
перфективного герундия/возвратности/прилагательного/причастия/глагола/существи-
тельного, деривационных суффиксов и «нн». Не ML и не словарь — детерминированно.

Используется в FTS-индексе: при индексации добавляем стемы, при поиске — стемим
запрос, чтобы «кот» находил «кота», «котов».
"""

from __future__ import annotations

import re

_VOWELS = "аеиоуыэюя"

_PERFECTIVE_GERUND = re.compile(r"(в|вши|вшись)$")
_REFLEXIVE = re.compile(r"(ся|сь)$")
_ADJECTIVE = re.compile(
    r"(ее|ие|ые|ое|ими|ыми|ей|ий|ый|ой|ем|им|ым|ом|его|ого|ему|ому|их|ых|ую|юю|ая|яя|ою|ею)$"
)
_PARTICIPLE = re.compile(r"(ем|нн|вш|ющ|щ|ивш|ывш|ующ)$")
_VERB = re.compile(
    r"(ила|ыла|ена|ейте|уйте|ите|или|ыли|ей|уй|ил|ыл|им|ым|ен|ило|ыло|ено|ят|ует|уют|ит|ыт|ены|ить|ыть|ишь|ую|ю)$"
)
_NOUN = re.compile(
    r"(а|ев|ов|ие|ье|е|иями|ями|ами|еи|ии|и|ией|ей|ой|ий|й|иям|ям|ием|ем|ам|ом|о|у|ах|иях|ях|ы|ь|ию|ью|ю|ия|ья|я)$"
)
_SUPERLATIVE = re.compile(r"(ейш|ейше)$")
_I = re.compile(r"и$")
_NN = re.compile(r"нн$")
_SOFT_SIGN = re.compile(r"ь$")
_DERIVATIONAL = re.compile(r"(ост|ость)$")


def _region(word: str) -> tuple[int, int]:
    """Возвращает (rv_start, r2_start): RV — после первой гласной; R2 — после RV,
    за которым следует согласная-гласная... упрощённо: R2 = после 2-й гласной."""
    rv = 0
    for index, char in enumerate(word):
        if char in _VOWELS:
            rv = index + 1
            break
    # R2: после второй гласной (согласная-гласная пара после RV).
    r2 = len(word)
    if rv < len(word):
        for index in range(rv, len(word)):
            if word[index] in _VOWELS:
                r2 = index + 1
                break
    return rv, r2


def _replace_suffix(word: str, pattern: re.Pattern[str], *, region: int) -> str:
    match = pattern.search(word)
    if match and match.start() >= region:
        return word[: match.start()]
    return word


def stem_word(word: str) -> str:
    """Стем одного русского слова (Snowball Russian)."""
    value = word.lower().replace("ё", "е")
    if not value or len(value) < 3:
        return value
    rv, r2 = _region(value)
    if rv == 0:
        return value
    # Шаг 1: перфективный герундий, иначе возвратность; затем прилаг./прич./глаг./сущ.
    if _PERFECTIVE_GERUND.search(value[rv:]):
        value = _replace_suffix(value, _PERFECTIVE_GERUND, region=rv)
    elif _REFLEXIVE.search(value[rv:]):
        value = _replace_suffix(value, _REFLEXIVE, region=rv)
    for pattern in (_ADJECTIVE, _PARTICIPLE, _VERB, _NOUN):
        candidate = _replace_suffix(value, pattern, region=rv)
        if candidate != value:
            value = candidate
            break
    # Шаг 2: «и»
    value = _replace_suffix(value, _I, region=rv)
    # Шаг 3: деривационные в R2
    value = _replace_suffix(value, _DERIVATIONAL, region=r2)
    # Шаг 4: «нн»→«н», суперлатив, мягкий знак в RV
    if _NN.search(value[rv:]):
        value = _replace_suffix(value, _NN, region=rv) + "н"
    else:
        value = _replace_suffix(value, _SUPERLATIVE, region=rv)
        value = _replace_suffix(value, _SOFT_SIGN, region=rv)
    return value


_TOKEN = re.compile(r"[а-яё]{3,}", re.IGNORECASE)


def stem_text(text: str) -> str:
    """Стемит русские токены текста, возвращает строку стемов."""
    return " ".join(stem_word(match.group(0)) for match in _TOKEN.finditer(text))


__all__ = ["stem_text", "stem_word"]
