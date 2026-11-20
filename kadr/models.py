"""Типы домена Kadr (не привязаны к БД и фреймворкам)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class FileRecord:
    rel_path: str
    ext: str
    size_bytes: int
    mtime: float
    is_primary: bool = True
    exists: bool = True
    content_hash: str | None = None
    companion_of: str | None = None


@dataclass(slots=True)
class MediaMetadata:
    """Нормализованный результат L0-разбора."""

    kind: str = "other"
    media_class: str = "unknown"
    scene_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None
    taken_at: datetime | None = None
    taken_at_source: str | None = None
    date_confidence: float = 0.0
    tz_offset: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    lens: str | None = None
    iso: int | None = None
    exposure_time: str | None = None
    f_number: float | None = None
    focal_length: float | None = None
    orientation: int | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    rating: int | None = None
    label: str | None = None
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    title: str | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)
    parse_errors: list[str] = field(default_factory=list)
    injection_suspected: bool = False
    pii_suspected: bool = False

    def to_context(self) -> dict[str, Any]:
        """Плоский контекст для DSL: ключи с точками ищутся напрямую."""
        taken = self.taken_at.isoformat() if self.taken_at else None
        return {
            "date": taken,
            "date.source": self.taken_at_source,
            "date.confidence": self.date_confidence,
            "kind": self.kind,
            "media_class": self.media_class,
            "scene_type": self.scene_type,
            "camera.make": self.camera_make,
            "camera.model": self.camera_model,
            "camera.lens": self.lens,
            "lens": self.lens,
            "iso": self.iso,
            "ext": None,
            "size": None,
            "width": self.width,
            "height": self.height,
            "duration_ms": self.duration_ms,
            "gps.lat": self.gps_lat,
            "gps.lon": self.gps_lon,
            "gps.present": self.gps_lat is not None and self.gps_lon is not None,
            "rating": self.rating,
            "label": self.label,
            "tags": list(self.tags),
            "description": self.description,
            "title": self.title,
            "ai.scene_type": self.scene_type,
            "ai.media_class": self.media_class,
        }


@dataclass(slots=True)
class ScannedFile:
    path: str
    rel_path: str
    ext: str
    size_bytes: int
    mtime: float
    content_hash: str
    is_primary: bool
    group_id: str | None = None
    cloud_placeholder: bool = False


@dataclass(slots=True)
class ScanReport:
    root: str
    volume_id: int
    scanned: int = 0
    added_assets: int = 0
    added_files: int = 0
    skipped_unchanged: int = 0
    skipped_unsupported: int = 0
    errors: list[str] = field(default_factory=list)
    groups: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "volume_id": self.volume_id,
            "scanned": self.scanned,
            "added_assets": self.added_assets,
            "added_files": self.added_files,
            "skipped_unchanged": self.skipped_unchanged,
            "skipped_unsupported": self.skipped_unsupported,
            "groups": self.groups,
            "errors": list(self.errors),
        }


@dataclass(slots=True)
class RuleDecision:
    rule_id: str
    matched: bool
    stop: bool = False
    unavailable_on_profile: bool = False
    reason: str | None = None
    tags: list[str] = field(default_factory=list)
    path_template: str | None = None
    fallback: bool = False


@dataclass(slots=True)
class PlanItem:
    seq: int
    asset_id: int
    src: str
    dst: str
    action: str
    size_bytes: int
    state: str = "pending"
    error: str | None = None
    collision: bool = False
    review_reason: str | None = None
    rule_id: str | None = None
    hash: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Plan:
    # id == 0 — предпросмотр без сохранения в каталоге (dry-run, persist=False).
    id: int
    mode: str
    root: str
    state: str
    items: list[PlanItem]
    summary: dict[str, Any] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode,
            "root": self.root,
            "state": self.state,
            "summary": self.summary,
            "rules": self.rules,
            "items": [asdict(item) for item in self.items],
        }


@dataclass(slots=True)
class SearchHit:
    asset_id: int
    score: float
    reason: str
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IndexStatus:
    backend: str
    status: str
    vector_count: int = 0
    dim: int = 0
    model_id: str = ""
    index_generation: int = 1
    reason: str | None = None
    measured_p95_ms: float | None = None
    degradation_visible: bool | None = None  # вычисляется по статусу (см. __post_init__)

    def __post_init__(self) -> None:
        if self.degradation_visible is None:
            # «Видна» только реальная деградация; active/building — не повод для бейджа.
            self.degradation_visible = self.status in {"degraded", "unavailable"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "status": self.status,
            "vector_count": self.vector_count,
            "dim": self.dim,
            "model_id": self.model_id,
            "index_generation": self.index_generation,
            "reason": self.reason,
            "measured_p95_ms": self.measured_p95_ms,
            "degradation_visible": self.degradation_visible,
        }


@dataclass(slots=True)
class ProfileInfo:
    profile: str
    ram_gb: float
    cores: int
    gpu: str | None
    vram_gb: float | None
    apple_silicon: bool
    vlm_in_main_flow: bool
    nightly_vlm_available: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "ram_gb": self.ram_gb,
            "cores": self.cores,
            "gpu": self.gpu,
            "vram_gb": self.vram_gb,
            "apple_silicon": self.apple_silicon,
            "vlm_in_main_flow": self.vlm_in_main_flow,
            "nightly_vlm_available": self.nightly_vlm_available,
            "reasons": list(self.reasons),
        }
