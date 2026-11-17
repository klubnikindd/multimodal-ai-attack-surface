"""Шаблоны путей: закрытый allowlist токенов + санитизация (FR-2.2, SEC-06)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..constants import FREE_TEXT_PATH_TOKENS, PATH_TOKENS
from ..errors import RuleError
from ..security.paths import normalize_rel_path, sanitize_segment

_TOKEN = re.compile(r"\{([^{}:]+)(?::([^{}]+))?\}")


def _lookup(context: dict[str, Any], field: str) -> Any:
    if field in context:
        return context[field]
    current: Any = context
    for part in field.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _stringify(value: Any, fmt: str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if fmt:
            try:
                return value.strftime(fmt)
            except ValueError:
                return value.isoformat()
        return value.isoformat()
    if fmt:
        # Формат у не-даты — это либо срез hash, либо суффикс-подсказка.
        text = str(value)
        if fmt.isdigit():
            return text[: int(fmt)]
        return text
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _token_specs(template: str) -> list[tuple[str, str | None, str | None]]:
    """Возвращает (token, format, default) с поддержкой ``{token|default}``."""
    specs = []
    for match in _TOKEN.finditer(template):
        token = match.group(1)
        fmt = match.group(2)
        default = None
        if "|" in token:
            token, _, default = token.partition("|")
        specs.append((token, fmt, default))
    return specs


def validate_template(template: str, *, allow_free_text: bool = False) -> None:
    if not template or not str(template).strip():
        raise RuleError("Пустой шаблон пути")
    for token, _fmt, _default in _token_specs(template):
        if token in FREE_TEXT_PATH_TOKENS:
            if not allow_free_text:
                raise RuleError(
                    f"Токен {token} запрещён по умолчанию (нужен allow_free_text_paths: true)"
                )
            continue
        if token not in PATH_TOKENS:
            raise RuleError(f"Неизвестный токен шаблона: {token}")
        if token == "hash" and _fmt and not _fmt.isdigit():
            raise RuleError("Формат {hash:...} должен быть числом символов")
    # Незакрытые/неизвестные скобки — ошибка, а не тихая подстановка.
    leftovers = _TOKEN.sub("", template)
    if "{" in leftovers or "}" in leftovers:
        raise RuleError("Некорректная фигурная скобка в шаблоне пути")


def render_path_template(
    template: str,
    context: dict[str, Any],
    *,
    allow_free_text: bool = False,
    counter: int | None = None,
) -> str:
    validate_template(template, allow_free_text=allow_free_text)

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        fmt = match.group(2)
        default = None
        if "|" in token:
            token, _, default = token.partition("|")
        if token in FREE_TEXT_PATH_TOKENS and not allow_free_text:
            raise RuleError(f"Свободный текст запрещён в путях: {token}")
        if token == "counter":
            return str(counter if counter is not None else 1)
        value = _lookup(context, token)
        rendered = _stringify(value, fmt)
        if rendered == "" and default is not None:
            rendered = default
        if token == "hash" and value:
            length = int(fmt) if fmt and fmt.isdigit() else 8
            rendered = str(value).removeprefix("blake3:").removeprefix("sha256:")[:length]
        if not (token == "date" and fmt):
            # Формат даты может намеренно создавать подкаталоги (`%Y/%Y-%m-%d`).
            if token in {"date.source", "kind", "ext", "ai.scene_type", "ai.event_type", "ai.doc_kind"}:
                rendered = sanitize_segment(rendered)
            else:
                rendered = rendered.replace("/", "_").replace("\\", "_")
                rendered = sanitize_segment(rendered)
        return rendered

    # date-формат может намеренно создавать подкаталоги (`%Y/%Y-%m-%d`).
    rendered = _TOKEN.sub(replace, template)
    # Разделители оставляем только из шаблона; значения уже санитизированы.
    segments = [sanitize_segment(part) for part in rendered.replace("\\", "/").split("/") if part not in {"", "."}]
    if not segments:
        raise RuleError("Шаблон пути дал пустой путь")
    return normalize_rel_path("/".join(segments))


__all__ = ["render_path_template", "validate_template"]
