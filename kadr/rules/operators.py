"""Операторы DSL правил (FR-2.1) и FR-2.13 (правила без VLM)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..constants import LOGICAL_KEYS, RULE_OPERATORS
from ..errors import RuleError
from ..models import RuleDecision

VLM_FIELDS = frozenset(
    {
        "ai.caption",
        "ai.caption_ru",
        "ai.caption_en",
        "ai.caption_short",
        "ai.event_type",
        "ai.keywords_en",
        "ai.alt_text",
        "ai.mood",
    }
)


def is_vlm_field(field: str) -> bool:
    return field in VLM_FIELDS or field.startswith("ai.caption") or field.startswith("ai.vlm")


def lookup(context: dict[str, Any], field: str) -> Any:
    if field in context:
        return context[field]
    current: Any = context
    for part in field.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _compare(a: Any, b: Any) -> int | None:
    if a is None or b is None:
        return None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return (a > b) - (a < b)
    da, db = _as_datetime(a), _as_datetime(b)
    if da is not None and db is not None:
        if da.tzinfo is None and db.tzinfo is not None:
            da = da.replace(tzinfo=db.tzinfo)
        if db.tzinfo is None and da.tzinfo is not None:
            db = db.replace(tzinfo=da.tzinfo)
        return (da > db) - (da < db)
    sa, sb = str(a).lower(), str(b).lower()
    return (sa > sb) - (sa < sb)


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, list) or isinstance(b, list):
        return a == b
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    return _compare(a, b) == 0


def _in(actual: Any, expected: Any) -> bool:
    expected_list = expected if isinstance(expected, (list, tuple, set)) else [expected]
    if isinstance(actual, (list, tuple, set)):
        return any(_eq(item, candidate) for item in actual for candidate in expected_list)
    return any(_eq(actual, candidate) for candidate in expected_list)


def _contains(actual: Any, expected: Any) -> bool:
    if actual is None:
        return False
    if isinstance(actual, (list, tuple, set)):
        if isinstance(expected, (list, tuple, set)):
            return any(_eq(item, candidate) for item in actual for candidate in expected)
        return any(_eq(item, expected) for item in actual)
    if isinstance(expected, (list, tuple, set)):
        return any(str(candidate).lower() in str(actual).lower() for candidate in expected)
    return str(expected).lower() in str(actual).lower()


def _evaluate_field(actual: Any, condition: Any) -> bool:
    if not isinstance(condition, dict) or not any(key in RULE_OPERATORS for key in condition):
        return _eq(actual, condition)
    for operator, expected in condition.items():
        if operator not in RULE_OPERATORS:
            raise RuleError(f"Неизвестный оператор: {operator}")
        if operator == "eq" and not _eq(actual, expected):
            return False
        if operator == "in" and not _in(actual, expected):
            return False
        if operator == "gte":
            cmp = _compare(actual, expected)
            if cmp is None or cmp < 0:
                return False
        if operator == "lte":
            cmp = _compare(actual, expected)
            if cmp is None or cmp > 0:
                return False
        if operator == "between":
            if not isinstance(expected, (list, tuple)) or len(expected) != 2:
                raise RuleError("between ожидает [min, max]")
            low, high = _compare(actual, expected[0]), _compare(actual, expected[1])
            if low is None or high is None or low < 0 or high > 0:
                return False
        if operator == "contains" and not _contains(actual, expected):
            return False
        if operator == "regex":
            try:
                if re.search(str(expected), str(actual or ""), flags=re.I) is None:
                    return False
            except re.error as exc:
                raise RuleError(f"Некорректный regex: {expected}") from exc
        if operator == "exists":
            want = bool(expected)
            if (actual is not None) != want:
                return False
        if operator in {"any", "all"}:
            values = list(actual) if isinstance(actual, (list, tuple, set)) else [actual]
            if isinstance(expected, bool):
                checks = [value is not None for value in values]
            elif isinstance(expected, dict):
                checks = [_evaluate_field(value, expected) for value in values]
            else:
                checks = [_in(value, expected) for value in values]
            if operator == "any" and not any(checks):
                return False
            if operator == "all" and not all(checks):
                return False
        if operator == "not":
            if _evaluate_field(actual, expected):
                return False
    return True


def evaluate_condition(condition: Any, context: dict[str, Any]) -> bool:
    if condition is None or condition == {}:
        return True
    if not isinstance(condition, dict):
        raise RuleError("Условие должно быть объектом")
    for key, value in condition.items():
        if key == "all":
            if not isinstance(value, list):
                raise RuleError("all ожидает список условий")
            if not all(evaluate_condition(item, context) for item in value):
                return False
        elif key == "any":
            if not isinstance(value, list):
                raise RuleError("any ожидает список условий")
            if not any(evaluate_condition(item, context) for item in value):
                return False
        elif key == "not":
            if evaluate_condition(value, context):
                return False
        elif key in LOGICAL_KEYS:
            raise RuleError(f"Неизвестный логический ключ: {key}")
        else:
            if not _evaluate_field(lookup(context, key), value):
                return False
    return True


def collect_fields(condition: Any) -> set[str]:
    fields: set[str] = set()
    if not isinstance(condition, dict):
        return fields
    for key, value in condition.items():
        if key in LOGICAL_KEYS:
            if isinstance(value, list):
                for item in value:
                    fields |= collect_fields(item)
            else:
                fields |= collect_fields(value)
        else:
            fields.add(key)
    return fields


def evaluate_rule(
    rule: dict[str, Any],
    context: dict[str, Any],
    *,
    vlm_allowed: bool = False,
    allow_free_text_paths: bool = False,
) -> RuleDecision:
    rule_id = str(rule.get("id") or "unnamed")
    when = rule.get("when") or {}
    fields = collect_fields(when)
    unavailable = sorted(
        field
        for field in fields
        if is_vlm_field(field) and lookup(context, field) is None and not vlm_allowed
    )
    if unavailable:
        return RuleDecision(
            rule_id=rule_id,
            matched=False,
            unavailable_on_profile=True,
            reason=(
                "Правило требует VLM-полей, недоступных на этом профиле: "
                + ", ".join(unavailable)
            ),
        )
    try:
        matched = evaluate_condition(when, context)
    except RuleError:
        raise
    if not matched:
        return RuleDecision(rule_id=rule_id, matched=False)
    then = rule.get("then") or {}
    path = then.get("path")
    if not path:
        raise RuleError(f"Правило {rule_id} не задаёт then.path")
    from .templates import render_path_template

    rendered = render_path_template(
        str(path), context, allow_free_text=allow_free_text_paths, counter=context.get("counter")
    )
    return RuleDecision(
        rule_id=rule_id,
        matched=True,
        stop=bool(rule.get("stop", False)),
        path_template=rendered,
        tags=[str(tag) for tag in (then.get("tags") or [])],
    )


__all__ = [
    "VLM_FIELDS",
    "collect_fields",
    "evaluate_condition",
    "evaluate_rule",
    "is_vlm_field",
    "lookup",
]
