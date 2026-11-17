"""Исполнение утверждённого плана и undo (FR-2.3/2.5/2.6, SEC-13).

Инварианты:
- единственный путь к ФС-мутациям — утверждённый план;
- journal пишется до действия (write-ahead);
- оригинал при copy не меняется, при move удаляется только после verify;
- undo проверяет неизменность файла и не удаляет безвозвратно.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog.repository import Catalog
from .config import Settings
from .errors import ExecutionError, PathSafetyError
from .hashing import hash_file, verify_hash
from .security.paths import validate_destination


@dataclass(slots=True)
class ExecutionReport:
    plan_id: int
    state: str = "applied"
    completed: int = 0
    skipped: int = 0
    errors: int = 0
    bytes_copied: int = 0
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "state": self.state,
            "completed": self.completed,
            "skipped": self.skipped,
            "errors": self.errors,
            "bytes_copied": self.bytes_copied,
            "messages": list(self.messages),
        }


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:  # pragma: no cover - Windows
        pass


def _copy_verify(src: Path, dst: Path, expected_hash: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{dst.name}.kadr-tmp-{uuid.uuid4().hex}"
    try:
        with open(src, "rb") as source, open(tmp, "wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        shutil.copystat(src, tmp)
        if not verify_hash(tmp, expected_hash):
            raise ExecutionError(f"verification failed after copy: {src} -> {dst}")
        if dst.exists():
            if verify_hash(dst, expected_hash):
                _fsync_dir(dst.parent)
                return
            raise ExecutionError(f"destination exists with different content: {dst}")
        os.replace(tmp, dst)
        _fsync_dir(dst.parent)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _unlink_source(src: Path, dst: Path) -> None:
    try:
        os.unlink(src)
    except OSError as exc:
        raise ExecutionError(f"dst создан, но src не удалён: {src}: {exc}") from exc


def _move_verify(src: Path, dst: Path, expected_hash: str) -> None:
    # Cross-device move = copy + verify + unlink (никогда не rename до verify).
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.parent / f".{dst.name}.kadr-tmp-{uuid.uuid4().hex}"
    try:
        with open(src, "rb") as source, open(tmp, "wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        shutil.copystat(src, tmp)
        if not verify_hash(tmp, expected_hash):
            raise ExecutionError(f"verification failed after move copy: {src}")
        if dst.exists():
            if verify_hash(dst, expected_hash):
                # Идемпотентность: фиксируем каталог до удаления источника.
                _fsync_dir(dst.parent)
                _unlink_source(src, dst)
                return
            raise ExecutionError(f"destination exists with different content: {dst}")
        os.replace(tmp, dst)
        _fsync_dir(dst.parent)
        if not verify_hash(dst, expected_hash):
            raise ExecutionError(f"verification failed after rename: {dst}")
        _unlink_source(src, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _hardlink_verify(src: Path, dst: Path, expected_hash: str) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if verify_hash(dst, expected_hash):
            return
        raise ExecutionError(f"destination exists with different content: {dst}")
    os.link(src, dst)
    if not verify_hash(dst, expected_hash):
        raise ExecutionError(f"verification failed after hardlink: {dst}")


def apply_plan(
    catalog: Catalog,
    plan_id: int,
    *,
    settings: Settings | None = None,
    force: bool = False,
) -> ExecutionReport:
    plan = catalog.get_plan(plan_id)
    if plan is None:
        raise ExecutionError(f"План {plan_id} не найден")
    if plan.state in {"applied", "applied_with_errors", "undone", "partially_undone"}:
        raise ExecutionError(f"План {plan_id} уже в состоянии {plan.state}")
    settings = settings or Settings()
    root = Path(plan.root)
    report = ExecutionReport(plan_id=plan_id)
    cleanup_temp_files(root)
    catalog.set_plan_state(plan_id, "approved", timestamp_field="approved_at")
    catalog.set_plan_state(plan_id, "applying")

    try:
        for item in plan.items:
            if item.state in {"skipped_collision", "skipped"}:
                report.skipped += 1
                catalog.set_plan_item_state(plan_id, item.seq, "skipped", "collision")
                continue
            if item.state == "review" and not force:
                report.skipped += 1
                catalog.set_plan_item_state(plan_id, item.seq, "review", "requires human review")
                continue
            if item.state == "done" and item.hash and Path(item.dst).is_file() and verify_hash(item.dst, item.hash):
                # Возобновление после прерывания: уже выполненный элемент не повторяем.
                report.completed += 1
                continue
            src = Path(item.src)
            try:
                if not src.is_file():
                    raise ExecutionError(f"source not found: {src}")
                dst = validate_destination(root, item.dst)
                source_hash = hash_file(src)
                if item.hash and source_hash != item.hash:
                    raise ExecutionError(
                        "источник изменился после построения плана; требуется новый dry-run"
                    )
                expected_hash = item.hash or source_hash
                already_done = dst.exists() and _already_done(dst, expected_hash)
                if already_done:
                    report.completed += 1
                    catalog.set_plan_item_state(plan_id, item.seq, "done")
                    continue
                journal_id = catalog.add_journal(plan_id, item.seq, plan.mode, str(src), str(dst), expected_hash)
                try:
                    if item.action == "copy":
                        _copy_verify(src, dst, expected_hash)
                        report.bytes_copied += item.size_bytes
                    elif item.action == "move":
                        _move_verify(src, dst, expected_hash)
                        report.bytes_copied += item.size_bytes
                    elif item.action == "hardlink":
                        _hardlink_verify(src, dst, expected_hash)
                    else:
                        raise ExecutionError(f"unknown action: {item.action}")
                except Exception as exc:
                    # Если dst уже создан и совпадает по хэшу, фиксируем его в журнале:
                    # undo обязан уметь убрать частичный результат.
                    if not already_done and dst.exists() and _already_done(dst, expected_hash):
                        catalog.mark_journal_done(journal_id)
                    else:
                        catalog.mark_journal_done(journal_id, error=str(exc))
                    raise
                catalog.mark_journal_done(journal_id)
                _apply_rule_tags(catalog, item)
                catalog.set_plan_item_state(plan_id, item.seq, "done")
                report.completed += 1
            except (OSError, ExecutionError, PathSafetyError, ValueError) as exc:
                report.errors += 1
                report.messages.append(f"{item.seq}: {exc}")
                catalog.set_plan_item_state(plan_id, item.seq, "error", str(exc))
                catalog.add_review(
                    item.asset_id or None,
                    "apply_error",
                    severity="high",
                    payload={"src": item.src, "dst": item.dst, "error": str(exc)},
                )
    except BaseException:
        # KeyboardInterrupt/kill -9: оставляем план в состоянии, из которого undo
        # восстановит частично выполненные операции по журналу.
        catalog.set_plan_state(plan_id, "interrupted")
        summary = dict(plan.summary)
        summary["execution"] = {**report.to_dict(), "state": "interrupted"}
        catalog.update_plan_summary(plan_id, summary)
        raise

    report.state = "applied" if report.errors == 0 else "applied_with_errors"
    catalog.set_plan_state(plan_id, report.state, timestamp_field="applied_at")
    summary = dict(plan.summary)
    summary["execution"] = report.to_dict()
    catalog.update_plan_summary(plan_id, summary)
    return report


def _already_done(dst: Path, expected_hash: str) -> bool:
    return dst.is_file() and verify_hash(dst, expected_hash)


def _apply_rule_tags(catalog: Catalog, item: Any) -> None:
    if not item.asset_id or not item.tags:
        return
    for tag in item.tags:
        catalog.add_asset_tag(item.asset_id, str(tag), source="rule", confidence=None)


def cleanup_temp_files(root: str | Path, *, older_than_seconds: float = 3600.0) -> int:
    """Удаляет оставшиеся `.kadr-tmp-*` после kill -9/сбоя.

    Не трогает свежие файлы (возможна параллельная операция) и не выходит за root.
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return 0
    removed = 0
    now = time.time()
    for pattern in ("*.kadr-tmp-*", ".kadr-embed-*"):
        for path in root_path.rglob(pattern):
            try:
                if not path.is_file():
                    continue
                if now - path.stat().st_mtime < older_than_seconds:
                    continue
                if path.resolve().parent != root_path and root_path not in path.resolve().parents:
                    continue
                path.unlink()
                removed += 1
            except OSError:
                continue
    return removed


def _trash_path(settings: Settings, plan_id: int, dst: Path) -> Path:
    target = settings.trash_dir / f"plan-{plan_id}" / f"{uuid.uuid4().hex}-{dst.name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _move_to_trash(settings: Settings, plan_id: int, dst: Path, expected_hash: str | None = None) -> Path:
    target = _trash_path(settings, plan_id, dst)
    try:
        os.replace(dst, target)
    except OSError:
        # Другой том: копируем в temp, верифицируем и только затем убираем dst.
        tmp = target.with_name(target.name + ".tmp")
        try:
            shutil.copy2(dst, tmp)
            if expected_hash and not verify_hash(tmp, expected_hash):
                raise ExecutionError(f"копия в корзине повреждена: {tmp}")
            os.replace(tmp, target)
            _fsync_dir(target.parent)
            dst.unlink()
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    if expected_hash and not verify_hash(target, expected_hash):
        raise ExecutionError(f"копия в корзине повреждена: {target}")
    return target


def undo_plan(
    catalog: Catalog,
    plan_id: int,
    *,
    settings: Settings | None = None,
) -> ExecutionReport:
    plan = catalog.get_plan(plan_id)
    if plan is None:
        raise ExecutionError(f"План {plan_id} не найден")
    allowed = {"applied", "applied_with_errors", "partially_undone", "interrupted", "approved", "applying"}
    if plan.state not in allowed:
        raise ExecutionError(f"План {plan_id} нельзя откатить из состояния {plan.state}")
    settings = settings or Settings()
    cleanup_temp_files(Path(plan.root))
    report = ExecutionReport(plan_id=plan_id, state="undone")
    rows = catalog.list_journal(plan_id, undone=True)
    by_seq = {item.seq: item for item in plan.items}
    if not rows:
        # Прерывание до первого журнального действия или пустой план.
        catalog.set_plan_state(plan_id, "undone", timestamp_field="undone_at")
        return report
    for row in rows:
        if row["undone_at"] is not None:
            continue
        action = row["action"]
        src = Path(row["src"])
        dst = Path(row["dst"])
        expected = row["hash"] or ""
        item = by_seq.get(int(row["seq"]))
        try:
            if not dst.exists():
                # Действие не было завершено или уже отменено. Для move проверяем,
                # что оригинал на месте; иначе это потеря, а не no-op.
                if action == "move" and not src.exists():
                    raise ExecutionError(f"источник и dst отсутствуют: {src}")
                catalog.mark_journal_undone(int(row["id"]))
                if item:
                    catalog.set_plan_item_state(plan_id, item.seq, "undone", "dst already missing")
                report.skipped += 1
                continue
            if expected and not verify_hash(dst, expected):
                raise ExecutionError(f"файл изменился после операции, откат запрещён: {dst}")
            if action in {"copy", "hardlink"}:
                _move_to_trash(settings, plan_id, dst, expected)
                report.completed += 1
            elif action == "move":
                if src.exists():
                    if expected and verify_hash(src, expected):
                        _move_to_trash(settings, plan_id, dst, expected)
                    else:
                        raise ExecutionError(f"конфликт при восстановлении: {src} уже существует")
                else:
                    src.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.replace(dst, src)
                    except OSError:
                        shutil.move(str(dst), str(src))
                report.completed += 1
            else:
                raise ExecutionError(f"unknown journal action: {action}")
            catalog.mark_journal_undone(int(row["id"]))
            if item:
                catalog.set_plan_item_state(plan_id, item.seq, "undone")
        except (OSError, ExecutionError, ValueError) as exc:
            report.errors += 1
            report.messages.append(f"undo {row['seq']}: {exc}")
            catalog.mark_journal_undone(int(row["id"]), error=str(exc))
            if item:
                catalog.set_plan_item_state(plan_id, item.seq, "undo_error", str(exc))
    # Невыполненные элементы без журнала тоже считаем отменёнными.
    for item in plan.items:
        if item.state in {"pending", "review", "skipped_collision"}:
            catalog.set_plan_item_state(plan_id, item.seq, "undone", "not executed")
    report.state = "undone" if report.errors == 0 else "partially_undone"
    catalog.set_plan_state(plan_id, report.state, timestamp_field="undone_at")
    summary = dict(plan.summary)
    summary["undo"] = report.to_dict()
    catalog.update_plan_summary(plan_id, summary)
    return report


def execution_report(catalog: Catalog, plan_id: int) -> dict[str, Any]:
    plan = catalog.get_plan(plan_id)
    if plan is None:
        raise ExecutionError(f"План {plan_id} не найден")
    return {
        "plan": {
            "id": plan.id,
            "state": plan.state,
            "mode": plan.mode,
            "root": plan.root,
            "summary": plan.summary,
        },
        "items": [
            {
                "seq": item.seq,
                "asset_id": item.asset_id,
                "src": item.src,
                "dst": item.dst,
                "action": item.action,
                "state": item.state,
                "error": item.error,
                "rule_id": item.rule_id,
                "hash": item.hash,
                "tags": list(item.tags),
            }
            for item in plan.items
        ],
        "journal": [dict(row) for row in catalog.list_journal(plan_id)],
    }


def export_report(catalog: Catalog, plan_id: int, path: str | Path, *, fmt: str = "json") -> Path:
    report = execution_report(catalog, plan_id)
    target = Path(path)
    if fmt == "csv":
        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["seq", "asset_id", "src", "dst", "action", "state", "error", "rule_id"],
            )
            writer.writeheader()
            writer.writerows(report["items"])
    else:
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return target


__all__ = ["ExecutionReport", "apply_plan", "cleanup_temp_files", "execution_report", "export_report", "undo_plan"]
