# Отчёт о состоянии проекта

<!-- kadr:generated:start -->

**Сгенерировано:** 2026-11-20T18:01:33+00:00 · **HEAD:** 53cace3 · **спека:** docs/spec/v3.1.md · **итог:** ✅ все проверенные гейты зелёные

| Гейт | Статус | Детали |
|---|---|---|
| tests | ✅ | 146 тестов: 0 fail, 2 skip |
| sqlite-vec-smoke | ✅ exit=0 | available=True, status=active |
| sidecar-lifecycle | ✅ exit=0 | ok=True, checks=3 |
| license-weights | ✅ exit=0 | enabled=2 issues=0 |
| model-integrity | ✅ exit=0 | ok=True, issues=0 |
| license-code | ✅ exit=0 | OK |
| sandbox-parity | ✅ exit=0 | policy_hash=bf75be1cf74e8e185dd34524e40b73c630f65e2f, design_parity={'equal': True, 'differences': {}, 'acce, enforcement={'parser_worker': 'rlimits/timeouts/pr |
| spec-invariants | ✅ exit=0 | OK: инварианты спецификаций соблюдены (current: v3.1.md) |
| no-onefile | ✅ exit=0 | OK: one-dir spec: D:\1IB_Project\multimodal-ai-attack-surface\packaging\kadr-engine.spec |

Источник: scripts/render-status.py (P0-08). Таблица перезаписывается, текст ниже — руками.

<!-- kadr:generated:end -->

Источник истины по требованиям — [`docs/spec/v3.1.md`](docs/spec/v3.1.md) (v3 → v3.1: 14 правок по [отчёту о готовности](docs/READINESS_REPORT_v3.md)). Ниже —
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
| Поиск: структурный/FTS5/семантика | ✅ | `kadr/search.py`; **RU-морфология** (стеммер Портера) в FTS |
| Группы дублей (FR-2.14) | ✅ | точные = один content_hash с несколькими файлами; почти-дубли → только review |
| Security-контур (часть) | ✅ | корпус SEC-01/04/06, decompression-bomb отклоняется (SEC-09), Job Object (SEC-17), API auth |
| XMP-sidecar, merge, пресеты приватности | ✅ | `kadr/xmp.py`, RAW не пишем |
| Локальный API с auth/Origin/CSRF | ✅ | `kadr/api.py`, `kadr/security/api_auth.py` |
| Профили, VLM-контракт, ночной планировщик | ✅ | VLM-runtime подключён (llama-server + «json_object» + валидатор §8.4), веса 2B/4B аудированы и enabled |
| Lifecycle sidecar, one-dir spec, target-triple | ✅ (хелперы) | `kadr/lifecycle.py`, `packaging/`; релизные артефакты — M6 |
| Лицензионный гейт весов | ✅ | 2 VLM-модели verified+enabled (apache-2.0, ревизии и sha256 запинены); embeddings/OCR disabled до ONNX-артефакта |
| Документы | ✅ | KNOWN_LIMITATIONS/BENCHMARKS, ADR-0001…0006, отчёт о готовности, план, спека v3.1 |
| Тесты | ✅ | `pytest`: unit/integration/security/fault/vector/CLI/API/lifecycle |
| Парсер недоверенных файлов | ⚠️ | worker + rlimits/timeout; **Job Object kill-on-close (Windows) ✅**; seccomp/Seatbelt (Linux/macOS) и egress — M3 |

## Не сделано / требует инфраструктуры

| Задача | Статус | Комментарий |
|---|---|---|
| Спайки D/E | ✅ | замерено: CPU 33/18/64 с, GPU 2,7/6,4 с; sqlite-vec до 200K (122 мс) |
| ONNX SigLIP2/OpenOCR | ☐ | нужен запиненный ONNX-артефакт; GGUF VLM — ✅ (llama-server) |
| ANN usearch | ✅ | tuned (conn32/ef200/256); recall 0.915–0.99 (10–50K), 0.80 (100K); sqlite-vec до 200K |
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
