"""Репозиторий каталога SQLite (ТЗ v3 §7)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import CatalogError
from ..models import MediaMetadata, Plan, PlanItem

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 4
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _inserted_id(cursor: sqlite3.Cursor) -> int:
    """«BT»lastrowid«BT» типизирован как «BT»int | None«BT»; после INSERT он всегда есть."""
    if cursor.lastrowid is None:  # pragma: no cover - не должно случаться
        raise RuntimeError("INSERT не вернул lastrowid")
    return int(cursor.lastrowid)


class Catalog:
    """Тонкая обёртка над SQLite с WAL, миграциями и репозиторными методами."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:  # pragma: no cover - Windows
            pass
        self.conn = sqlite3.connect(
            str(self.path), timeout=30, isolation_level=None, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.fts5 = False
        self.migrate()
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - Windows
            pass

    # ------------------------------------------------------------------ schema
    def migrate(self) -> None:
        try:
            self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            self._ensure_fts()
            self._ensure_column("plan_items", "hash", "TEXT")
            self._ensure_column("plan_items", "tags_json", "TEXT")
            # FR-2.14: группы дублей (exact по content_hash; near — только review)
            self._ensure_column("assets", "dup_group", "TEXT")
            self._ensure_column("assets", "dup_is_original", "INTEGER")
            self._ensure_column("assets", "dup_copies", "INTEGER")
            row = self.conn.execute(
                "SELECT version FROM schema_meta ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if row is None:
                self.conn.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif int(row["version"]) < SCHEMA_VERSION:
                self.conn.execute(
                    "UPDATE schema_meta SET version=?, applied_at=? WHERE version=?",
                    (SCHEMA_VERSION, utcnow(), int(row["version"])),
                )
        except sqlite3.DatabaseError as exc:  # pragma: no cover - защита от порчи
            raise CatalogError(f"Не удалось инициализировать каталог: {exc}") from exc

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        # Имена объектов SQLite нельзя биндить параметрами: валидируем их явно,
        # чтобы миграция не стала вектором инъекции при будущих изменениях.
        for identifier, value in (("table", table), ("column", column)):
            if not _IDENTIFIER_RE.fullmatch(value):
                raise CatalogError(f"Недопустимый идентификатор {identifier}: {value!r}")
        if not _IDENTIFIER_RE.fullmatch(ddl):
            raise CatalogError(f"Недопустимый тип колонки: {ddl!r}")
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {row["name"] for row in rows}:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _ensure_fts(self) -> None:
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5("
                "asset_id UNINDEXED, caption, ocr_text, asr_text, tags, tokenize='unicode61')"
            )
            self.fts5 = True
        except sqlite3.OperationalError:
            # FTS5 нет: деградация видима через Catalog.fts5=False и статус поиска.
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS fts_fallback("
                "asset_id INTEGER PRIMARY KEY, caption TEXT, ocr_text TEXT, asr_text TEXT, tags TEXT)"
            )
            self.fts5 = False

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN")
        try:
            yield self.conn
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ settings
    def set_setting(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), utcnow()),
        )

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------ volumes
    def ensure_volume(self, root_path: str | Path, label: str | None = None) -> int:
        root = str(Path(root_path).resolve())
        row = self.conn.execute("SELECT id FROM volumes WHERE root_path=?", (root,)).fetchone()
        if row:
            self.conn.execute("UPDATE volumes SET last_seen=? WHERE id=?", (utcnow(), row["id"]))
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO volumes(uuid, label, root_path, last_seen) VALUES (?, ?, ?, ?)",
            (None, label or Path(root).name, root, utcnow()),
        )
        return _inserted_id(cur)

    # ------------------------------------------------------------------ assets
    def get_asset_by_hash(self, content_hash: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM assets WHERE content_hash=?", (content_hash,)
        ).fetchone()

    def upsert_asset(self, content_hash: str, meta: MediaMetadata, *, size_bytes: int) -> int:
        return self.upsert_asset_ex(content_hash, meta, size_bytes=size_bytes)[0]

    def upsert_asset_ex(
        self, content_hash: str, meta: MediaMetadata, *, size_bytes: int
    ) -> tuple[int, bool]:
        """Возвращает (asset_id, created); один SELECT вместо гонки scanner+upsert."""
        existing = self.get_asset_by_hash(content_hash)
        if existing:
            # Идемпотентность: обновляем только пустые/уточнённые L0-поля, не затирая AI-результаты.
            self.conn.execute(
                "UPDATE assets SET updated_at=?, kind=COALESCE(NULLIF(kind,'other'), ?), "
                "media_class=COALESCE(NULLIF(media_class,'unknown'), ?), "
                "width=COALESCE(width, ?), height=COALESCE(height, ?), "
                "duration_ms=COALESCE(duration_ms, ?), "
                "taken_at=COALESCE(taken_at, ?), taken_at_source=COALESCE(taken_at_source, ?), "
                "date_confidence=MAX(date_confidence, ?), tz=COALESCE(tz, ?), "
                "camera_make=COALESCE(camera_make, ?), camera_model=COALESCE(camera_model, ?), "
                "lens=COALESCE(lens, ?), iso=COALESCE(iso, ?), exposure_time=COALESCE(exposure_time, ?), "
                "f_number=COALESCE(f_number, ?), focal_length=COALESCE(focal_length, ?), "
                "orientation=COALESCE(orientation, ?), gps_lat=COALESCE(gps_lat, ?), "
                "gps_lon=COALESCE(gps_lon, ?), rating=COALESCE(rating, ?), label=COALESCE(label, ?), "
                "description=COALESCE(description, ?), title=COALESCE(title, ?), "
                "injection_suspected=MAX(injection_suspected, ?), pii_suspected=MAX(pii_suspected, ?) "
                "WHERE id=?",
                (
                    utcnow(),
                    meta.kind,
                    meta.media_class,
                    meta.width,
                    meta.height,
                    meta.duration_ms,
                    meta.taken_at.isoformat() if meta.taken_at else None,
                    meta.taken_at_source,
                    meta.date_confidence,
                    meta.tz_offset,
                    meta.camera_make,
                    meta.camera_model,
                    meta.lens,
                    meta.iso,
                    meta.exposure_time,
                    meta.f_number,
                    meta.focal_length,
                    meta.orientation,
                    meta.gps_lat,
                    meta.gps_lon,
                    meta.rating,
                    meta.label,
                    meta.description,
                    meta.title,
                    int(meta.injection_suspected),
                    int(meta.pii_suspected),
                    existing["id"],
                ),
            )
            return int(existing["id"]), False
        now = utcnow()
        cur = self.conn.execute(
            "INSERT INTO assets(content_hash, kind, media_class, scene_type, bytes, width, height, "
            "duration_ms, taken_at, taken_at_source, date_confidence, tz, camera_make, camera_model, "
            "lens, iso, exposure_time, f_number, focal_length, orientation, gps_lat, gps_lon, "
            "rating, label, description, title, injection_suspected, pii_suspected, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                content_hash,
                meta.kind,
                meta.media_class,
                meta.scene_type,
                size_bytes,
                meta.width,
                meta.height,
                meta.duration_ms,
                meta.taken_at.isoformat() if meta.taken_at else None,
                meta.taken_at_source,
                meta.date_confidence,
                meta.tz_offset,
                meta.camera_make,
                meta.camera_model,
                meta.lens,
                meta.iso,
                meta.exposure_time,
                meta.f_number,
                meta.focal_length,
                meta.orientation,
                meta.gps_lat,
                meta.gps_lon,
                meta.rating,
                meta.label,
                meta.description,
                meta.title,
                int(meta.injection_suspected),
                int(meta.pii_suspected),
                now,
                now,
            ),
        )
        return _inserted_id(cur), True

    def update_asset_ai(
        self, asset_id: int, *, media_class: str | None = None, scene_type: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE assets SET media_class=COALESCE(?, media_class), scene_type=COALESCE(?, scene_type), "
            "updated_at=? WHERE id=?",
            (media_class, scene_type, utcnow(), asset_id),
        )

    def get_asset(self, asset_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()

    def list_assets(
        self, limit: int = 100, offset: int = 0, kind: str | None = None
    ) -> list[sqlite3.Row]:
        if kind:
            return list(
                self.conn.execute(
                    "SELECT * FROM assets WHERE kind=? ORDER BY id LIMIT ? OFFSET ?",
                    (kind, limit, offset),
                )
            )
        return list(
            self.conn.execute("SELECT * FROM assets ORDER BY id LIMIT ? OFFSET ?", (limit, offset))
        )

    def iter_assets(self, batch_size: int = 500) -> Iterator[list[sqlite3.Row]]:
        last = 0
        while True:
            rows = list(
                self.conn.execute(
                    "SELECT * FROM assets WHERE id>? ORDER BY id LIMIT ?", (last, batch_size)
                )
            )
            if not rows:
                break
            last = int(rows[-1]["id"])
            yield rows

    def count_assets(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM assets").fetchone()
        return int(row["n"])

    def asset_context(self, asset_id: int) -> dict[str, Any]:
        asset = self.get_asset(asset_id)
        if asset is None:
            return {}
        primary = self.primary_file(asset_id)
        tags = self.tags_for_asset(asset_id)
        ctx: dict[str, Any] = {key: asset[key] for key in asset.keys()}
        taken_at = None
        if asset["taken_at"]:
            try:
                taken_at = datetime.fromisoformat(str(asset["taken_at"]).replace("Z", "+00:00"))
            except ValueError:
                taken_at = None
        ctx.update(
            {
                "date": taken_at,
                "date.source": asset["taken_at_source"],
                "date.confidence": asset["date_confidence"],
                "ext": Path(primary["rel_path"]).suffix.lower().lstrip(".") if primary else None,
                "size": int(asset["bytes"] or 0),
                "tags": [t["name"] for t in tags],
                "persons": [],
                "file.stem": Path(primary["rel_path"]).stem if primary else None,
                "file.mtime": (
                    datetime.fromtimestamp(float(primary["mtime"]))
                    if primary and primary["mtime"]
                    else None
                ),
                "hash": asset["content_hash"],
                "gps.present": asset["gps_lat"] is not None and asset["gps_lon"] is not None,
                "ai.scene_type": asset["scene_type"],
                "ai.media_class": asset["media_class"],
                "ai.doc_kind": "document" if asset["scene_type"] == "document" else None,
                "ai.confidence": None,
            }
        )
        zeroshot = self.get_ai_output(asset_id, "zeroshot")
        if zeroshot:
            try:
                zero_output = json.loads(zeroshot["output_json"])
            except (TypeError, ValueError):
                zero_output = {}
            for key, value in zero_output.items():
                ctx.setdefault(f"ai.{key}", value)
            ctx["ai.confidence"] = zeroshot["confidence"]
        ai = self.get_ai_output(asset_id, "vlm")
        if ai:
            try:
                output = json.loads(ai["output_json"])
            except (TypeError, ValueError):
                output = {}
            for key, value in output.items():
                ctx[f"ai.{key}"] = value
            ctx["ai.confidence"] = ai["confidence"]
        # FR-2.14 / §5.2.3: dup.group → origin-<hash8> | dup-<hash8> | single
        dup_group = asset["dup_group"] if "dup_group" in asset.keys() else None
        ctx["dup.group"] = str(dup_group) if dup_group else "single"
        ctx["dup.is_original"] = bool(asset["dup_is_original"])
        ctx["dup.copies"] = int(asset["dup_copies"] or 0)
        return ctx

    def set_dup_group(
        self, asset_id: int, group_id: str, *, is_original: bool = False, copies: int = 0
    ) -> None:
        """FR-2.14: фиксирует принадлежность ассета к группе дублей."""
        self.conn.execute(
            "UPDATE assets SET dup_group=?, dup_is_original=?, dup_copies=? WHERE id=?",
            (group_id, int(is_original), int(copies), asset_id),
        )

    # ------------------------------------------------------------------ files
    def add_file(
        self,
        asset_id: int,
        volume_id: int,
        rel_path: str,
        ext: str,
        size_bytes: int,
        mtime: float,
        is_primary: bool,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO files(asset_id, volume_id, rel_path, ext, size_bytes, mtime, is_primary, exists_flag) "
            "VALUES (?,?,?,?,?,?,?,1) "
            "ON CONFLICT(volume_id, rel_path) DO UPDATE SET asset_id=excluded.asset_id, "
            "ext=excluded.ext, size_bytes=excluded.size_bytes, mtime=excluded.mtime, "
            "is_primary=excluded.is_primary, exists_flag=1",
            (asset_id, volume_id, rel_path, ext, size_bytes, mtime, int(is_primary)),
        )
        return int(cur.lastrowid or 0)

    def get_file_by_path(self, volume_id: int, rel_path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM files WHERE volume_id=? AND rel_path=?", (volume_id, rel_path)
        ).fetchone()

    def primary_file(self, asset_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM files WHERE asset_id=? AND is_primary=1 ORDER BY id LIMIT 1", (asset_id,)
        ).fetchone()

    def files_for_asset(self, asset_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM files WHERE asset_id=? ORDER BY is_primary DESC, id", (asset_id,)
            )
        )

    def mark_file_missing(self, file_id: int) -> None:
        self.conn.execute("UPDATE files SET exists_flag=0 WHERE id=?", (file_id,))

    def volume_root(self, volume_id: int) -> str | None:
        row = self.conn.execute("SELECT root_path FROM volumes WHERE id=?", (volume_id,)).fetchone()
        return str(row["root_path"]) if row else None

    # ------------------------------------------------------------------ raw metadata
    def add_meta_raw(
        self, asset_id: int, tool: str, tool_version: str, payload: dict[str, Any]
    ) -> None:
        self.conn.execute(
            "INSERT INTO meta_raw(asset_id, tool, tool_version, json, created_at) VALUES (?,?,?,?,?)",
            (
                asset_id,
                tool,
                tool_version,
                json.dumps(payload, ensure_ascii=False, default=str),
                utcnow(),
            ),
        )

    # ------------------------------------------------------------------ AI / tags
    def set_ai_output(
        self,
        asset_id: int,
        task: str,
        model_id: str,
        output: dict[str, Any],
        *,
        model_rev: str = "",
        prompt_rev: str = "",
        confidence: float | None = None,
        runtime_ms: float | None = None,
        device: str | None = None,
        source: str = "model",
        experimental: bool = False,
    ) -> None:
        self.conn.execute(
            "INSERT INTO ai(asset_id, task, model_id, model_rev, prompt_rev, output_json, confidence, "
            "runtime_ms, device, source, experimental, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(asset_id, task, model_id, model_rev, prompt_rev) DO UPDATE SET "
            "output_json=excluded.output_json, confidence=excluded.confidence, "
            "runtime_ms=excluded.runtime_ms, device=excluded.device, source=excluded.source, "
            "experimental=excluded.experimental, created_at=excluded.created_at",
            (
                asset_id,
                task,
                model_id,
                model_rev,
                prompt_rev,
                json.dumps(output, ensure_ascii=False, default=str),
                confidence,
                runtime_ms,
                device,
                source,
                int(experimental),
                utcnow(),
            ),
        )

    def get_ai_output(self, asset_id: int, task: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM ai WHERE asset_id=? AND task=? ORDER BY id DESC LIMIT 1",
            (asset_id, task),
        ).fetchone()

    def get_ai_outputs(self, asset_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM ai WHERE asset_id=? ORDER BY id", (asset_id,)))

    def ensure_tag(
        self, name: str, *, source: str = "model", confidence: float | None = None
    ) -> int:
        row = self.conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if row:
            return int(row["id"])
        cur = self.conn.execute("INSERT INTO tags(name) VALUES (?)", (name,))
        return _inserted_id(cur)

    def add_asset_tag(
        self, asset_id: int, name: str, *, source: str = "model", confidence: float | None = None
    ) -> None:
        tag_id = self.ensure_tag(name)
        self.conn.execute(
            "INSERT INTO asset_tags(asset_id, tag_id, source, confidence, created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(asset_id, tag_id, source) DO UPDATE SET confidence=excluded.confidence",
            (asset_id, tag_id, source, confidence, utcnow()),
        )

    def tags_for_asset(self, asset_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT t.id, t.name, at.source, at.confidence, at.verified_by FROM asset_tags at "
                "JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=? ORDER BY t.name",
                (asset_id,),
            )
        )

    # ------------------------------------------------------------------ embeddings / vector state
    def upsert_embedding(
        self,
        asset_id: int,
        model_id: str,
        vector: Sequence[float],
        *,
        index_generation: int = 1,
        quantization: str = "float32",
    ) -> None:
        import struct

        blob = struct.pack(f"<{len(vector)}f", *[float(x) for x in vector])
        self.conn.execute(
            "INSERT INTO embeddings(asset_id, model_id, dim, index_generation, quantization, vec, created_at) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(asset_id, model_id, index_generation) DO UPDATE SET "
            "dim=excluded.dim, quantization=excluded.quantization, vec=excluded.vec, created_at=excluded.created_at",
            (asset_id, model_id, len(vector), index_generation, quantization, blob, utcnow()),
        )

    def get_embeddings(self, model_id: str, index_generation: int = 1) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT asset_id, dim, vec FROM embeddings WHERE model_id=? AND index_generation=? ORDER BY asset_id",
                (model_id, index_generation),
            )
        )

    def count_embeddings(self, model_id: str, index_generation: int = 1) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM embeddings WHERE model_id=? AND index_generation=?",
            (model_id, index_generation),
        ).fetchone()
        return int(row["n"])

    def set_vector_state(
        self,
        backend: str,
        *,
        model_id: str,
        index_generation: int,
        status: str,
        reason: str | None,
        vector_count: int,
        dim: int,
        quantization: str = "float32",
        measured_p95_ms: float | None = None,
        build_progress: float = 1.0,
    ) -> None:
        self.conn.execute(
            "INSERT INTO vector_index_state(backend, model_id, index_generation, vector_count, dim, "
            "quantization, built_at, build_progress, status, reason, measured_p95_ms, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(backend, model_id, index_generation) DO UPDATE SET "
            "vector_count=excluded.vector_count, dim=excluded.dim, quantization=excluded.quantization, "
            "built_at=excluded.built_at, build_progress=excluded.build_progress, status=excluded.status, "
            "reason=excluded.reason, measured_p95_ms=excluded.measured_p95_ms, updated_at=excluded.updated_at",
            (
                backend,
                model_id,
                index_generation,
                vector_count,
                dim,
                quantization,
                utcnow() if status == "active" else None,
                build_progress,
                status,
                reason,
                measured_p95_ms,
                utcnow(),
            ),
        )

    def get_vector_state(
        self, backend: str, model_id: str, index_generation: int
    ) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM vector_index_state WHERE backend=? AND model_id=? AND index_generation=?",
            (backend, model_id, index_generation),
        ).fetchone()

    def latest_vector_state(self, model_id: str | None = None) -> sqlite3.Row | None:
        if model_id:
            return self.conn.execute(
                "SELECT * FROM vector_index_state WHERE model_id=? ORDER BY updated_at DESC, id DESC LIMIT 1",
                (model_id,),
            ).fetchone()
        return self.conn.execute(
            "SELECT * FROM vector_index_state ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()

    def current_index_generation(self, model_id: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(index_generation) AS g FROM embeddings WHERE model_id=?", (model_id,)
        ).fetchone()
        return int(row["g"] or 1)

    # ------------------------------------------------------------------ FTS
    def fts_upsert(
        self,
        asset_id: int,
        *,
        caption: str = "",
        ocr_text: str = "",
        asr_text: str = "",
        tags: str = "",
    ) -> None:
        if self.fts5:
            self.conn.execute("DELETE FROM fts WHERE asset_id=?", (asset_id,))
            self.conn.execute(
                "INSERT INTO fts(asset_id, caption, ocr_text, asr_text, tags) VALUES (?,?,?,?,?)",
                (asset_id, caption, ocr_text, asr_text, tags),
            )
        else:
            self.conn.execute(
                "INSERT INTO fts_fallback(asset_id, caption, ocr_text, asr_text, tags) VALUES (?,?,?,?,?) "
                "ON CONFLICT(asset_id) DO UPDATE SET caption=excluded.caption, ocr_text=excluded.ocr_text, "
                "asr_text=excluded.asr_text, tags=excluded.tags",
                (asset_id, caption, ocr_text, asr_text, tags),
            )

    def fts_search(self, query: str, limit: int = 50) -> list[int]:
        if not query.strip():
            return []
        try:
            if self.fts5:
                rows = self.conn.execute(
                    "SELECT asset_id FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT ?",
                    (query, limit),
                ).fetchall()
            else:
                like = f"%{query}%"
                rows = self.conn.execute(
                    "SELECT asset_id FROM fts_fallback WHERE caption LIKE ? OR ocr_text LIKE ? "
                    "OR asr_text LIKE ? OR tags LIKE ? LIMIT ?",
                    (like, like, like, like, limit),
                ).fetchall()
            return [int(r["asset_id"]) for r in rows]
        except sqlite3.OperationalError:
            # Пользовательский запрос может быть невалиден для FTS (кавычки/операторы).
            like = f"%{query}%"
            if self.fts5:
                rows = self.conn.execute(
                    "SELECT asset_id FROM fts WHERE caption LIKE ? OR ocr_text LIKE ? OR asr_text LIKE ? "
                    "OR tags LIKE ? LIMIT ?",
                    (like, like, like, like, limit),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT asset_id FROM fts_fallback WHERE caption LIKE ? OR ocr_text LIKE ? "
                    "OR asr_text LIKE ? OR tags LIKE ? LIMIT ?",
                    (like, like, like, like, limit),
                ).fetchall()
            return [int(r["asset_id"]) for r in rows]

    # ------------------------------------------------------------------ review / jobs
    def add_review(
        self,
        asset_id: int | None,
        reason: str,
        *,
        severity: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO review_queue(asset_id, reason, severity, payload_json, state, created_at) "
            "VALUES (?,?,?,?, 'open', ?)",
            (
                asset_id,
                reason,
                severity,
                json.dumps(payload or {}, ensure_ascii=False, default=str),
                utcnow(),
            ),
        )
        return _inserted_id(cur)

    def list_review(self, state: str = "open", limit: int = 100) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM review_queue WHERE state=? ORDER BY "
                "CASE severity WHEN 'high' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id DESC LIMIT ?",
                (state, limit),
            )
        )

    def resolve_review(self, review_id: int, state: str = "resolved") -> None:
        self.conn.execute("UPDATE review_queue SET state=? WHERE id=?", (state, review_id))

    def create_job(
        self,
        kind: str,
        *,
        total: int = 0,
        eta_seconds: int | None = None,
        state: str = "pending",
        throttle_reason: str | None = None,
    ) -> int:
        now = utcnow()
        cur = self.conn.execute(
            "INSERT INTO jobs(kind, state, progress, total, eta_seconds, throttle_reason, created_at, updated_at) "
            "VALUES (?,?,0,?,?,?,?,?)",
            (kind, state, total, eta_seconds, throttle_reason, now, now),
        )
        return _inserted_id(cur)

    def update_job(
        self,
        job_id: int,
        *,
        state: str | None = None,
        progress: float | None = None,
        total: int | None = None,
        error: str | None = None,
        eta_seconds: int | None = None,
        throttle_reason: str | None = None,
        finished: bool = False,
    ) -> None:
        fields: list[str] = ["updated_at=?"]
        values: list[Any] = [utcnow()]
        for name, value in (
            ("state", state),
            ("progress", progress),
            ("total", total),
            ("error", error),
            ("eta_seconds", eta_seconds),
            ("throttle_reason", throttle_reason),
        ):
            if value is not None:
                fields.append(f"{name}=?")
                values.append(value)
        if finished:
            fields.append("finished_at=?")
            values.append(utcnow())
        values.append(job_id)
        self.conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", values)

    def get_job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def list_jobs(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)))

    # ------------------------------------------------------------------ plans / journal
    def create_plan(
        self,
        mode: str,
        root: str,
        rules: dict[str, Any],
        summary: dict[str, Any],
        items: list[PlanItem],
    ) -> int:
        now = utcnow()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO plans(mode, root, rules_json, summary_json, state, created_at) "
                "VALUES (?,?,?,?, 'draft', ?)",
                (
                    mode,
                    root,
                    json.dumps(rules, ensure_ascii=False),
                    json.dumps(summary, ensure_ascii=False),
                    now,
                ),
            )
            plan_id = _inserted_id(cur)
            conn.executemany(
                "INSERT INTO plan_items(plan_id, seq, asset_id, src, dst, action, size_bytes, state, error, "
                "collision, review_reason, rule_id, hash, tags_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        plan_id,
                        item.seq,
                        item.asset_id,
                        item.src,
                        item.dst,
                        item.action,
                        item.size_bytes,
                        item.state,
                        item.error,
                        int(item.collision),
                        item.review_reason,
                        item.rule_id,
                        item.hash,
                        json.dumps(item.tags, ensure_ascii=False),
                    )
                    for item in items
                ],
            )
        return plan_id

    def get_plan(self, plan_id: int) -> Plan | None:
        row = self.conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            return None
        items = [
            PlanItem(
                seq=int(r["seq"]),
                asset_id=int(r["asset_id"] or 0),
                src=r["src"],
                dst=r["dst"],
                action=r["action"],
                size_bytes=int(r["size_bytes"]),
                state=r["state"],
                error=r["error"],
                collision=bool(r["collision"]),
                review_reason=r["review_reason"],
                rule_id=r["rule_id"],
                hash=r["hash"] if "hash" in r.keys() else None,
                tags=json.loads(r["tags_json"])
                if "tags_json" in r.keys() and r["tags_json"]
                else [],
            )
            for r in self.conn.execute(
                "SELECT * FROM plan_items WHERE plan_id=? ORDER BY seq", (plan_id,)
            )
        ]
        return Plan(
            id=int(row["id"]),
            mode=row["mode"],
            root=row["root"],
            state=row["state"],
            items=items,
            summary=json.loads(row["summary_json"]),
            rules=json.loads(row["rules_json"]),
        )

    def list_plans(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM plans ORDER BY id DESC LIMIT ?", (limit,)))

    def set_plan_state(
        self, plan_id: int, state: str, *, timestamp_field: str | None = None
    ) -> None:
        if timestamp_field in {"approved_at", "applied_at", "undone_at"}:
            self.conn.execute(
                f"UPDATE plans SET state=?, {timestamp_field}=? WHERE id=?",
                (state, utcnow(), plan_id),
            )
        else:
            self.conn.execute("UPDATE plans SET state=? WHERE id=?", (state, plan_id))

    def update_plan_summary(self, plan_id: int, summary: dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE plans SET summary_json=? WHERE id=?",
            (json.dumps(summary, ensure_ascii=False), plan_id),
        )

    def set_plan_item_state(
        self, plan_id: int, seq: int, state: str, error: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE plan_items SET state=?, error=? WHERE plan_id=? AND seq=?",
            (state, error, plan_id, seq),
        )

    def add_journal(
        self, plan_id: int, seq: int, action: str, src: str, dst: str, hash_: str | None
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO journal(plan_id, seq, action, src, dst, hash) VALUES (?,?,?,?,?,?)",
            (plan_id, seq, action, src, dst, hash_),
        )
        return _inserted_id(cur)

    def mark_journal_done(self, journal_id: int, *, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE journal SET done_at=?, error=? WHERE id=?",
            (utcnow() if error is None else None, error, journal_id),
        )

    def list_journal(self, plan_id: int, *, undone: bool = False) -> list[sqlite3.Row]:
        if undone:
            return list(
                self.conn.execute(
                    "SELECT * FROM journal WHERE plan_id=? AND undone_at IS NULL ORDER BY seq DESC",
                    (plan_id,),
                )
            )
        return list(
            self.conn.execute("SELECT * FROM journal WHERE plan_id=? ORDER BY seq", (plan_id,))
        )

    def mark_journal_undone(self, journal_id: int, *, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE journal SET undone_at=?, error=? WHERE id=?",
            (utcnow() if error is None else None, error, journal_id),
        )

    # ------------------------------------------------------------------ stats / export
    def stats(self) -> dict[str, Any]:
        def count(table: str) -> int:
            row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            return int(row["n"])

        by_kind = {
            row["kind"]: int(row["n"])
            for row in self.conn.execute("SELECT kind, COUNT(*) AS n FROM assets GROUP BY kind")
        }
        return {
            "assets": count("assets"),
            "files": count("files"),
            "embeddings": count("embeddings"),
            "plans": count("plans"),
            "review_open": int(
                self.conn.execute(
                    "SELECT COUNT(*) AS n FROM review_queue WHERE state='open'"
                ).fetchone()["n"]
            ),
            "by_kind": by_kind,
            "fts5": self.fts5,
            "schema_version": SCHEMA_VERSION,
        }

    def export_json(self, limit: int = 1000) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for asset in self.list_assets(limit=limit):
            files = self.files_for_asset(int(asset["id"]))
            result.append(
                {
                    "asset": dict(asset),
                    "files": [dict(f) for f in files],
                    "tags": [dict(t) for t in self.tags_for_asset(int(asset["id"]))],
                }
            )
        return result

    def iter_asset_ids(self, kind: str | None = None) -> Iterable[int]:
        if kind:
            rows = self.conn.execute("SELECT id FROM assets WHERE kind=? ORDER BY id", (kind,))
        else:
            rows = self.conn.execute("SELECT id FROM assets ORDER BY id")
        for row in rows:
            yield int(row["id"])

    def delete_asset(
        self, asset_id: int
    ) -> None:  # pragma: no cover - используется тестами/сбросом
        self.conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
