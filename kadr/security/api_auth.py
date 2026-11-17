"""Локальный API: bearer-токен, Origin/Host, CSRF (SEC-10, T10)."""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def is_loopback_host(host: str) -> bool:
    name = host.strip().lower()
    if name.startswith("["):
        name = name[1:].split("]", 1)[0]
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    return name in {"127.0.0.1", "localhost", "::1"}


@dataclass(slots=True)
class ApiSecurity:
    token: str
    csrf_token: str = ""
    allowed_hosts: set[str] = field(default_factory=set)
    allowed_origins: set[str] = field(
        default_factory=lambda: {
            "http://127.0.0.1",
            "http://localhost",
            "tauri://localhost",
            "https://tauri.localhost",
        }
    )

    def __post_init__(self) -> None:
        if not self.csrf_token:
            # Независимый от bearer фактор: bearer утекает в заголовок каждого
            # запроса, CSRF живёт только в памяти UI и выдаётся по auth-запросу.
            self.csrf_token = secrets.token_urlsafe(32)

    @staticmethod
    def _host_name(host: str) -> str:
        name = host.strip().lower()
        if name.startswith("["):
            return name[1:].split("]", 1)[0]
        if name.count(":") == 1:
            return name.split(":", 1)[0]
        return name

    def _check_host(self, host: str | None) -> tuple[int, str] | None:
        if not host:
            return 401, "missing Host"
        if not is_loopback_host(host):
            return 403, "non-loopback Host is forbidden"
        if self.allowed_hosts:
            name = self._host_name(host)
            if name not in self.allowed_hosts and host.lower() not in self.allowed_hosts:
                return 403, "Host is not allowed"
        return None

    def _check_origin(self, origin: str | None) -> tuple[int, str] | None:
        # Пустой Origin — CLI/нативное приложение; доступ всё равно требует bearer.
        if not origin:
            return None
        normalized = origin.rstrip("/").lower()
        allowed = {item.rstrip("/").lower() for item in self.allowed_origins}
        if normalized in allowed:
            return None
        parsed = urlsplit(normalized)
        # UI обслуживается с того же loopback-хоста; порт заранее неизвестен.
        if parsed.scheme in {"http", "https"} and parsed.hostname and is_loopback_host(parsed.hostname):
            return None
        if parsed.scheme in {"tauri", "https"} and parsed.hostname in {"localhost", "tauri.localhost"}:
            return None
        return 403, "Origin is not allowed"

    @staticmethod
    def _bearer(headers: dict[str, str]) -> str | None:
        value = headers.get("authorization") or headers.get("Authorization")
        if not value:
            return None
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        return token.strip()

    def authorize(
        self,
        *,
        method: str,
        headers: dict[str, str],
        host: str | None = None,
    ) -> tuple[int, str] | None:
        # Host/Origin проверяем до bearer: не раскрываем валидность токена
        # запросам с чужого origin.
        host_error = self._check_host(host)
        if host_error:
            return host_error
        origin_error = self._check_origin(headers.get("origin"))
        if origin_error:
            return origin_error
        if not constant_time_equal(self._bearer(headers) or "", self.token):
            return 401, "missing bearer token"
        if method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return None
        # Мутации: bearer + CSRF.
        csrf = headers.get("x-kadr-csrf") or headers.get("X-Kadr-CSRF")
        if not csrf or not hmac.compare_digest(csrf, self.csrf_token):
            return 403, "missing or invalid CSRF token"
        return None


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


__all__ = ["ApiSecurity", "constant_time_equal", "is_loopback_host"]
