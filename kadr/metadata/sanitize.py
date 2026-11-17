"""Санитизация недоверенного текста (метаданные, OCR, подписи моделей).

Правила из THREAT_MODEL T01/T04/T06: NFKC, удаление control/zero-width/bidi,
лимит длины, детект инъекций и PII. Метаданные никогда не попадают в
инструкционную часть промпта — это лишь первый слой.
"""

from __future__ import annotations

import re
import unicodedata

from ..constants import MAX_METADATA_TEXT

_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)", re.I),
    re.compile(r"(system|developer)\s*(prompt|message|note)\s*:", re.I),
    re.compile(r"<\s*/?\s*(system|assistant|developer|tool)\s*>", re.I),
    re.compile(r"\bASSISTANT\s*:", re.I),
    re.compile(r"\bSYSTEM\s+NOTE\s*:", re.I),
    re.compile(r"output\s+only\s*\{", re.I),
    re.compile(r"classify\s+this\s+(image|photo)\s+as", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
    re.compile(r"act\s+as\s+(an?\s+)?(admin|system|developer)", re.I),
    re.compile(r"\b(tool_calls?|function_calls?|system_prompt)\b", re.I),
    re.compile(r"\{\{.*?\}\}|\$\{.*?\}", re.S),
)
_ZERO_WIDTH = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)(?!\d)")
_CARD_CANDIDATE = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")
_PASSPORT = re.compile(r"\b(?:\d{2}\s?\d{2}\s?\d{6}|[A-Z]{2}\d{7})\b")
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def sanitize_text(text: object, *, limit: int = MAX_METADATA_TEXT) -> str:
    """NFKC + удаление невидимых/управляющих символов + обрезка."""
    if text is None:
        return ""
    raw = str(text)
    raw = unicodedata.normalize("NFKC", raw)
    raw = _ZERO_WIDTH.sub("", raw)
    raw = _CONTROL.sub("", raw)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    # Управляющие символы внутри строки не нужны, но переводы строк оставляем.
    raw = raw.strip()
    if len(raw) > limit:
        raw = raw[:limit]
    return raw


def detect_injection(text: object) -> tuple[bool, list[str]]:
    """Лёгкий детерминированный детектор prompt injection.

    Это не ML-сканер и не заменяет архитектурную защиту (metadata не идёт в
    инструкции, tools отсутствуют). Срабатывание → review inbox.
    """
    value = sanitize_text(text)
    if not value:
        return False, []
    hits: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(value)
        if match:
            hits.append(match.group(0)[:80])
    # base64-подобные длинные блоки — подозрительно для метаданных
    if re.search(r"(?:[A-Za-z0-9+/]{80,}={0,2})", value):
        hits.append("long-base64")
    return bool(hits), hits


def _luhn(number: str) -> bool:
    digits = [int(ch) for ch in number if ch.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def detect_pii(text: object) -> dict[str, list[str]]:
    """Детерминированные PII-детекторы для Lite-профиля (SEC-04)."""
    value = sanitize_text(text)
    result: dict[str, list[str]] = {}
    if not value:
        return result
    emails = _EMAIL.findall(value)
    if emails:
        result["email"] = emails[:20]
    # Карты проверяем первыми: иначе _PHONE поглотит 16-значный номер.
    cards = [match.group(0) for match in _CARD_CANDIDATE.finditer(value) if _luhn(match.group(0))]
    if cards:
        result["card"] = cards[:20]
    value_without_cards = _CARD_CANDIDATE.sub(
        lambda match: " " * len(match.group(0)) if _luhn(match.group(0)) else match.group(0),
        value,
    )
    phones = [m.group(0).strip() for m in _PHONE.finditer(value_without_cards)]
    if phones:
        result["phone"] = phones[:20]
    passports = _PASSPORT.findall(value)
    if passports:
        result["passport"] = passports[:20]
    ips = _IP.findall(value)
    if ips:
        result["ip"] = ips[:20]
    return result


def redact_pii(text: object) -> str:
    value = sanitize_text(text)
    for pattern, replacement in (
        (_EMAIL, "[email]"),
        (_CARD_CANDIDATE, "[card]"),
        (_PASSPORT, "[document]"),
        (_PHONE, "[phone]"),
        (_IP, "[ip]"),
    ):
        if pattern is _CARD_CANDIDATE:
            value = pattern.sub(lambda m: "[card]" if _luhn(m.group(0)) else m.group(0), value)
        else:
            value = pattern.sub(replacement, value)
    return value


def has_sensitive_text(text: object) -> bool:
    return bool(detect_pii(text)) or bool(detect_injection(text)[0])
