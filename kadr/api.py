"""Локальный HTTP API Kadr (FastAPI), только 127.0.0.1 + bearer + CSRF.

Модуль импортируется лениво: CLI и доменное ядро работают без FastAPI.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from . import __product_name__, __version__
from .ai.classify import analyze_catalog
from .ai.personalize import rerank_by_rating
from .ai.vlm import NightlyVLMPlanner
from .catalog.repository import Catalog
from .config import Settings
from .errors import ExecutionError
from .executor import apply_plan, execution_report, undo_plan
from .licensing import validate_manifest_file
from .metadata.serialization import metadata_to_payload
from .planner import build_plan, plan_tree, render_tree
from .profiles import detect_profile
from .rules.dsl import load_rules_dict
from .rules.presets import get_preset
from .scanner import scan_root
from .search import execute_search, semantic_status
from .security.api_auth import ApiSecurity
from .security.paths import PathSafetyError
from .security.sandbox import load_policy
from .vector.factory import create_vector_index
from .workers.client import extract_metadata_sandboxed


class ScanRequest(BaseModel):
    root: str
    limit: int | None = None


class AnalyzeRequest(BaseModel):
    limit: int | None = None
    run_ocr_enabled: bool = True


class SearchRequest(BaseModel):
    query: str = ""
    k: int = Field(default=50, ge=1, le=500)
    backend: str | None = None


class PlanRequest(BaseModel):
    rules: dict[str, Any] | None = None
    preset: str | None = None
    library_root: str | None = None
    limit: int | None = None
    allow_free_text_paths: bool | None = None


class ApplyRequest(BaseModel):
    force: bool = False


class NightlyRequest(BaseModel):
    count: int = Field(default=0, ge=0)
    explicit: bool = True
    ac_power: bool = True
    free_ram_gb: float = 8.0
    free_disk_gb: float = 10.0
    selected: bool = False
    create_job: bool = False


def _headers(request: Request) -> dict[str, str]:
    return {key.lower(): value for key, value in request.headers.items()}


def _rerank_hits(hits: list[dict[str, Any]], catalog: Catalog) -> list[dict[str, Any]]:
    """FR-3.12: реранкинг результатов по рейтингам пользователя (assets.rating)."""
    ratings: dict[int, float] = {}
    for h in hits:
        row = catalog.conn.execute("SELECT rating FROM assets WHERE id=?", (h["asset_id"],)).fetchone()
        if row and row["rating"] is not None:
            ratings[int(h["asset_id"])] = float(row["rating"])
    if not ratings:
        return hits
    reranked = rerank_by_rating([(int(h["asset_id"]), float(h["score"])) for h in hits], ratings)
    by_id = {h["asset_id"]: h for h in hits}
    return [by_id[aid] for aid, _ in reranked]


def create_app(
    settings: Settings | None = None,
    catalog: Catalog | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    catalog = catalog or Catalog(settings.db_path)
    if settings.vector_backend_override:
        catalog.set_setting("vector_backend_override", settings.vector_backend_override)
    profile = detect_profile(settings.profile_override)
    security = ApiSecurity(token=settings.api_token)
    app = FastAPI(
        title=f"{__product_name__} local API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.catalog = catalog
    app.state.profile = profile
    app.state.security = security
    app.state.shutdown_requested = False
    # Одно sqlite-соединение: сериализуем API-доступ, иначе BEGIN/COMMIT
    # конфликтуют в threadpool. Для локального API это дешёвая и предсказуемая модель.
    app.state.db_lock = asyncio.Lock()

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if request.url.path in {"/health", "/", "/ui", "/ui/ui.js", "/ui/ui.css", "/favicon.ico"}:
            return await call_next(request)
        if request.method.upper() == "OPTIONS":
            return JSONResponse(status_code=403, content={"detail": "CORS is closed"})
        verdict = security.authorize(
            method=request.method,
            headers=_headers(request),
            host=request.headers.get("host"),
        )
        if verdict is not None:
            status, detail = verdict
            return JSONResponse(status_code=status, content={"detail": detail})
        if request.url.path.startswith(("/v1/", "/internal/")):
            async with request.app.state.db_lock:
                return await call_next(request)
        return await call_next(request)

    def state(request: Request):
        return request.app.state

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        return {
            "status": "ok",
            "product": __product_name__,
            "version": __version__,
            "schema": state(request).catalog.stats()["schema_version"],
        }

    static_dir = Path(__file__).with_name("static")

    def _ui_file(name: str, media_type: str) -> FileResponse:
        path = (static_dir / name).resolve()
        if path.parent != static_dir.resolve() or not path.is_file():
            raise HTTPException(status_code=404, detail="ui asset not found")
        return FileResponse(path, media_type=media_type)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        index_path = (static_dir / "index.html").resolve()
        if index_path.parent != static_dir.resolve() or not index_path.is_file():
            raise HTTPException(status_code=404, detail="ui not installed")
        html = index_path.read_text(encoding="utf-8")
        return HTMLResponse(
            html,
            headers={
                "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.get("/ui", response_class=HTMLResponse)
    def ui() -> HTMLResponse:
        return index()

    @app.get("/ui/ui.js")
    def ui_js() -> FileResponse:
        return _ui_file("ui.js", "text/javascript")

    @app.get("/ui/ui.css")
    def ui_css() -> FileResponse:
        return _ui_file("ui.css", "text/css")

    @app.get("/v1/system/status")
    def system_status(request: Request) -> dict[str, Any]:
        app_state = state(request)
        manifest = validate_manifest_file(target_profile=app_state.profile.profile)
        index = create_vector_index(app_state.catalog)
        policy = load_policy()
        return {
            "product": __product_name__,
            "version": __version__,
            "profile": app_state.profile.to_dict(),
            "catalog": str(app_state.settings.catalog_path),
            "stats": app_state.catalog.stats(),
            "vector_index": index.status().to_dict(),
            "models": manifest.to_dict(),
            "sandbox": {"hash": policy.policy_hash(), "parity": policy.parity()},
            "known_limitations": "KNOWN_LIMITATIONS.md",
            "csrf_token": app_state.security.csrf_token,
            "offline": True,
        }

    @app.get("/v1/models")
    def models(request: Request) -> dict[str, Any]:
        return validate_manifest_file(target_profile=state(request).profile.profile).to_dict()

    @app.post("/v1/scan")
    def scan(payload: ScanRequest, request: Request) -> dict[str, Any]:
        app_state = state(request)
        try:
            report = scan_root(app_state.catalog, payload.root, app_state.settings)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PathSafetyError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return report.to_dict()

    @app.post("/v1/analyze")
    def analyze(payload: AnalyzeRequest, request: Request) -> dict[str, Any]:
        app_state = state(request)
        return analyze_catalog(
            app_state.catalog,
            limit=payload.limit,
            run_ocr_enabled=payload.run_ocr_enabled,
            ocr_language=app_state.settings.ocr_language,
        )

    @app.post("/v1/metadata")
    async def metadata_upload(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
        """Метаданные загруженного файла через временный файл; без чтения по пути."""
        app_state = state(request)
        tmp_dir = app_state.settings.home / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(tmp_dir, 0o700)
        except OSError:
            pass
        suffix = Path(file.filename or "upload.bin").suffix or ".bin"
        target = tmp_dir / f"upload-{os.getpid()}-{uuid.uuid4().hex}{suffix}"
        total = 0
        try:
            with target.open("wb") as fh:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise HTTPException(status_code=413, detail="upload too large")
                    fh.write(chunk)
            meta = extract_metadata_sandboxed(target)
            return metadata_to_payload(meta)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            try:
                target.unlink()
            except OSError:
                pass

    @app.get("/v1/assets")
    def assets(
        request: Request, limit: int = 100, offset: int = 0, kind: str | None = None
    ) -> dict[str, Any]:
        catalog_ = state(request).catalog
        rows = catalog_.list_assets(limit=min(max(limit, 1), 500), offset=max(offset, 0), kind=kind)
        result = []
        for row in rows:
            asset_id = int(row["id"])
            primary = catalog_.primary_file(asset_id)
            result.append(
                {
                    **{key: row[key] for key in row.keys()},
                    "path": primary["rel_path"] if primary else None,
                    "tags": [tag["name"] for tag in catalog_.tags_for_asset(asset_id)],
                }
            )
        return {"items": result, "count": len(result)}

    @app.get("/v1/assets/{asset_id}")
    def asset_detail(asset_id: int, request: Request) -> dict[str, Any]:
        catalog_ = state(request).catalog
        asset = catalog_.get_asset(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="asset not found")
        return {
            "asset": dict(asset),
            "files": [dict(row) for row in catalog_.files_for_asset(asset_id)],
            "tags": [dict(row) for row in catalog_.tags_for_asset(asset_id)],
            "ai": [dict(row) for row in catalog_.get_ai_outputs(asset_id)],
        }

    @app.get("/v1/assets/{asset_id}/file")
    def asset_file(asset_id: int, request: Request) -> FileResponse:
        catalog_ = state(request).catalog
        primary = catalog_.primary_file(asset_id)
        if primary is None:
            raise HTTPException(status_code=404, detail="asset has no primary file")
        root = catalog_.volume_root(int(primary["volume_id"]))
        if not root:
            raise HTTPException(status_code=404, detail="volume not found")
        path = (Path(root) / primary["rel_path"]).resolve()
        try:
            path.relative_to(Path(root).resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="path outside volume") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="file missing")
        return FileResponse(path, filename=path.name)

    @app.get("/v1/assets/{asset_id}/thumbnail")
    def asset_thumbnail(asset_id: int, request: Request, size: int = 256) -> Response:
        """Миниатюра (JPEG) для галереи; для не-рендерящихся файлов — 415."""
        catalog_ = state(request).catalog
        primary = catalog_.primary_file(asset_id)
        if primary is None:
            raise HTTPException(status_code=404, detail="asset has no primary file")
        root = catalog_.volume_root(int(primary["volume_id"]))
        if not root:
            raise HTTPException(status_code=404, detail="volume not found")
        path = (Path(root) / primary["rel_path"]).resolve()
        try:
            path.relative_to(Path(root).resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="path outside volume") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="file missing")
        try:
            from PIL import Image
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Pillow unavailable") from exc
        import io

        try:
            with Image.open(path) as img:
                img.thumbnail((size, size))
                buffer = io.BytesIO()
                img.convert("RGB").save(buffer, format="JPEG", quality=80)
                return Response(content=buffer.getvalue(), media_type="image/jpeg")
        except Exception as exc:
            raise HTTPException(status_code=415, detail="not a renderable image") from exc

    @app.get("/v1/assets/{asset_id}/absolute-path")
    def asset_absolute_path(asset_id: int, request: Request) -> dict[str, Any]:
        """Абсолютный путь primary-файла — для «показать в папке» (FR-5.6)."""
        catalog_ = state(request).catalog
        primary = catalog_.primary_file(asset_id)
        if primary is None:
            raise HTTPException(status_code=404, detail="asset has no primary file")
        root = catalog_.volume_root(int(primary["volume_id"]))
        if not root:
            raise HTTPException(status_code=404, detail="volume not found")
        path = (Path(root) / primary["rel_path"]).resolve()
        try:
            path.relative_to(Path(root).resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="path outside volume") from exc
        return {"path": str(path)}

    @app.post("/v1/search")
    def search(payload: SearchRequest, request: Request) -> dict[str, Any]:
        result = execute_search(
            state(request).catalog,
            payload.query,
            k=payload.k,
            backend=payload.backend,
        )
        d = result.to_dict()
        d["hits"] = _rerank_hits(d["hits"], state(request).catalog)
        return d

    @app.get("/v1/search/status")
    def search_status(request: Request, backend: str | None = None) -> dict[str, Any]:
        return semantic_status(state(request).catalog, backend=backend)

    def _rules_from_request(payload: PlanRequest, request: Request):
        if payload.rules is not None:
            return load_rules_dict(
                payload.rules, allow_free_text=bool(payload.allow_free_text_paths)
            )
        if payload.preset:
            return load_rules_dict(
                get_preset(payload.preset), allow_free_text=bool(payload.allow_free_text_paths)
            )
        raise HTTPException(status_code=400, detail="rules or preset is required")

    @app.post("/v1/plans")
    def create_plan(payload: PlanRequest, request: Request) -> dict[str, Any]:
        app_state = state(request)
        ruleset = _rules_from_request(payload, request)
        root = payload.library_root or (
            str(app_state.settings.library_root)
            if app_state.settings.library_root
            else ruleset.library_root
        )
        if not root:
            raise HTTPException(status_code=400, detail="library_root is required")
        plan = build_plan(
            app_state.catalog,
            ruleset,
            root,
            profile=app_state.profile,
            allow_free_text_paths=payload.allow_free_text_paths,
            case_insensitive=bool(app_state.settings.case_insensitive_fs),
            limit=payload.limit,
        )
        return {
            "plan": plan.to_dict(),
            "tree": plan_tree(plan),
            "tree_text": render_tree(plan),
        }

    @app.get("/v1/presets")
    def presets(request: Request) -> dict[str, Any]:
        """Пресеты раскладки (FR-2.11) — для UI «Правила» (safety + rules)."""
        from .rules.presets import PRESETS

        return {"presets": {name: dict(ruleset) for name, ruleset in PRESETS.items()}}

    @app.get("/v1/plans")
    def list_plans(request: Request, limit: int = 50) -> dict[str, Any]:
        return {"items": [dict(row) for row in state(request).catalog.list_plans(limit=limit)]}

    @app.get("/v1/plans/{plan_id}")
    def plan_detail(plan_id: int, request: Request) -> dict[str, Any]:
        try:
            report = execution_report(state(request).catalog, plan_id)
        except (ExecutionError, ValueError, KeyError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        plan = state(request).catalog.get_plan(plan_id)
        return {"report": report, "tree_text": render_tree(plan) if plan else ""}

    @app.post("/v1/plans/{plan_id}/apply")
    def plan_apply(plan_id: int, payload: ApplyRequest, request: Request) -> dict[str, Any]:
        app_state = state(request)
        try:
            report = apply_plan(
                app_state.catalog, plan_id, settings=app_state.settings, force=payload.force
            )
        except (ExecutionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # не отдаём клиенту внутренние пути/стек
            raise HTTPException(status_code=500, detail="apply failed") from exc
        return report.to_dict()

    @app.post("/v1/plans/{plan_id}/undo")
    def plan_undo(plan_id: int, request: Request) -> dict[str, Any]:
        app_state = state(request)
        try:
            report = undo_plan(app_state.catalog, plan_id, settings=app_state.settings)
        except (ExecutionError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # не отдаём клиенту внутренние пути/стек
            raise HTTPException(status_code=500, detail="undo failed") from exc
        return report.to_dict()

    @app.get("/v1/review")
    def review(request: Request, state_filter: str = "open", limit: int = 100) -> dict[str, Any]:
        rows = state(request).catalog.list_review(state=state_filter, limit=min(max(limit, 1), 500))
        return {"items": [dict(row) for row in rows]}

    @app.post("/v1/review/{review_id}/resolve")
    def resolve_review(review_id: int, request: Request) -> dict[str, Any]:
        state(request).catalog.resolve_review(review_id)
        return {"ok": True, "id": review_id}

    @app.get("/v1/jobs")
    def jobs(request: Request, limit: int = 50) -> dict[str, Any]:
        return {"items": [dict(row) for row in state(request).catalog.list_jobs(limit=limit)]}

    @app.post("/v1/jobs/nightly-vlm")
    def nightly_vlm(payload: NightlyRequest, request: Request) -> dict[str, Any]:
        app_state = state(request)
        planner = NightlyVLMPlanner(app_state.catalog, app_state.profile)
        count = payload.count or app_state.catalog.count_assets()
        plan = planner.plan(
            count,
            explicit=payload.explicit,
            ac_power=payload.ac_power,
            free_ram_gb=payload.free_ram_gb,
            free_disk_gb=payload.free_disk_gb,
            selected=payload.selected,
        )
        job_id = None
        if payload.create_job and plan.available:
            job_id = planner.create_job(plan)
        return {"plan": plan.to_dict(), "job_id": job_id, "job_created": job_id is not None}

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: int, request: Request) -> dict[str, Any]:
        job = state(request).catalog.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        state(request).catalog.update_job(job_id, state="cancelled", finished=True)
        return {"ok": True, "job_id": job_id}

    @app.get("/v1/export/catalog")
    def export_catalog(request: Request, limit: int = 1000) -> dict[str, Any]:
        return {"items": state(request).catalog.export_json(limit=min(max(limit, 1), 5000))}

    @app.post("/internal/shutdown")
    def shutdown(request: Request) -> dict[str, Any]:
        state(request).shutdown_requested = True
        return {"ok": True, "shutdown": True}

    return app


# В релизе токен и порт передаются sidecar-handshake (docs/ADR-0005).
# Для uvicorn используйте фабрику: `uvicorn kadr.api:create_app --factory`.
# Автосоздание app при импорте включается только явно, чтобы импорт модуля не
# создавал каталог и не читал ~/.kadr в тестах/CLI.
app = create_app() if os.environ.get("KADR_API_AUTOCREATE") == "1" else None


__all__ = ["create_app"]
