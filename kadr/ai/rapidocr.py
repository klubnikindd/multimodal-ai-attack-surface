"""RapidOCR (PP-OCRv4 ONNX) — общий движок для in-process и sandbox-воркера."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)
_ENGINE: Any = None
_FAILED = False


def model_dir() -> Path | None:
    import os

    env = os.environ.get("KADR_RAPIDOCR_DIR")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parents[2] / ".models-test" / "rapidocr")
    for candidate in candidates:
        if (candidate / "ch_PP-OCRv4_det_infer.onnx").is_file() and (
            candidate / "ch_PP-OCRv4_rec_infer.onnx"
        ).is_file():
            return candidate
    return None


def engine() -> Any:
    global _ENGINE, _FAILED
    if _ENGINE is not None:
        return _ENGINE
    if _FAILED:
        return None
    d = model_dir()
    if d is None:
        _FAILED = True
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR(
            det_model_path=str(d / "ch_PP-OCRv4_det_infer.onnx"),
            cls_model_path=str(d / "ch_ppocr_mobile_v2.0_cls_infer.onnx"),
            rec_model_path=str(d / "ch_PP-OCRv4_rec_infer.onnx"),
            rec_keys_path=str(d / "ppocr_keys_v1.txt"),
        )
        return _ENGINE
    except Exception as exc:
        _FAILED = True
        _logger.warning("rapidocr init failed: %s", exc)
        return None


def run(path: str | Path) -> dict[str, Any]:
    """OCR через RapidOCR. Возвращает {'available','text','engine','error'}."""
    e = engine()
    if e is None:
        return {"available": False, "text": "", "engine": "none", "error": "rapidocr unavailable"}
    try:
        result, _elapse = e(str(path))
        texts = [str(item[1]) for item in result] if result else []
        text = "\n".join(t for t in texts if t)
        return {"available": True, "text": text, "engine": "rapidocr", "error": None}
    except Exception as exc:
        return {"available": False, "text": "", "engine": "rapidocr", "error": str(exc)}


__all__ = ["engine", "model_dir", "run"]
