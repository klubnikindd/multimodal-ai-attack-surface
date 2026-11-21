"""AI-lite: OCR, media_class/scene_type, zero-shot теги, эмбеддинги."""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..catalog.repository import Catalog
from ..constants import DEFAULT_OCR_LANGUAGE, OCR_LANGUAGE_PATTERN
from ..errors import PathSafetyError
from ..metadata.sanitize import detect_injection, detect_pii, sanitize_text
from ..security.paths import safe_join
from ..workers.client import run_ocr_sandboxed
from .concepts import zero_shot_tags
from .duplicates import compute_dup_groups
from .cascade import classify_scene
from .embeddings import ConceptHashingEmbedding, embed_asset, get_embedding_backend

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OcrResult:
    text: str = ""
    engine: str = "none"
    available: bool = False
    error: str | None = None


def _tesseract_binary() -> str | None:
    return shutil.which("tesseract")


def _ocr_language(lang: str | None) -> str:
    value = str(lang or DEFAULT_OCR_LANGUAGE).strip()
    if not OCR_LANGUAGE_PATTERN.fullmatch(value):
        raise ValueError(f"Некорректный язык OCR: {lang!r}")
    return value


def run_ocr_rapidocr(path: str | Path) -> OcrResult:
    """OCR через RapidOCR (PP-OCRv4 ONNX, det→cls→rec). Текст недоверенный (T02)."""
    from .rapidocr import run as _run

    result = _run(path)
    return OcrResult(
        text=str(result.get("text") or ""),
        engine=str(result.get("engine") or "none"),
        available=bool(result.get("available")),
        error=result.get("error"),
    )


def run_ocr(
    path: str | Path,
    *,
    timeout: float = 10.0,
    sandbox: bool = False,
    lang: str = DEFAULT_OCR_LANGUAGE,
) -> OcrResult:
    """OCR: RapidOCR (PP-OCRv4 ONNX) с fallback на tesseract; текст недоверенный (T02)."""
    if not sandbox:
        rapid = run_ocr_rapidocr(path)
        if rapid.available:
            return rapid
    language = _ocr_language(lang)
    if sandbox:
        result = run_ocr_sandboxed(path, lang=language)
        return OcrResult(
            text=str(result.get("text") or ""),
            engine="tesseract",
            available=bool(result.get("available")),
            error=result.get("error"),
        )
    binary = _tesseract_binary()
    if not binary:
        return OcrResult(available=False, engine="none", error="tesseract not installed")
    try:
        completed = subprocess.run(  # noqa: S603 - argv без shell
            [binary, str(path), "stdout", "-l", language],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            return OcrResult(available=True, engine="tesseract", error=completed.stderr[:300])
        text = sanitize_text(completed.stdout, limit=20_000)
        return OcrResult(text=text, engine="tesseract", available=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return OcrResult(available=True, engine="tesseract", error=str(exc))


def _media_path(catalog: Catalog, asset_id: int) -> Path | None:
    primary = catalog.primary_file(asset_id)
    if primary is None:
        return None
    root = catalog.volume_root(int(primary["volume_id"]))
    if not root:
        return None
    try:
        return safe_join(root, primary["rel_path"])
    except PathSafetyError:
        return None


def _scene_from_tags(tags: list[str]) -> str | None:
    mapping = {
        "скриншот": "screenshot",
        "документ": "document",
        "свадьба": "wedding_ceremony",
        "концерт": "concert",
        "спорт": "sport",
        "портрет": "portrait",
        "пляж": "landscape",
        "горы": "landscape",
        "город": "street",
        "природа": "landscape",
        "закат": "landscape",
        "еда": "food",
        "товар": "product",
        "авто": "product",
    }
    for tag in tags:
        if tag in mapping:
            return mapping[tag]
    return None


def _review_once(
    catalog: Catalog, asset_id: int, reason: str, severity: str, payload: dict[str, Any]
) -> None:
    row = catalog.conn.execute(
        "SELECT id FROM review_queue WHERE asset_id=? AND reason=? AND state='open' LIMIT 1",
        (asset_id, reason),
    ).fetchone()
    if row is None:
        catalog.add_review(asset_id, reason, severity=severity, payload=payload)


def classify_asset(
    catalog: Catalog,
    asset_id: int,
    *,
    run_ocr_enabled: bool = True,
    ocr_language: str = DEFAULT_OCR_LANGUAGE,
) -> dict[str, Any]:
    asset = catalog.get_asset(asset_id)
    if asset is None:
        raise ValueError(f"asset {asset_id} not found")
    path = _media_path(catalog, asset_id)
    kind = str(asset["kind"] or "other")
    media_class = str(asset["media_class"] or "unknown")
    scene_type = asset["scene_type"]
    started = time.monotonic()

    ocr_text = ""
    ocr_available = False
    if run_ocr_enabled and kind in {"photo", "raw", "other"} and path is not None:
        existing = catalog.get_ai_output(asset_id, "ocr")
        if existing:
            try:
                ocr_text = str(json.loads(existing["output_json"]).get("text", ""))
            except (TypeError, ValueError):
                ocr_text = ""
            ocr_available = str(existing["model_id"] or "none") != "none"
        else:
            result = run_ocr(path, sandbox=True, lang=ocr_language)
            ocr_available = result.available
            if result.available:
                ocr_text = result.text
                catalog.set_ai_output(
                    asset_id,
                    "ocr",
                    result.engine,
                    {"text": ocr_text, "error": result.error},
                    confidence=0.5 if ocr_text else 0.0,
                    runtime_ms=(time.monotonic() - started) * 1000,
                    device="cpu",
                )
                if ocr_text:
                    hits = detect_injection(ocr_text)[0]
                    pii = sorted(detect_pii(ocr_text))
                    if hits or pii:
                        _review_once(
                            catalog,
                            asset_id,
                            "ocr_sensitive_content",
                            "high" if hits else "warning",
                            {
                                "injection": hits,
                                "pii": pii,
                                "chars": len(ocr_text),
                            },
                        )

    primary = catalog.primary_file(asset_id)
    filename_text = Path(primary["rel_path"]).stem if primary else ""
    combined_text = " ".join(
        part for part in (ocr_text, filename_text, asset["description"] or "") if part
    )
    tags_with_scores = zero_shot_tags(combined_text, threshold=0.5)
    # Документ/скриншот — детерминированные сигналы, а не «магия» VLM.
    lowered = combined_text.lower()
    if any(marker in lowered for marker in ("screenshot", "скриншот", "screen shot")):
        scene_type = "screenshot"
        media_class = "screenshot"
    elif any(
        marker in lowered
        for marker in ("receipt", "invoice", "документ", "чек", "квитанция", "scan")
    ):
        scene_type = "document"
        media_class = "document"
    elif scene_type is None:
        scene_type = _scene_from_tags([tag for tag, _ in tags_with_scores])

    for tag, confidence in tags_with_scores:
        catalog.add_asset_tag(asset_id, tag, source="model", confidence=confidence)
    if tags_with_scores:
        catalog.set_ai_output(
            asset_id,
            "zeroshot",
            "zero-shot-dictionary-v1",
            {"tags": [{"name": tag, "confidence": score} for tag, score in tags_with_scores]},
            confidence=max(score for _, score in tags_with_scores),
            runtime_ms=(time.monotonic() - started) * 1000,
            device="cpu",
        )
    backend = get_embedding_backend(catalog)
    vector = embed_asset(catalog, asset_id, backend)
    # L1 (FR-3.10): zero-shot по изображению (SigLIP2 image→text)
    l1_scene, l1_conf, needs_vlm = classify_scene(vector, backend)
    if scene_type in (None, "unknown"):
        scene_type = l1_scene
    catalog.set_ai_output(
        asset_id,
        "scene_l1",
        backend.model_id,
        {"scene": l1_scene, "confidence": round(l1_conf, 4), "needs_vlm": needs_vlm},
        confidence=l1_conf,
        device="cpu",
    )

    catalog.update_asset_ai(asset_id, media_class=media_class, scene_type=scene_type)

    all_tags = [row["name"] for row in catalog.tags_for_asset(asset_id)]
    catalog.fts_upsert(
        asset_id,
        caption=str(asset["description"] or ""),
        ocr_text=ocr_text,
        asr_text="",
        tags=" ".join(all_tags),
    )
    return {
        "asset_id": asset_id,
        "media_class": media_class,
        "scene_type": scene_type,
        "tags": [{"name": tag, "confidence": score} for tag, score in tags_with_scores],
        "ocr_chars": len(ocr_text),
        "embedding_dim": len(vector),
        "embedding_model": backend.model_id,
        "ocr_available": ocr_available,
    }


def analyze_catalog(
    catalog: Catalog,
    *,
    limit: int | None = None,
    run_ocr_enabled: bool = True,
    ocr_language: str = DEFAULT_OCR_LANGUAGE,
) -> dict[str, Any]:
    processed = 0
    errors: list[str] = []
    for asset_id in catalog.iter_asset_ids():
        if limit is not None and processed >= limit:
            break
        try:
            classify_asset(
                catalog,
                asset_id,
                run_ocr_enabled=run_ocr_enabled,
                ocr_language=ocr_language,
            )
            processed += 1
        except (OSError, ValueError, sqlite3.Error) as exc:
            logger.exception("analyze failed for asset %s", asset_id)
            errors.append(f"asset {asset_id}: {type(exc).__name__}: {exc}")
    duplicates = compute_dup_groups(catalog)
    return {
        "processed": processed,
        "errors": errors,
        "embeddings": catalog.stats()["embeddings"],
        "fts5": catalog.fts5,
        "embedding_model": ConceptHashingEmbedding.model_id,
        "duplicates": duplicates,
    }


__all__ = ["OcrResult", "analyze_catalog", "classify_asset", "run_ocr"]
