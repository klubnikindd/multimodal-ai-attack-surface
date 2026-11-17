"""Dry-run, план раскладки и предпросмотр дерева (FR-2.4, FR-2.7, FR-2.13)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .catalog.repository import Catalog
from .constants import COLLISION_MODES, SUPPORTED_EXTENSIONS
from .errors import PlanError
from .hashing import hash_file
from .models import Plan, PlanItem, RuleDecision
from .rules.dsl import RuleSet
from .rules.operators import evaluate_rule
from .rules.templates import render_path_template
from .security.paths import safe_join


def _planned_key(path: Path, case_insensitive: bool) -> str:
    text = str(path.resolve()) if path.exists() else str(path.absolute())
    return text.lower() if case_insensitive else text


def _suffix_candidate(dst: Path, index: int) -> Path:
    return dst.with_name(f"{dst.stem} ({index}){dst.suffix}")


def _fallback_decision(
    ruleset: RuleSet,
    context: dict[str, Any],
    *,
    allow_free_text: bool,
    counter: int,
    reason: str,
) -> RuleDecision:
    rendered = render_path_template(
        str(ruleset.fallback["path"]),
        context,
        allow_free_text=allow_free_text,
        counter=counter,
    )
    return RuleDecision(
        rule_id="fallback",
        matched=True,
        path_template=rendered,
        fallback=True,
        reason=reason,
    )


def _decide(
    ruleset: RuleSet,
    context: dict[str, Any],
    *,
    allow_free_text: bool,
    vlm_allowed: bool,
    counter: int,
    unavailable: dict[str, str],
) -> RuleDecision:
    """Выбирает правило для ассета и рендерит каталог назначения один раз."""
    ext_value = str(context.get("ext") or "")
    if context.get("kind") == "other" or (
        ext_value and ("." + ext_value.lstrip(".")) not in SUPPORTED_EXTENSIONS
    ):
        # FR-1.2: неподдержанное → _Unsorted + review, без падения.
        return _fallback_decision(
            ruleset,
            context,
            allow_free_text=allow_free_text,
            counter=counter,
            reason="unsupported_format",
        )
    for rule in ruleset.rules:
        result = evaluate_rule(
            rule,
            context,
            vlm_allowed=vlm_allowed,
            allow_free_text_paths=allow_free_text,
        )
        if result.unavailable_on_profile:
            unavailable[result.rule_id] = result.reason or "unavailable"
            continue
        if result.matched:
            return result
    return _fallback_decision(
        ruleset,
        context,
        allow_free_text=allow_free_text,
        counter=counter,
        reason="fallback",
    )


def resolve_collision(
    dst: Path,
    *,
    mode: str,
    source_hash: str,
    planned: set[str],
    case_insensitive: bool,
) -> tuple[Path, bool, str | None]:
    """Возвращает (путь, collision, reason). Ничего не пишет на диск."""
    if mode not in COLLISION_MODES:
        raise PlanError(f"Неизвестный режим коллизий: {mode}")
    key = _planned_key(dst, case_insensitive)
    exists = dst.exists() or key in planned
    if not exists:
        return dst, False, None
    if mode == "suffix":
        for index in range(1, 10000):
            candidate = _suffix_candidate(dst, index)
            candidate_key = _planned_key(candidate, case_insensitive)
            if not candidate.exists() and candidate_key not in planned:
                return candidate, True, None
        raise PlanError("Не удалось подобрать свободное имя")
    if mode == "hash":
        if not source_hash:
            raise PlanError("нет хэша для разрешения hash-коллизии")
        suffix = source_hash.removeprefix("blake3:").removeprefix("sha256:")[:8]
        candidate = dst.with_name(f"{dst.stem}-{suffix}{dst.suffix}")
        candidate_key = _planned_key(candidate, case_insensitive)
        if not candidate.exists() and candidate_key not in planned:
            return candidate, True, None
        raise PlanError("Коллизия даже с hash-суффиксом")
    # skip/ask: оставляем имя, executor пропустит; ask дополнительно в review.
    return dst, True, "collision"


def build_plan(
    catalog: Catalog,
    ruleset: RuleSet,
    library_root: str | Path,
    *,
    profile: Any | None = None,
    allow_free_text_paths: bool | None = None,
    case_insensitive: bool = False,
    limit: int | None = None,
    persist: bool = True,
) -> Plan:
    """Строит план раскладки.

    ``persist=False`` — предпросмотр без записи в каталог: план не получает
    строку в ``plans`` и возвращается с ``id == 0`` (apply/undo для него
    недоступны).
    """
    root = Path(library_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    allow_free_text = (
        ruleset.allow_free_text_paths if allow_free_text_paths is None else allow_free_text_paths
    )
    vlm_allowed = (
        bool(getattr(profile, "vlm_in_main_flow", False)) if profile is not None else False
    )
    planned: set[str] = set()
    items: list[PlanItem] = []
    unavailable: dict[str, str] = {}
    warnings: list[str] = []
    errors: list[str] = []
    collisions = 0
    seq = 0

    seen_assets = 0
    for asset_id in catalog.iter_asset_ids():
        if limit is not None and seen_assets >= limit:
            break
        seen_assets += 1
        context = catalog.asset_context(asset_id)
        if not context:
            continue
        files = [f for f in catalog.files_for_asset(asset_id) if f["exists_flag"]]
        if not files:
            continue
        primary = next((f for f in files if bool(f["is_primary"])), None)
        if primary is None:
            primary = files[0]
            warnings.append(f"asset {asset_id}: primary file не помечен, взят первый")
        volume_root = catalog.volume_root(int(primary["volume_id"]))
        if not volume_root:
            errors.append(f"asset {asset_id}: неизвестный том")
            continue
        context["file.stem"] = Path(primary["rel_path"]).stem
        context["ext"] = Path(primary["rel_path"]).suffix.lower().lstrip(".")

        src_primary = Path(volume_root) / primary["rel_path"]
        decision: RuleDecision | None = None
        destination_dir: Path | None = None

        for file_row in files:
            if ruleset.keep_sidecars is False and file_row["ext"] in {
                ".xmp",
                ".aae",
                ".dop",
                ".pp3",
                ".lrv",
                ".thm",
            }:
                continue
            src = Path(volume_root) / file_row["rel_path"]
            if not src.exists():
                errors.append(f"{src}: источник не найден")
                catalog.mark_file_missing(int(file_row["id"]))
                continue
            if decision is None:
                # {counter} — номер текущего элемента плана. Шаблон пути
                # рендерится один раз на ассет по его первому файлу, чтобы
                # RAW+JPEG+sidecar не разъехались по разным каталогам.
                context["counter"] = seq + 1
                decision = _decide(
                    ruleset,
                    context,
                    allow_free_text=allow_free_text,
                    vlm_allowed=vlm_allowed,
                    counter=seq + 1,
                    unavailable=unavailable,
                )
                assert decision.path_template is not None
                try:
                    # Шаблон пути задаёт каталог назначения (как в канонических
                    # примерах §5.2.1); имя файла сохраняется, иначе архив
                    # переименовался бы в «photo»/«video». Санитизация и
                    # проверка root — в safe_join.
                    destination_dir = safe_join(root, decision.path_template)
                except Exception as exc:
                    errors.append(f"asset {asset_id}: {exc}")
                    catalog.add_review(
                        asset_id,
                        "plan_path_rejected",
                        severity="high",
                        payload={"error": str(exc)},
                    )
                    break
                if (destination_dir / src_primary.name).resolve() == src_primary.resolve():
                    # Ассет уже лежит на месте: не расходуем seq и идём дальше.
                    break
            seq += 1
            assert destination_dir is not None
            dst = destination_dir / src.name
            item_hash = str(context.get("hash") or "")
            if not file_row["is_primary"] or not item_hash:
                try:
                    item_hash = hash_file(src)
                except (OSError, ValueError):
                    item_hash = ""
            if dst.exists() and ruleset.collision == "hash" and not item_hash:
                raise PlanError(f"нет хэша для разрешения hash-коллизии: {src}")
            if dst.exists() or _planned_key(dst, case_insensitive) in planned:
                dst, collision, reason = resolve_collision(
                    dst,
                    mode=ruleset.collision,
                    source_hash=item_hash,
                    planned=planned,
                    case_insensitive=case_insensitive,
                )
                if collision:
                    collisions += 1
            else:
                collision, reason = False, None
            planned.add(_planned_key(dst, case_insensitive))
            state = "pending"
            error = None
            if collision and reason == "collision":
                if ruleset.collision == "skip":
                    state = "skipped_collision"
                elif ruleset.collision == "ask":
                    state = "review"
                    catalog.add_review(
                        asset_id,
                        "plan_collision",
                        severity="warning",
                        payload={"src": str(src), "dst": str(dst)},
                    )
            if decision.fallback and ruleset.fallback.get("review"):
                # Fallback остаётся безопасной раскладкой в _Unsorted; review — это
                # информационный флаг, а не блокировка операции (FR-1.2).
                catalog.add_review(
                    asset_id,
                    "fallback_unsorted",
                    severity="info",
                    payload={"src": str(src), "dst": str(dst)},
                )
            items.append(
                PlanItem(
                    seq=seq,
                    asset_id=asset_id,
                    src=str(src),
                    dst=str(dst),
                    action=ruleset.mode,
                    size_bytes=int(file_row["size_bytes"]),
                    state=state,
                    error=error,
                    collision=collision,
                    review_reason=reason,
                    rule_id=decision.rule_id,
                    hash=item_hash or None,
                    tags=list(decision.tags),
                )
            )

    # move на другой том тоже временно занимает место на dst-диске.
    total_bytes = sum(item.size_bytes for item in items if item.action in {"copy", "move"})
    free_bytes = shutil.disk_usage(root).free
    space_ok = total_bytes <= free_bytes
    if not space_ok:
        warnings.append(f"Недостаточно места: нужно {total_bytes} байт, свободно {free_bytes} байт")
        # Блокируем только элементы сверх доступного объёма, а не весь план.
        remaining = free_bytes
        for item in items:
            if item.state != "pending":
                continue
            cost = item.size_bytes if item.action in {"copy", "move"} else 0
            if cost <= remaining:
                remaining -= cost
            else:
                item.state = "review"
                item.review_reason = "insufficient_space"
        catalog.add_review(
            None,
            "plan_insufficient_space",
            severity="high",
            payload={"needed": total_bytes, "free": free_bytes},
        )
    if str(root).startswith(("//", "\\\\")):
        warnings.append("library_root на сетевом пути: снижен параллелизм и включены retry")
    summary: dict[str, Any] = {
        "items": len(items),
        "assets": len({item.asset_id for item in items}),
        "bytes": total_bytes,
        "actions": _count_by(items, "action"),
        "states": _count_by(items, "state"),
        "collisions": collisions,
        "unavailable_rules": unavailable,
        "warnings": warnings,
        "errors": errors,
        "space_ok": space_ok,
        "free_bytes": free_bytes,
        "mode": ruleset.mode,
        "fallback_review": bool(ruleset.fallback.get("review", True)),
    }
    plan_id = (
        catalog.create_plan(ruleset.mode, str(root), ruleset.to_dict(), summary, items)
        if persist
        else 0
    )
    return Plan(
        id=plan_id,
        mode=ruleset.mode,
        root=str(root),
        state="draft",
        items=items,
        summary=summary,
        rules=ruleset.to_dict(),
    )


def _count_by(items: list[PlanItem], attribute: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        key = str(getattr(item, attribute))
        result[key] = result.get(key, 0) + 1
    return result


def plan_tree(plan: Plan) -> dict[str, Any]:
    """Дерево «что куда» для UI и CLI."""
    tree: dict[str, Any] = {"type": "root", "name": Path(plan.root).name, "children": {}}
    for item in plan.items:
        try:
            rel = Path(item.dst).relative_to(plan.root)
        except ValueError:
            rel = Path("_external") / Path(item.dst).name
        node = tree
        for part in rel.parts[:-1]:
            children = node["children"]
            if part not in children:
                children[part] = {"type": "dir", "name": part, "children": {}}
            node = children[part]
        node["children"].setdefault(rel.name, {"type": "file", "name": rel.name, "items": []})
        node["children"][rel.name]["items"].append(
            {"src": item.src, "action": item.action, "state": item.state, "seq": item.seq}
        )
    return tree


def render_tree(plan: Plan, *, max_lines: int = 200) -> str:
    tree = plan_tree(plan)
    lines: list[str] = [f"{tree['name']}/"]

    def walk(node: dict[str, Any], prefix: str) -> None:
        children = sorted(
            node.get("children", {}).items(),
            key=lambda pair: (pair[1]["type"] != "dir", pair[0].lower()),
        )
        for index, (name, child) in enumerate(children):
            if len(lines) >= max_lines:
                return
            last = index == len(children) - 1
            connector = "└── " if last else "├── "
            if child["type"] == "dir":
                lines.append(f"{prefix}{connector}{name}/")
                walk(child, prefix + ("    " if last else "│   "))
            else:
                count = len(child.get("items", []))
                suffix = f" ({count})" if count > 1 else ""
                lines.append(f"{prefix}{connector}{name}{suffix}")

    walk(tree, "")
    if len(lines) >= max_lines:
        lines.append("… (обрезано)")
    return "\n".join(lines)


__all__ = ["build_plan", "plan_tree", "render_tree", "resolve_collision"]
