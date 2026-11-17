"""Kadr — локальный раскладчик и каталогизатор медиаархива.

Пакет реализует движок продукта из ТЗ ``docs/spec/v3.md``: каталог SQLite,
сканер и L0-метаданные, DSL правил, планы/dry-run/undo, AI-lite и векторный
поиск за интерфейсом :class:`kadr.vector.base.VectorIndex`, XMP-sidecar,
локальный API и CLI.

Research-трек MIAS (Multimodal AI Attack Surface) живёт в ``security/`` и
``docs/THREAT_MODEL.md``; доменный код продукта не зависит от research-корпуса.
"""

from __future__ import annotations

__version__ = "0.1.0b0"
__product_name__ = "Kadr"
__research_track__ = "MIAS"

from .config import Settings  # noqa: E402

__all__ = ["Settings", "__product_name__", "__research_track__", "__version__"]
