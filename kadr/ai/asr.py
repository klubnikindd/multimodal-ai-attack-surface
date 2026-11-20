"""ASR (FR-3.7, P1): faster-whisper, локально, с тайм-кодами.

Зависимость опциональная: без faster-whisper/весов `available=False` и
`transcribe` поднимает `RuntimeError` с честной причиной — пайплайн не
падает, а помечает поле пустым (как tesseract/ffprobe).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_MODEL = os.environ.get("KADR_ASR_MODEL", "small")
_DEFAULT_DEVICE = "cuda" if os.environ.get("KADR_ASR_DEVICE") else "cpu"
_COMPUTE_TYPE = os.environ.get("KADR_ASR_COMPUTE", "int8")


@dataclass(slots=True)
class ASRSegment:
    start: float
    end: float
    text: str


@dataclass(slots=True)
class ASRResult:
    text: str
    language: str
    segments: list[ASRSegment] = field(default_factory=list)
    available: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "language": self.language,
            "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in self.segments],
            "available": self.available,
        }


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:  # pragma: no cover - ctranslate2 отсутствует
        return False


class WhisperASR:
    """Ленивая обёртка faster-whisper; модель грузится один раз на инстанс."""

    def __init__(
        self,
        model_size: str = _DEFAULT_MODEL,
        *,
        device: str = _DEFAULT_DEVICE,
        compute_type: str = _COMPUTE_TYPE,
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self.available = is_available()

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        import faster_whisper

        # download_root=None → используем HF-кэш; веса small/turbo — MIT (faster-whisper)
        # и SYSTEM-карточка Whisper (Apache-2.0/MIT в зависимости от модели).
        self._model = faster_whisper.WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )
        return self._model

    def transcribe(self, path: str | Path, language: str | None = None) -> ASRResult:
        if not self.available:
            raise RuntimeError("ASR недоступен: faster-whisper не установлен")
        model = self._load()
        segments_iter, info = model.transcribe(str(path), language=language, beam_size=1)
        segments = [
            ASRSegment(start=float(seg.start), end=float(seg.end), text=str(seg.text).strip())
            for seg in segments_iter
        ]
        text = " ".join(seg.text for seg in segments).strip()
        return ASRResult(
            text=text,
            language=str(getattr(info, "language", "") or ""),
            segments=segments,
            available=True,
        )


__all__ = ["ASRResult", "ASRSegment", "WhisperASR", "is_available"]
