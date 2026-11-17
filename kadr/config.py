"""Конфигурация Kadr: пути каталога, профиль, локальный API."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .constants import DEFAULT_OCR_LANGUAGE, OCR_LANGUAGE_PATTERN
from .errors import ConfigError


def default_home() -> Path:
    env = os.environ.get("KADR_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".kadr").resolve()


@dataclass(slots=True)
class Settings:
    """Настройки одного экземпляра движка.

    ``dev_mode`` — только для разработки: он не должен попадать в релизную
    сборку (CI проверяет, что ``dev_mode`` не выставлен в packaging-конфиге).
    """

    home: Path = field(default_factory=default_home)
    catalog_path: Path | None = None
    library_root: Path | None = None
    rules_path: Path | None = None
    profile_override: str | None = None
    vector_backend_override: str | None = None
    api_token: str = ""
    api_host: str = "127.0.0.1"
    api_port: int = 0
    dev_mode: bool = False
    allow_free_text_paths: bool = False
    skip_hidden: bool = True
    follow_symlinks: bool = False
    nightly_vlm_enabled: bool = False
    case_insensitive_fs: bool | None = None
    allowed_scan_roots: list[Path] = field(default_factory=list)
    parser_sandbox: bool = True
    ocr_language: str = DEFAULT_OCR_LANGUAGE

    def __post_init__(self) -> None:
        self.home = Path(self.home).expanduser().resolve()
        self.ocr_language = str(self.ocr_language or DEFAULT_OCR_LANGUAGE).strip()
        if not OCR_LANGUAGE_PATTERN.fullmatch(self.ocr_language):
            raise ConfigError(f"Некорректный KADR_OCR_LANG: {self.ocr_language!r}")
        if self.catalog_path is None:
            self.catalog_path = self.home / "catalog.db"
        else:
            self.catalog_path = Path(self.catalog_path).expanduser().resolve()
        if self.library_root is not None:
            self.library_root = Path(self.library_root).expanduser().resolve()
        if self.rules_path is not None:
            self.rules_path = Path(self.rules_path).expanduser().resolve()
        if not self.allowed_scan_roots:
            raw_roots = os.environ.get("KADR_SCAN_ROOTS", "")
            self.allowed_scan_roots = [
                Path(item).expanduser().resolve() for item in raw_roots.split(os.pathsep) if item.strip()
            ]
        else:
            self.allowed_scan_roots = [Path(item).expanduser().resolve() for item in self.allowed_scan_roots]
        if not self.api_token:
            self.api_token = os.environ.get("KADR_API_TOKEN") or secrets.token_urlsafe(32)
        if self.case_insensitive_fs is None:
            if os.name == "nt":
                self.case_insensitive_fs = True
            elif hasattr(os, "uname"):
                self.case_insensitive_fs = os.uname().sysname == "Darwin"
            else:  # pragma: no cover - экзотические платформы
                self.case_insensitive_fs = False

    @property
    def models_dir(self) -> Path:
        return self.home / "models"

    @property
    def trash_dir(self) -> Path:
        return self.home / "trash"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def lock_path(self) -> Path:
        return self.home / "engine.lock"

    def ensure_dirs(self) -> None:
        for path in (self.home, self.models_dir, self.trash_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path, 0o700)
            except OSError:  # pragma: no cover - Windows ACL
                pass

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        env_map = {
            "home": "KADR_HOME",
            "catalog_path": "KADR_CATALOG",
            "library_root": "KADR_LIBRARY_ROOT",
            "rules_path": "KADR_RULES",
            "profile_override": "KADR_PROFILE",
            "vector_backend_override": "KADR_VECTOR_BACKEND",
            "api_token": "KADR_API_TOKEN",
            "api_port": "KADR_API_PORT",
            "ocr_language": "KADR_OCR_LANG",
        }
        values: dict[str, object] = {}
        for key, env_name in env_map.items():
            raw = os.environ.get(env_name)
            if raw:
                values[key] = raw
        if "api_port" in values:
            try:
                values["api_port"] = int(values["api_port"])  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"Некорректный KADR_API_PORT: {values['api_port']!r}") from exc
        if "dev_mode" not in overrides and os.environ.get("KADR_DEV_MODE") in {"1", "true", "yes"}:
            values["dev_mode"] = True
        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]
