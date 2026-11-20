"""Security-корпус (SEC-04/T04, SEC-06/T06): итерация по `security/corpus/`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kadr.errors import PathSafetyError
from kadr.metadata.sanitize import detect_pii
from kadr.security.paths import normalize_rel_path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "security" / "corpus"


def _load(name: str):
    return json.loads((CORPUS / name).read_text(encoding="utf-8"))


def test_path_traversal_corpus_rejected():
    payloads = _load("path_traversal_payloads.json")["payloads"]
    assert payloads
    for payload in payloads:
        with pytest.raises(PathSafetyError):
            normalize_rel_path(payload)


def test_pii_corpus_detected():
    payloads = _load("pii_payloads.json")["payloads"]
    assert payloads
    missed = []
    for entry in payloads:
        detected = detect_pii(entry["text"])
        if entry["category"] not in detected:
            missed.append(entry["category"])
    assert missed == [], f"не детектированы PII-категории: {missed}"
