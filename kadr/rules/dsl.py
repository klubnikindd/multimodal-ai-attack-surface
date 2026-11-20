"""Загрузка и валидация декларативных правил (YAML/JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..constants import COLLISION_MODES, FILE_MODES, LOGICAL_KEYS, RULE_OPERATORS
from ..errors import RuleError
from .templates import validate_template

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

DEFAULT_FALLBACK = {"path": "_Unsorted/{file.mtime:%Y-%m}", "review": True}


@dataclass(slots=True)
class RuleSet:
    version: int
    rules: list[dict[str, Any]]
    safety: dict[str, Any] = field(default_factory=dict)
    fallback: dict[str, Any] = field(default_factory=dict)
    library_root: str | None = None
    allow_free_text_paths: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "library_root": self.library_root,
            "allow_free_text_paths": self.allow_free_text_paths,
            "safety": dict(self.safety),
            "rules": list(self.rules),
            "fallback": dict(self.fallback),
        }

    @property
    def mode(self) -> str:
        return str(self.safety.get("mode", "copy"))

    @property
    def collision(self) -> str:
        return str(self.safety.get("collision", "suffix"))

    @property
    def never_delete(self) -> bool:
        return bool(self.safety.get("never_delete", True))

    @property
    def keep_sidecars(self) -> bool:
        return bool(self.safety.get("keep_sidecars", True))


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuleError(f"{name} должен быть объектом")
    return value


def _validate_condition(condition: Any, *, allow_free_text: bool) -> None:
    if condition is None:
        return
    if not isinstance(condition, dict):
        raise RuleError("when должен быть объектом условий")
    for key, value in condition.items():
        if key in LOGICAL_KEYS:
            if key == "not":
                _validate_condition(value, allow_free_text=allow_free_text)
            else:
                if not isinstance(value, list):
                    raise RuleError(f"{key} ожидает список условий")
                for item in value:
                    _validate_condition(item, allow_free_text=allow_free_text)
            continue
        if isinstance(value, dict):
            for operator in value:
                if operator not in RULE_OPERATORS:
                    raise RuleError(f"Неизвестный оператор '{operator}' в поле '{key}'")
            if "between" in value and (
                not isinstance(value["between"], (list, tuple)) or len(value["between"]) != 2
            ):
                raise RuleError("between ожидает [min, max]")
        # скалярное значение трактуется как eq


def validate_rules(data: dict[str, Any], *, allow_free_text: bool = False) -> RuleSet:
    if not isinstance(data, dict):
        raise RuleError("Корень правил должен быть объектом")
    version = data.get("version", 1)
    if version != 1:
        raise RuleError(f"Поддерживается только version: 1, получено {version!r}")
    allow_free_text = bool(data.get("allow_free_text_paths", allow_free_text))
    safety = _require_mapping(data.get("safety", {}), "safety")
    mode = safety.get("mode", "copy")
    if mode not in FILE_MODES:
        raise RuleError(f"Неизвестный режим: {mode}")
    collision = safety.get("collision", "suffix")
    if collision not in COLLISION_MODES:
        raise RuleError(f"Неизвестный режим коллизий: {collision}")
    if safety.get("never_delete") is False:
        # Удаление файлов в v0.1 не поддерживается вовсе, даже флагом.
        raise RuleError("never_delete: false запрещён в v0.1")
    safety = {
        "mode": mode,
        "verify": safety.get("verify", "blake3"),
        "never_delete": True,
        "keep_sidecars": bool(safety.get("keep_sidecars", True)),
        "collision": collision,
    }
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise RuleError("rules должен быть непустым списком")
    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise RuleError(f"Правило #{index} должно быть объектом")
        rule_id = str(rule.get("id") or f"rule_{index}")
        if rule_id in seen_ids:
            raise RuleError(f"Дублирующийся id правила: {rule_id}")
        seen_ids.add(rule_id)
        then = _require_mapping(rule.get("then"), f"then правила {rule_id}")
        path = then.get("path")
        if not isinstance(path, str) or not path.strip():
            raise RuleError(f"Правило {rule_id} не задаёт then.path")
        validate_template(path, allow_free_text=allow_free_text)
        _validate_condition(rule.get("when"), allow_free_text=allow_free_text)
        normalized.append(dict(rule))
    fallback = data.get("fallback", DEFAULT_FALLBACK)
    fallback = _require_mapping(fallback, "fallback")
    fallback_path = fallback.get("path", DEFAULT_FALLBACK["path"])
    validate_template(str(fallback_path), allow_free_text=allow_free_text)
    fallback = {"path": fallback_path, "review": bool(fallback.get("review", True))}
    return RuleSet(
        version=1,
        rules=normalized,
        safety=safety,
        fallback=fallback,
        library_root=data.get("library_root"),
        allow_free_text_paths=allow_free_text,
        raw=dict(data),
    )


def load_rules_dict(data: dict[str, Any], *, allow_free_text: bool = False) -> RuleSet:
    if data is None:
        raise RuleError("Правила пусты: ожидается объект с version/rules")
    return validate_rules(data, allow_free_text=allow_free_text)


def load_rules(path: str | Path, *, allow_free_text: bool = False) -> RuleSet:
    file_path = Path(path)
    if not file_path.is_file():
        raise RuleError(f"Файл правил не найден: {file_path}")
    text = file_path.read_text(encoding="utf-8")
    if not text.strip():
        raise RuleError(f"Файл правил пуст: {file_path}")
    try:
        if file_path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            if yaml is None:
                raise RuleError("PyYAML не установлен, используйте JSON-правила")
            data = yaml.safe_load(text)
    except (ValueError, TypeError) as exc:
        raise RuleError(f"Не удалось разобрать правила: {exc}") from exc
    if data is None:
        raise RuleError(f"Файл правил пуст: {file_path}")
    if not isinstance(data, dict):
        raise RuleError("Корень правил должен быть объектом")
    return validate_rules(data, allow_free_text=allow_free_text)


def dump_rules(ruleset: RuleSet, path: str | Path) -> None:
    file_path = Path(path)
    data = ruleset.to_dict()
    if file_path.suffix.lower() == ".json" or yaml is None:
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        file_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )


__all__ = [
    "DEFAULT_FALLBACK",
    "RuleSet",
    "dump_rules",
    "load_rules",
    "load_rules_dict",
    "validate_rules",
]
