"""SEC-01/T01: корпус prompt-injection в метаданных — ASR детерминированного слоя 0%."""

from __future__ import annotations

import json
from pathlib import Path

from kadr.metadata.sanitize import detect_injection, sanitize_text

REPO_ROOT = Path(__file__).resolve().parents[2]


def _corpus() -> list[dict]:
    path = REPO_ROOT / "security" / "corpus" / "injection_payloads.json"
    return json.loads(path.read_text(encoding="utf-8"))["payloads"]


def test_every_injection_payload_is_flagged():
    payloads = _corpus()
    assert payloads
    missed = [p["id"] for p in payloads if not detect_injection(p["text"])[0]]
    assert missed == [], f"пропущенные инъекции (ASR>0): {missed}"


def test_zero_width_and_bidi_are_stripped():
    cleaned = sanitize_text("a\u200bb\u202ec\u202dd\u2060e")
    assert "\u200b" not in cleaned
    assert "\u202e" not in cleaned
    assert "\u2060" not in cleaned
    # NFKC нормализует совместимые формы
    assert sanitize_text("\u2160") == "I"


def test_control_chars_are_removed():
    assert sanitize_text("a\x00b\x1fc") == "abc"
