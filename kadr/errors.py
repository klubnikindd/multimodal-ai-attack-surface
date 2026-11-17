"""Доменные исключения Kadr."""

from __future__ import annotations


class KadrError(Exception):
    """Базовая ошибка продукта."""


class ConfigError(KadrError):
    """Некорректная конфигурация или настройка."""


class CatalogError(KadrError):
    """Ошибка каталога SQLite."""


class MetadataError(KadrError):
    """Файл не удалось разобрать безопасным способом."""


class RuleError(KadrError):
    """Некорректное правило DSL."""


class PathSafetyError(KadrError):
    """Путь выходит за library_root или содержит небезопасный компонент."""


class PlanError(KadrError):
    """План нельзя построить или применить."""


class ExecutionError(KadrError):
    """Ошибка исполнения плана."""


class VectorIndexError(KadrError):
    """Векторный бэкенд недоступен или вернул ошибку."""


class LicenseError(KadrError):
    """Манифест модели нарушает лицензионную политику."""
