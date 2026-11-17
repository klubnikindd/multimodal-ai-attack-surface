"""Сканер ФС, идемпотентная индексация и группировка ассетов (FR-1.x)."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from .catalog.repository import Catalog
from .config import Settings
from .constants import (
    AUDIO_EXTENSIONS,
    EDIT_SIDECAR_EXTENSIONS,
    IMAGE_EXTENSIONS,
    PROXY_EXTENSIONS,
    RAW_EXTENSIONS,
    SIDECAR_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from .hashing import hash_file, perceptual_hash
from .metadata.extractor import extract_metadata
from .models import MediaMetadata, ScanReport
from .security.paths import is_cloud_placeholder, validate_scan_root
from .workers.client import extract_metadata_sandboxed

_SKIP_DIRS = {
    ".git",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "$RECYCLE.BIN",
    "System Volume Information",
    ".Trash",
    ".Trashes",
    ".Spotlight-V100",
}
_PRIMARY_PRIORITY = {
    "photo": 0,
    "video": 1,
    "audio": 2,
    "raw": 3,
    "other": 4,
    "sidecar": 5,
}


def _kind_for_ext(ext: str) -> str:
    if ext in IMAGE_EXTENSIONS:
        return "photo"
    if ext in RAW_EXTENSIONS:
        return "raw"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext in SIDECAR_EXTENSIONS:
        return "sidecar"
    return "other"


def _group_key(path: Path, root: Path) -> str:
    try:
        rel_parent = path.parent.relative_to(root).as_posix()
    except ValueError:
        rel_parent = path.parent.as_posix()
    return f"{rel_parent}/{path.stem.lower()}"


def group_paths(paths: Iterable[Path], root: Path) -> list[list[Path]]:
    """Группирует RAW+JPEG+XMP, Live Photo, sidecar'ы и прокси (FR-1.6).

    Группировка консервативная: одинаковый stem и каталог. Burst-серии —
    отдельная P1-фича, здесь не склеиваем разные снимки.
    """
    groups: dict[str, list[Path]] = {}
    for path in paths:
        ext = path.suffix.lower()
        if ext in SIDECAR_EXTENSIONS and ext not in EDIT_SIDECAR_EXTENSIONS and ext not in PROXY_EXTENSIONS:
            key = f"{_group_key(path.with_suffix(''), root)}"
        else:
            key = _group_key(path, root)
        groups.setdefault(key, []).append(path)
    result = []
    for members in groups.values():
        # Если группа состоит только из sidecar без основного файла — оставляем как отдельный other.
        result.append(sorted(members, key=lambda p: (_PRIMARY_PRIORITY.get(_kind_for_ext(p.suffix.lower()), 9), p.name.lower())))
    return result


def _pick_primary(paths: list[Path]) -> Path:
    return min(
        paths,
        key=lambda p: (_PRIMARY_PRIORITY.get(_kind_for_ext(p.suffix.lower()), 9), len(p.name), p.name.lower()),
    )


def _generic_metadata(path: Path, kind: str) -> MediaMetadata:
    from .metadata.dates import resolve_taken_at

    stat = path.stat()
    resolution = resolve_taken_at(filename=path.name, mtime=stat.st_mtime)
    return MediaMetadata(
        kind=kind,
        media_class="unknown" if kind == "other" else kind,
        taken_at=resolution.taken_at,
        taken_at_source=resolution.source,
        date_confidence=resolution.confidence,
        tz_offset=resolution.tz_offset,
        raw_json={"file": {"name": path.name, "ext": path.suffix.lower(), "size_bytes": stat.st_size}},
    )


def _safe_extract(path: Path, kind: str, settings: Settings) -> MediaMetadata:
    if path.suffix.lower() in SUPPORTED_EXTENSIONS:
        try:
            if settings.parser_sandbox:
                return extract_metadata_sandboxed(path)
            return extract_metadata(path)
        except Exception as exc:  # парсер не роняет скан
            meta = _generic_metadata(path, kind)
            meta.parse_errors.append(f"{type(exc).__name__}: {exc}")
            return meta
    return _generic_metadata(path, kind)


def _walk(root: Path, settings: Settings) -> Iterator[Path]:
    for current, dirs, files in os.walk(root, followlinks=bool(settings.follow_symlinks)):
        if settings.skip_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
        else:
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            path = Path(current) / name
            if path.is_symlink() and not settings.follow_symlinks:
                continue
            if settings.skip_hidden and name.startswith("."):
                continue
            if any(marker in name for marker in (".kadr-backup", ".kadr-tmp-", ".kadr-embed-")):
                continue  # служебные файлы продукта
            yield path


def scan_root(catalog: Catalog, root: str | Path, settings: Settings | None = None) -> ScanReport:
    settings = settings or Settings()
    root_path = validate_scan_root(root, allowed_roots=settings.allowed_scan_roots or None)
    volume_id = catalog.ensure_volume(root_path)
    report = ScanReport(root=str(root_path), volume_id=volume_id)

    paths = list(_walk(root_path, settings))
    groups = group_paths(paths, root_path)
    report.groups = len(groups)

    for group in groups:
        primary = _pick_primary(group)
        rels: dict[Path, str] = {}
        for path in group:
            try:
                rels[path] = path.relative_to(root_path).as_posix()
            except ValueError:
                report.errors.append(f"path outside root: {path}")
        if len(rels) != len(group):
            continue
        existing_rows = {p: catalog.get_file_by_path(volume_id, rels[p]) for p in group}
        signatures: dict[Path, tuple[int, float] | None] = {}
        for path in group:
            try:
                stat = path.stat()
                signatures[path] = (stat.st_size, stat.st_mtime)
            except OSError as exc:
                signatures[path] = None
                report.errors.append(f"{rels[path]}: stat error: {exc}")
        if any(signature is None for signature in signatures.values()):
            # Файл исчез между обходом и обработкой: группа пропускается, скан живёт.
            continue
        unchanged = {
            p: bool(
                row
                and row["exists_flag"]
                and int(row["size_bytes"]) == signatures[p][0]  # type: ignore[index]
                and abs(float(row["mtime"]) - signatures[p][1]) < 1e-6  # type: ignore[index]
            )
            for p, row in existing_rows.items()
        }
        placeholder = any(is_cloud_placeholder(p) for p in group)
        if placeholder:
            catalog.add_review(None, "cloud_placeholder", severity="warning", payload={"path": rels[primary]})
            report.skipped_unchanged += len(group)
            continue
        if all(unchanged.values()):
            report.skipped_unchanged += len(group)
            report.scanned += len(group)
            continue

        primary_rel = rels[primary]
        primary_row = existing_rows.get(primary)
        if unchanged.get(primary) and primary_row is not None:
            asset_id = int(primary_row["asset_id"])
            asset = catalog.get_asset(asset_id)
            if asset is None:  # повреждённый каталог — переиндексируем
                unchanged[primary] = False
                asset_id = 0
            else:
                content_hash = asset["content_hash"]
        else:
            content_hash = ""
            asset_id = 0

        if not asset_id:
            try:
                content_hash = hash_file(primary)
            except (OSError, ValueError) as exc:
                report.errors.append(f"{primary_rel}: hash error: {exc}")
                continue
            kind = _kind_for_ext(primary.suffix.lower())
            meta = _safe_extract(primary, kind, settings)
            meta.kind = kind
            try:
                asset_id, created = catalog.upsert_asset_ex(
                    content_hash, meta, size_bytes=primary.stat().st_size
                )
            except Exception as exc:
                report.errors.append(f"{primary_rel}: catalog error: {exc}")
                continue
            if created:
                report.added_assets += 1
            try:
                catalog.add_meta_raw(asset_id, "pillow/ffprobe", "v0.1", meta.raw_json)
            except Exception:
                pass
            _persist_asset_side_data(catalog, asset_id, primary, meta)
            phash = perceptual_hash(primary) if kind in {"photo", "raw"} else None
            if phash:
                catalog.conn.execute("UPDATE assets SET phash=? WHERE id=?", (phash, asset_id))
        else:
            kind = _kind_for_ext(primary.suffix.lower())
            # Изменился компаньон: перечитываем primary и сверяем хэш, чтобы не
            # записать в каталог устаревший content_hash при изменении mtime.
            try:
                current_hash = hash_file(primary)
                if current_hash:
                    content_hash = current_hash
            except (OSError, ValueError) as exc:
                report.errors.append(f"{primary_rel}: hash refresh error: {exc}")
            meta = _safe_extract(primary, kind, settings)
            meta.kind = kind
            asset_id, _ = catalog.upsert_asset_ex(content_hash, meta, size_bytes=primary.stat().st_size)
            _persist_asset_side_data(catalog, asset_id, primary, meta)

        for member in group:
            rel = rels[member]
            try:
                stat = member.stat()
            except OSError as exc:
                report.errors.append(f"{rel}: stat error: {exc}")
                continue
            catalog.add_file(
                asset_id,
                volume_id,
                rel,
                member.suffix.lower(),
                stat.st_size,
                stat.st_mtime,
                is_primary=(member == primary),
            )
            if member.suffix.lower() not in SUPPORTED_EXTENSIONS:
                report.skipped_unsupported += 1
            if not unchanged.get(member, False):
                report.added_files += 1
            report.scanned += 1
    return report


def _review_once(catalog: Catalog, asset_id: int, reason: str, severity: str, payload: dict) -> None:
    row = catalog.conn.execute(
        "SELECT id FROM review_queue WHERE asset_id=? AND reason=? AND state='open' LIMIT 1",
        (asset_id, reason),
    ).fetchone()
    if row is None:
        catalog.add_review(asset_id, reason, severity=severity, payload=payload)


def _persist_asset_side_data(catalog: Catalog, asset_id: int, path: Path, meta: MediaMetadata) -> None:
    for tag in meta.tags:
        catalog.add_asset_tag(asset_id, tag, source="import", confidence=None)
    if meta.injection_suspected:
        _review_once(
            catalog,
            asset_id,
            "metadata_injection_suspected",
            "high",
            {"hits": list(meta.raw_json.get("injection_hits", [])), "path": path.name},
        )
    if meta.pii_suspected:
        _review_once(catalog, asset_id, "pii_in_metadata", "warning", {"path": path.name})
    if meta.taken_at_source in {"filename", "mtime"}:
        _review_once(
            catalog,
            asset_id,
            "date_low_confidence",
            "info",
            {"source": meta.taken_at_source, "confidence": meta.date_confidence},
        )
    tags_text = " ".join(meta.tags)
    catalog.fts_upsert(
        asset_id,
        caption=meta.description or "",
        ocr_text="",
        asr_text="",
        tags=tags_text,
    )


def find_exact_duplicates(catalog: Catalog) -> dict[str, list[str]]:
    """Точные дубли по content_hash: один ассет — несколько файлов."""
    rows = catalog.conn.execute(
        "SELECT a.id, a.content_hash, COUNT(f.id) AS n FROM assets a JOIN files f ON f.asset_id=a.id "
        "WHERE f.exists_flag=1 GROUP BY a.id HAVING n > 1"
    ).fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        files = catalog.conn.execute(
            "SELECT rel_path FROM files WHERE asset_id=? AND exists_flag=1 ORDER BY id",
            (row["id"],),
        ).fetchall()
        if len(files) > 1:
            result[row["content_hash"]] = [f["rel_path"] for f in files]
    return result


def scan_many(catalog: Catalog, roots: Iterable[str | Path], settings: Settings | None = None) -> list[ScanReport]:
    return [scan_root(catalog, root, settings) for root in roots]
