"""FR-3.7/ASR (P1): обёртка faster-whisper — честная деградация без зависимостей."""

from __future__ import annotations

import pytest

from kadr.ai.asr import ASRResult, ASRSegment, WhisperASR, is_available


def test_asr_result_serialization():
    result = ASRResult(
        text="привет",
        language="ru",
        segments=[ASRSegment(start=0.0, end=1.2, text="привет")],
    )
    payload = result.to_dict()
    assert payload["text"] == "привет"
    assert payload["segments"][0]["end"] == 1.2


def test_asr_unavailable_raises_cleanly_when_no_dependency(monkeypatch):
    if is_available():
        pytest.skip("faster-whisper установлен — тест деградации неприменим")
    asr = WhisperASR("small")
    assert asr.available is False
    with pytest.raises(RuntimeError, match="недоступен"):
        asr.transcribe("nonexistent.wav")
