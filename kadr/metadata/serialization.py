"""Сериализация MediaMetadata между движком и worker-процессом."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import MediaMetadata


def metadata_to_payload(meta: MediaMetadata) -> dict[str, Any]:
    return {
        "kind": meta.kind,
        "media_class": meta.media_class,
        "scene_type": meta.scene_type,
        "width": meta.width,
        "height": meta.height,
        "duration_ms": meta.duration_ms,
        "taken_at": meta.taken_at.isoformat() if meta.taken_at else None,
        "taken_at_source": meta.taken_at_source,
        "date_confidence": meta.date_confidence,
        "tz_offset": meta.tz_offset,
        "camera_make": meta.camera_make,
        "camera_model": meta.camera_model,
        "lens": meta.lens,
        "iso": meta.iso,
        "exposure_time": meta.exposure_time,
        "f_number": meta.f_number,
        "focal_length": meta.focal_length,
        "orientation": meta.orientation,
        "gps_lat": meta.gps_lat,
        "gps_lon": meta.gps_lon,
        "rating": meta.rating,
        "label": meta.label,
        "tags": list(meta.tags),
        "description": meta.description,
        "title": meta.title,
        "raw_json": meta.raw_json,
        "parse_errors": list(meta.parse_errors),
        "injection_suspected": meta.injection_suspected,
        "pii_suspected": meta.pii_suspected,
    }


def metadata_from_payload(payload: dict[str, Any]) -> MediaMetadata:
    taken_at = payload.get("taken_at")
    try:
        parsed_taken = datetime.fromisoformat(str(taken_at)) if taken_at else None
    except (TypeError, ValueError):
        parsed_taken = None
    return MediaMetadata(
        kind=str(payload.get("kind") or "other"),
        media_class=str(payload.get("media_class") or "unknown"),
        scene_type=payload.get("scene_type"),
        width=payload.get("width"),
        height=payload.get("height"),
        duration_ms=payload.get("duration_ms"),
        taken_at=parsed_taken,
        taken_at_source=payload.get("taken_at_source"),
        date_confidence=float(payload.get("date_confidence") or 0.0),
        tz_offset=payload.get("tz_offset"),
        camera_make=payload.get("camera_make"),
        camera_model=payload.get("camera_model"),
        lens=payload.get("lens"),
        iso=payload.get("iso"),
        exposure_time=payload.get("exposure_time"),
        f_number=payload.get("f_number"),
        focal_length=payload.get("focal_length"),
        orientation=payload.get("orientation"),
        gps_lat=payload.get("gps_lat"),
        gps_lon=payload.get("gps_lon"),
        rating=payload.get("rating"),
        label=payload.get("label"),
        tags=list(payload.get("tags") or []),
        description=payload.get("description"),
        title=payload.get("title"),
        raw_json=dict(payload.get("raw_json") or {}),
        parse_errors=list(payload.get("parse_errors") or []),
        injection_suspected=bool(payload.get("injection_suspected")),
        pii_suspected=bool(payload.get("pii_suspected")),
    )
