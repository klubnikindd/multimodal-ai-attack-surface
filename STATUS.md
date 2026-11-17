# Отчёт о состоянии проекта

Источник истины по требованиям — [`docs/spec/v3.md`](docs/spec/v3.md). Ниже —
фактическое состояние реализации в этом репозитории, без обещаний будущих
майлстоунов.

## Сделано (M0-каркас и M1/M2-lite)

| Область | Статус | Комментарий |
|---|---|---|
| Каталог SQLite, WAL, миграции, схема §7 | ✅ | `kadr/catalog/schema.sql`, `kadr/catalog/repository.py` |
| Сканер ФС, идемпотентность, группы ассетов | ✅ | `kadr/scanner.py`, тесты групп RAW+JPEG+XMP/Live Photo |
| L0-метаданные и иерархия дат | ✅ | Pillow/EXIF/XMP/ffprobe, `taken_at_source`, `date_confidence` |
| DSL правил, шаблоны путей, санитизация | ✅ | `kadr/rules`, операторы, пресеты, FR-2.13 |
| Dry-run/план/дерево/коллизии | ✅ | `kadr/planner.py`, план сохраняется в БД |
| copy/move/hardlink + verify + журнал + undo | ✅ | `kadr/executor.py`, fault-injection тесты |
| AI-lite: OCR/zero-shot/классификация/эмбеддинги | ✅ (fallback) | tesseract по наличию; concept-hash вместо SigLIP до аудита весов |
| VectorIndex и видимая деградация | ✅ | sqlite-vec/hybrid/ann/exact; `vector_index_state` |
| Поиск: структурный/FTS5/семантика | ✅ | `kadr/search.py` |
| XMP-sidecar, merge, пресеты приватности | ✅ | `kadr/xmp.py`, RAW не пишем |
| Локальный API с auth/Origin/CSRF | ✅ | `kadr/api.py`, `kadr/security/api_auth.py` |
| Профили, VLM-контракт, ночной планировщик | ✅ (каркас) | реальный VLM-рантайм — после M0/лицензий |
| Lifecycle sidecar, one-dir spec, target-triple | ✅ (хелперы) | `kadr/lifecycle.py`, `packaging/`; релизные артефакты — M6 |
| Лицензионный гейт весов | ✅ | `models/manifest.yaml`, `kadr/licensing.py`; веса disabled |
| Документы KNOWN_LIMITATIONS/BENCHMARKS/ADR-0004/0005 | ✅ | честные ограничения и решения |
| Тесты | ✅ | `pytest`: unit/integration/security/fault/vector/CLI/API/lifecycle |
| Парсер недоверенных файлов | ⚠️ | отдельный worker + POSIX rlimits/timeouts; seccomp/Job Object/egress — M3 |

## Не сделано / требует инфраструктуры

| Задача | Статус | Комментарий |
|---|---|---|
| M0-спайки D (VLM-бюджеты) и E (200K+ векторов) | ☐ | нужны устройства и замеры; таблицы помечены «не измерено» |
| Реальный ONNX SigLIP/OpenOCR, GGUF VLM | ☐ | веса disabled до `license-weights`/`model-integrity` |
| ANN usearch на 200K | ☐ | адаптер есть; порог и recall — по спайку E |
| Видео/аудио ASR, сцены, ключевые кадры | ☐ | P1 |
| Лица/кластеры | ☐ | P1, opt-in, лицензия весов блокирует |
| Embedded-запись MP4/MOV/MP3, свойства ОС | ☐ | P1; JPEG/PNG/TIFF opt-in есть |
| Tauri UI, установщики, подпись, updater | ☐ | M6 |
| Полная CI-матрица 3 ОС, SBOM, Trivy, fuzz | ☐ | workflow-каркас добавлен; нужна инфраструктура |
| Golden-датасет и метрики качества | ☐ | M2/M3 |

## Известные проблемы

- Без `blake3` используется `sha256:` fallback; это меняет формат `content_hash`
  (несовместимо с уже проиндексированной библиотекой). Для релиза `blake3`
  обязателен.
- Без `sqlite-vec` поиск работает через exact fallback; статус `degraded` виден,
  но это не ANN и не подтверждает NFR-18.
- Concept-hash энкодер — не CLIP: семантика ограничена словарём понятий/токенов.
- Tesseract/ffprobe/Pillow — внешние/опциональные зависимости; при их отсутствии
  соответствующие поля пусты, ошибки попадают в `parse_errors`.
