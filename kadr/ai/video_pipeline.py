"""Видео-конвейер (FR-3.6 + FR-3.7): сцены/ключевые кадры + ASR.

Опциональные зависимости: scenedetect (FR-3.6), faster-whisper (FR-3.7).
Без них — сцены/ASR помечаются unavailable, пайплайн не падает.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from ..catalog.repository import Catalog
from ..video.scenes import detect_scenes, extract_keyframes


def run_video_pipeline(catalog: Catalog, asset_id: int, path: str | Path, *, run_asr: bool = True) -> dict[str, Any]:
    """Для видео: scene-cut + ключевые кадры + ASR. Пишет AI-outputs."""
    result: dict[str, Any] = {"scenes": 0, "keyframes": 0, "asr_chars": 0, "asr_available": False}

    # FR-3.6: сцены + ключевые кадры
    try:
        scenes = detect_scenes(path)
        result["scenes"] = len(scenes)
        with tempfile.TemporaryDirectory() as td:
            frames = extract_keyframes(path, Path(td), k=12, width=320)
            result["keyframes"] = len(frames)
        catalog.set_ai_output(
            asset_id, "video_scenes", "scenedetect-v1",
            {"scenes": result["scenes"], "keyframes": result["keyframes"]},
            source="model",
        )
    except Exception as exc:  # noqa: BLE001 - опционально, не роняем пайплайн
        result["scenes_error"] = str(exc)[:200]

    # FR-3.7: ASR (faster-whisper, опционально)
    if run_asr:
        try:
            from .asr import WhisperASR

            asr = WhisperASR()
            if asr.available:
                r = asr.transcribe(path)
                result["asr_chars"] = len(r.text)
                result["asr_available"] = True
                catalog.set_ai_output(
                    asset_id, "asr", "faster-whisper",
                    {"text": r.text, "language": r.language, "segments": [s.to_dict() if hasattr(s, "to_dict") else str(s) for s in r.segments]},
                    source="model",
                )
                if r.text:
                    catalog.fts_upsert(asset_id, caption="", ocr_text="", asr_text=r.text, tags="")
        except Exception as exc:  # noqa: BLE001
            result["asr_error"] = str(exc)[:200]

    return result


__all__ = ["run_video_pipeline"]
