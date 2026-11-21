"""CLI Kadr: scan | analyze | plan | apply | undo | search | bench | doctor."""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __product_name__, __version__
from .ai.classify import analyze_catalog
from .ai.embeddings import embed_query, get_embedding_backend
from .ai.vlm import NightlyVLMPlanner
from .catalog.repository import Catalog
from .config import Settings
from .executor import apply_plan, undo_plan
from .hashing import hash_file
from .licensing import validate_manifest_file
from .planner import build_plan, render_tree
from .profiles import benchmark_payload, detect_profile
from .rules.dsl import dump_rules, load_rules, load_rules_dict
from .rules.presets import PRESETS, get_preset
from .scanner import scan_root
from .search import execute_search, rebuild_index
from .security.sandbox import load_policy
from .vector.factory import create_vector_index


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env(
        home=args.home or os.environ.get("KADR_HOME") or str(Path.home() / ".kadr"),
        catalog_path=args.catalog,
        library_root=args.library_root,
        profile_override=args.profile,
        vector_backend_override=args.vector_backend,
    )
    settings.ensure_dirs()
    return settings


def _catalog(settings: Settings) -> Catalog:
    return Catalog(settings.db_path)


def _print(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(value, (dict, list)):
                    print(f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}")
                else:
                    print(f"{key}: {value}")
        elif isinstance(payload, list):
            for item in payload:
                print(json.dumps(item, ensure_ascii=False, default=str))
        else:
            print(payload)


def cmd_init(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    payload = {
        "home": str(settings.home),
        "catalog": str(settings.catalog_path),
        "library_root": str(settings.library_root) if settings.library_root else None,
        "schema": catalog.stats()["schema_version"],
        "fts5": catalog.fts5,
    }
    catalog.close()
    _print(payload, args.json)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    payloads = []
    try:
        for root in args.paths:
            report = scan_root(catalog, root, settings)
            payloads.append(report.to_dict())
    finally:
        catalog.close()
    if args.json:
        _print(payloads if len(payloads) > 1 else payloads[0], True)
    else:
        for payload in payloads:
            print(
                f"Просканировано: {payload['root']} — файлов {payload['scanned']}, "
                f"ассетов +{payload['added_assets']}, пропущено без изменений {payload['skipped_unchanged']}, "
                f"групп {payload['groups']}, ошибок {len(payload['errors'])}"
            )
            for error in payload["errors"][:20]:
                print(f"  ! {error}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    payload = analyze_catalog(catalog, limit=args.limit, ocr_language=settings.ocr_language)
    catalog.close()
    _print(payload, args.json)
    return 0


def _load_rules(args: argparse.Namespace):
    if args.rules:
        return load_rules(args.rules, allow_free_text=bool(args.allow_free_text_paths))
    if args.preset:
        return load_rules_dict(
            get_preset(args.preset), allow_free_text=bool(args.allow_free_text_paths)
        )
    raise SystemExit("Укажите --rules FILE или --preset NAME")


def cmd_plan(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    ruleset = _load_rules(args)
    root = (
        args.root
        or (str(settings.library_root) if settings.library_root else None)
        or ruleset.library_root
    )
    if not root:
        raise SystemExit("Укажите --root (library_root)")
    plan = build_plan(
        catalog,
        ruleset,
        root,
        profile=detect_profile(settings.profile_override),
        allow_free_text_paths=args.allow_free_text_paths,
        case_insensitive=bool(settings.case_insensitive_fs),
        limit=args.limit,
    )
    payload = {
        "plan_id": plan.id,
        "state": plan.state,
        "mode": plan.mode,
        "root": plan.root,
        "summary": plan.summary,
        "tree": render_tree(plan) if args.tree else None,
    }
    if args.json:
        _print(payload, True)
    else:
        print(f"План #{plan.id}: {plan.mode} → {plan.root} ({plan.state})")
        print(
            f"Файлов: {plan.summary['items']}, ассетов: {plan.summary['assets']}, "
            f"байт: {plan.summary['bytes']}, коллизий: {plan.summary['collisions']}"
        )
        print(f"Состояния: {plan.summary['states']}")
        if plan.summary.get("unavailable_rules"):
            print("Правила, недоступные на профиле (не блокируют раскладку):")
            for rule_id, reason in plan.summary["unavailable_rules"].items():
                print(f"  - {rule_id}: {reason}")
        for warning in plan.summary.get("warnings", []):
            print(f"  ! {warning}")
        for error in plan.summary.get("errors", []):
            print(f"  ✗ {error}")
        if args.tree:
            print()
            print(render_tree(plan))
    catalog.close()
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    report = apply_plan(catalog, args.plan_id, settings=settings, force=args.force)
    payload = report.to_dict()
    catalog.close()
    _print(payload, args.json)
    return 0 if report.errors == 0 else 2


def cmd_undo(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    report = undo_plan(catalog, args.plan_id, settings=settings)
    payload = report.to_dict()
    catalog.close()
    _print(payload, args.json)
    return 0 if report.errors == 0 else 2


def cmd_search(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    result = execute_search(catalog, " ".join(args.query), k=args.k, backend=args.vector_backend)
    payload = result.to_dict()
    if args.json:
        _print(payload, True)
    else:
        print(f"Индекс: {result.index_badge}")
        for hit in result.hits:
            fields = hit.fields
            print(
                f"#{hit.asset_id} score={hit.score:.3f} {hit.reason} "
                f"{fields.get('taken_at') or '—'} {fields.get('path') or '—'}"
            )
        if not result.hits:
            print("Ничего не найдено.")
    catalog.close()
    return 0


def cmd_reindex(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    payload = rebuild_index(catalog, backend=args.vector_backend)
    catalog.close()
    _print(payload, args.json)
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    settings = _settings(args)
    profile = detect_profile(settings.profile_override)
    payload: dict[str, Any] = benchmark_payload(profile)
    if args.path:
        catalog = _catalog(settings)
        started = time.perf_counter()
        report = scan_root(catalog, args.path, settings)
        scan_seconds = time.perf_counter() - started
        files = max(1, report.scanned)
        payload["scan"] = {
            "root": str(args.path),
            "files": report.scanned,
            "seconds": round(scan_seconds, 3),
            "files_per_second": round(files / scan_seconds, 1) if scan_seconds else None,
            "added_assets": report.added_assets,
        }
        started = time.perf_counter()
        analyzed = analyze_catalog(
            catalog, limit=args.limit, ocr_language=settings.ocr_language
        )
        analyze_seconds = time.perf_counter() - started
        payload["analyze"] = {
            **analyzed,
            "seconds": round(analyze_seconds, 3),
            "photos_per_second": round(analyzed["processed"] / analyze_seconds, 1)
            if analyze_seconds and analyzed["processed"]
            else None,
        }
        db_path = settings.db_path
        payload["storage"] = {
            "catalog_bytes": db_path.stat().st_size if db_path.exists() else 0,
        }
        catalog.close()
    else:
        # Микробенчмарк без файлов: хэширование и запрос эмбеддинга.
        sample = b"kadr-bench-" * 100_000
        tmp = settings.home / "bench-sample.bin"
        tmp.write_bytes(sample)
        try:
            hash_times = []
            for _ in range(5):
                start = time.perf_counter()
                hash_file(tmp)
                hash_times.append((time.perf_counter() - start) * 1000)
            vec_times = []
            backend = get_embedding_backend()
            for _ in range(20):
                start = time.perf_counter()
                embed_query("закат на пляже с собакой", backend)
                vec_times.append((time.perf_counter() - start) * 1000)
            payload["micro"] = {
                "hash_ms_median": round(statistics.median(hash_times), 3),
                "embed_text_ms_median": round(statistics.median(vec_times), 3),
                "sample_bytes": len(sample),
            }
        finally:
            tmp.unlink(missing_ok=True)
    _print(payload, True)
    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


def cmd_worker_parser(args: argparse.Namespace) -> int:
    """Скрытая команда для PyInstaller-бандла: те же обязанности, что у
    python -m kadr.workers.parser_worker (спайк C). В dev-режиме не используется."""
    from .workers.parser_worker import main as worker_main

    return worker_main()


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    profile = detect_profile(settings.profile_override)
    manifest = validate_manifest_file(target_profile=profile.profile)
    policy = load_policy()
    index = create_vector_index(catalog, backend=args.vector_backend)
    stats = catalog.stats()
    # Снимок ДО close(): после закрытия соединения чтение падало с
    # "Cannot operate on a closed database" (найден на one-dir-бандле, спайк C).
    index_status = index.status()
    fts5 = catalog.fts5
    payload = {
        "product": __product_name__,
        "version": __version__,
        "python": platform.python_version(),
        "os": platform.platform(),
        "home": str(settings.home),
        "catalog": str(settings.catalog_path),
        "catalog_stats": stats,
        "profile": profile.to_dict(),
        "vector_index": index_status.to_dict(),
        "models": manifest.to_dict(),
        "sandbox": {"hash": policy.policy_hash(), "parity": policy.parity()},
        "fts5": fts5,
        "process": {
            "pid": os.getpid(),
            "process_group": os.getpgrp() if hasattr(os, "getpgrp") else None,
        },
        "known_limitations": "KNOWN_LIMITATIONS.md",
    }
    if args.orphan_check:
        payload["orphan_check"] = _orphan_check()
    catalog.close()
    if args.json:
        _print(payload, True)
    else:
        print(
            f"{__product_name__} {__version__} | {profile.profile} | RAM {profile.ram_gb} ГБ | cores {profile.cores}"
        )
        print(
            f"Каталог: {settings.catalog_path} (assets={stats['assets']}, files={stats['files']}, embeddings={stats['embeddings']})"
        )
        print(f"FTS5: {'да' if fts5 else 'НЕТ — текстовый поиск ограничен'}")
        print(
            f"Векторный индекс: {index_status.backend} / {index_status.status} ({index_status.vector_count} векторов)"
        )
        if index_status.reason:
            print(f"  причина: {index_status.reason}")
        print(
            f"Модели: включено {manifest.enabled_count}; issues {len(manifest.issues)}; warnings {len(manifest.warnings)}"
        )
        for issue in manifest.issues:
            print(f"  ✗ {issue}")
        for warning in manifest.warnings:
            print(f"  ! {warning}")
        print(f"Песочница: hash {policy.policy_hash()[:12]}, parity={policy.parity()['equal']}")
        print("Известные ограничения: KNOWN_LIMITATIONS.md")
    return 0


def _orphan_check() -> dict[str, Any]:
    """Проверка процессов-потомков текущего движка (для lifecycle-теста)."""
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid,ppid,pgid,comm"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode != 0:
            return {"available": False}
        current = os.getpid()
        children = []
        for line in completed.stdout.splitlines()[1:]:
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            pid, ppid, pgid, comm = int(parts[0]), int(parts[1]), int(parts[2]), parts[3]
            if ppid == current and pid != current:
                children.append({"pid": pid, "ppid": ppid, "pgid": pgid, "comm": comm})
        return {"available": True, "pid": current, "children": children, "orphans": []}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"available": False}


def cmd_nightly(args: argparse.Namespace) -> int:
    settings = _settings(args)
    catalog = _catalog(settings)
    profile = detect_profile(settings.profile_override)
    planner = NightlyVLMPlanner(catalog, profile)
    count = args.count or catalog.count_assets()
    plan = planner.plan(
        count,
        explicit=args.explicit,
        ac_power=not args.battery,
        free_ram_gb=args.free_ram_gb,
        free_disk_gb=args.free_disk_gb,
        selected=args.selected,
    )
    payload = plan.to_dict()
    if args.create_job and plan.available:
        payload["job_id"] = planner.create_job(plan)
    catalog.close()
    _print(payload, args.json)
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    if args.action == "list":
        _print(sorted(PRESETS), args.json)
        return 0
    if args.action == "export":
        if not args.name:
            raise SystemExit("Укажите имя пресета")
        data = get_preset(args.name)
        ruleset = load_rules_dict(data)
        if args.output:
            dump_rules(ruleset, args.output)
            print(f"Записано: {args.output}")
        else:
            _print(data, True)
        return 0
    raise SystemExit("Используйте: kadr rules list | export NAME --output FILE")


def cmd_serve(args: argparse.Namespace) -> int:
    settings = _settings(args)
    try:
        import uvicorn

        from .api import create_app
    except Exception as exc:
        raise SystemExit(f"Для serve нужны fastapi/uvicorn: {exc}") from exc
    app = create_app(settings)

    if args.sidecar:
        import socket
        import threading
        import urllib.request

        from .lifecycle import Watchdog, handshake_payload

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])

        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level=args.log_level)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Watchdog: самозавершение при смерти родителя (Linux/macOS — getppid;
        # Windows — Job Object kill-on-close у Rust-хоста).
        Watchdog(stdin=sys.stdin).start()

        url = f"http://127.0.0.1:{port}/health"
        deadline = time.monotonic() + 15.0
        ready = False
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310 - localhost
                    if response.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.1)
        if not ready:
            raise SystemExit("sidecar server did not become ready")

        # stdout-handshake — единственный канал порта/токена/CSRF (не URL, не логи).
        csrf_token = app.state.security.csrf_token
        print(handshake_payload(port, settings.api_token, os.getpid(), csrf_token=csrf_token), flush=True)

        # Graceful shutdown: POST /internal/shutdown ставит флаг → сервер мягко останавливается.
        while not app.state.shutdown_requested:
            time.sleep(0.1)
        server.should_exit = True
        thread.join(timeout=5)
        return 0

    if args.print_token:
        print(f"KADR_API_TOKEN={settings.api_token}", file=sys.stderr)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level=args.log_level)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kadr", description=f"{__product_name__} — локальный раскладчик медиаархива"
    )
    parser.add_argument("--home", help="Каталог данных Kadr (по умолчанию ~/.kadr)")
    parser.add_argument("--catalog", help="Путь к файлу каталога SQLite")
    parser.add_argument("--library-root", help="Корень библиотеки для планов")
    parser.add_argument(
        "--profile",
        choices=["lite-forced", "lite", "balanced", "pro", "pro-plus"],
        help="Переопределить профиль",
    )
    parser.add_argument(
        "--vector-backend",
        choices=["sqlite-vec", "ann", "hybrid", "brute-force"],
        help="Векторный бэкенд",
    )
    parser.add_argument("--json", action="store_true", help="JSON-вывод")
    parser.add_argument("--version", action="version", version=f"{__product_name__} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Инициализировать каталог и каталоги данных")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("scan", help="Просканировать папки и проиндексировать метаданные")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("analyze", help="AI-lite: OCR, zero-shot теги, эмбеддинги")
    p.add_argument("--limit", type=int)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("plan", help="Dry-run: построить план раскладки")
    p.add_argument("--rules", help="Файл правил YAML/JSON")
    p.add_argument("--preset", choices=sorted(PRESETS), help="Готовый пресет правил")
    p.add_argument("--root", help="library_root")
    p.add_argument("--limit", type=int)
    p.add_argument("--tree", action="store_true", help="Показать дерево «что куда»")
    p.add_argument("--allow-free-text-paths", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", help="Применить план (copy/move/hardlink)")
    p.add_argument("plan_id", type=int)
    p.add_argument("--force", action="store_true", help="Применить и элементы review")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("undo", help="Откатить план")
    p.add_argument("plan_id", type=int)
    p.set_defaults(func=cmd_undo)

    p = sub.add_parser("search", help="Поиск: структурный, FTS, семантика")
    p.add_argument("query", nargs="+")
    p.add_argument("-k", type=int, default=20)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("reindex", help="Пересобрать векторный индекс без пересчёта эмбеддингов")
    p.set_defaults(func=cmd_reindex)

    p = sub.add_parser("bench", help="Бенчмарк-харнесс")
    p.add_argument("path", nargs="?")
    p.add_argument("--limit", type=int)
    p.add_argument("--output", help="Записать JSON-результат")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("doctor", help="Состояние системы, индекса, лицензий и процессов")
    p.add_argument("--orphan-check", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "worker-parser",
        help=argparse.SUPPRESS,
        description="Внутренний worker (one-dir-бандл); не вызывать напрямую",
    )
    p.set_defaults(func=cmd_worker_parser)
    p = sub.add_parser("status", help="Краткий статус (алиас doctor)")
    p.add_argument("--orphan-check", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("nightly", help="Планировщик ночной VLM-задачи (experimental)")
    p.add_argument("--count", type=int)
    p.add_argument(
        "--explicit",
        action="store_true",
        help="Подтвердить явный запуск (без флага задача недоступна)",
    )
    p.add_argument("--battery", action="store_true", help="Имитировать запуск на батарее")
    p.add_argument("--free-ram-gb", type=float, default=8.0)
    p.add_argument("--free-disk-gb", type=float, default=10.0)
    p.add_argument("--selected", action="store_true", help="Только выделенные фото")
    p.add_argument("--create-job", action="store_true")
    p.set_defaults(func=cmd_nightly)

    p = sub.add_parser("rules", help="Пресеты правил: list/export")
    p.add_argument("action", choices=["list", "export"])
    p.add_argument("name", nargs="?")
    p.add_argument("--output")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("serve", help="Запустить локальный API (127.0.0.1)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--log-level", default="info")
    p.add_argument("--print-token", action="store_true")
    p.add_argument(
        "--sidecar",
        action="store_true",
        help="Режим sidecar: случайный порт + stdout-handshake для Tauri-оболочки (§11.2)",
    )
    p.set_defaults(func=cmd_serve)
    return parser


def _configure_stdio() -> None:
    """UTF-8-вывод CLI: русский текст и «→» роняли cp1251-консоль Windows (NFR-14)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - exotic consoles
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("Остановлено.", file=sys.stderr)
        return 130
    except Exception as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False))
        else:
            print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
