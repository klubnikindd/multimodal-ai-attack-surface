# Kadr — раскладчик и каталогизатор медиаархива

**Kadr** (рабочее имя) — локальное кроссплатформенное приложение для раскладки
фото/видео/аудио по пользовательским правилам и каталогизации библиотеки.
Обещание продукта: разобрать большой архив по папкам, найти точные дубли,
сделать библиотеку поисковой и **не потерять ни одного оригинала**. Любую
раскладку можно откатить.

Репозиторий также содержит research-трек **MIAS** (Multimodal AI Attack Surface):
модель угроз, security-корпус и защитный контур. Требования безопасности —
обязательная часть продукта, а не надстройка.

- Исходное ТЗ: [`docs/spec/v3.1.md`](docs/spec/v3.1.md) (current; `v3.md`, `v2.md`, `v1.md` — архив)
- Отчёт о готовности: [`docs/READINESS_REPORT_v3.md`](docs/READINESS_REPORT_v3.md)
- План реализации: [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md)
- Архитектурные решения: [`docs/adr/`](docs/adr/)
- Известные ограничения: [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md)
- Бенчмарки: [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)
- Модель угроз: [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md)

## Что уже работает (M0/M1/M2-lite)

- Сканер ФС + каталог SQLite (WAL, миграции), идемпотентность по контент-хэшу.
- L0-метаданные: EXIF/Pillow, XMP-sidecar, ffprobe (если установлен), иерархия
  дат `DateTimeOriginal → CreateDate → XMP → QuickTime → ID3 → имя файла → mtime`
  с `taken_at_source` и `date_confidence`.
- Группы ассетов: RAW+JPEG+XMP, Live Photo (HEIC+MOV), `.aae/.dop/.pp3`,
  `.LRV/.THM`; правила применяются к группе.
- DSL правил YAML/JSON, операторы `eq/in/gte/lte/between/contains/regex/exists/
  any/all/not`, шаблоны путей с allowlist-токенами и санитизацией.
- Dry-run → план → предпросмотр дерева → copy/move/hardlink с verify blake3 →
  write-ahead журнал → **undo**; `never_delete` включён всегда.
- AI-lite: классификация скриншот/документ, zero-shot теги, OCR (tesseract,
  если установлен), эмбеддинги и семантический поиск через сменный `VectorIndex`
  (`sqlite-vec` / `hybrid` / `ann` / exact fallback) с **видимым** статусом
  деградации.
- Поиск: структурный синтаксис (`camera:Canon date:2025-07..2025-08 kind:video
  rating>=4`), FTS5 с **русской морфологией**, семантика.
- Группы дублей: точные (один content_hash — несколько файлов) и почти-дубли в review.
- XMP-sidecar merge-запись, пресеты приватности `stock/social/archive`,
  запрет записи внутрь RAW.
- Локальный API на `127.0.0.1` с bearer-токеном, Origin/Host и CSRF-проверкой.
- Профили Lite/Balanced/Pro с авто-детектом; VLM вне Lite; ночная VLM-задача с
  честным ETA и контрактом JSON; лицензионный гейт весов.
- Lifecycle sidecar: process group, PDEATHSIG, единый shutdown, watchdog,
  single-instance lock, one-dir PyInstaller spec.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# для API-демо дополнительно: pip install -e ".[api]"
```

Минимально для CLI: Python 3.10+ и PyYAML. Pillow нужен для метаданных/эмбеддингов,
`blake3` — для контент-хэшей (без него используется `sha256:` fallback), FastAPI —
только для API. В релизе — Python 3.12 sidecar.

## CLI

```bash
kadr init
kadr scan /media/Photo
kadr analyze
kadr plan --preset date --root /media/Sorted --tree
kadr apply 1
kadr undo 1
kadr search 'camera:Canon date:2025-07..2025-08 kind:photo'
kadr bench /media/Photo --limit 100
kadr doctor
```

Полезные флаги: `--home`, `--catalog`, `--profile`, `--vector-backend`, `--json`.
Команда `kadr` объявлена в `[project.scripts]`; без установки пакета используйте
`python -m kadr ...`.
Каталог по умолчанию — `~/.kadr/catalog.db`. Для API/CLI можно ограничить
разрешённые корни сканирования: `KADR_SCAN_ROOTS=/media/Photo:/mnt/cards`
(системные каталоги и весь home по умолчанию запрещены). Язык OCR задаётся
`KADR_OCR_LANG` (по умолчанию `rus+eng`; нужен установленный tesseract
traineddata для каждого языка).

## Локальный API

```bash
KADR_HOME="$HOME/.kadr" \
KADR_SCAN_ROOTS="/media/Photo:/mnt/cards" \
KADR_API_TOKEN=$(python -c 'import secrets;print(secrets.token_urlsafe(32))') \
  uvicorn kadr.api:create_app --factory --host 127.0.0.1 --port 8765
```

`--reload` годится только для локальной разработки; не запускайте его на
публичных хостах. Legacy `attack_surface`-демо — тоже только для research.

Все запросы, кроме `/health`, требуют `Authorization: Bearer <token>`; мутации —
ещё `X-Kadr-CSRF: <csrf_token>` (выдаётся в `/v1/system/status`). CORS закрыт, `Host`/`Origin` проверяются. Файлы
отдаются только по `asset_id`, не по пути. Минимальный каталог-UI доступен на
`http://127.0.0.1:8765/` (введите bearer-токен в шапке; статика отдаётся с CSP).

Основные эндпоинты: `/v1/system/status`, `/v1/scan`, `/v1/analyze`,
`/v1/assets`, `/v1/assets/{id}/file`, `/v1/search`, `/v1/plans`,
`/v1/plans/{id}/apply`, `/v1/plans/{id}/undo`, `/v1/review`, `/v1/jobs`,
`/v1/models`, `/internal/shutdown`.

## Архитектура

```
UI Shell (Tauri 2) ── IPC/HTTP + bearer ── Engine API (FastAPI)
                                            ├── Catalog SQLite (WAL, FTS5)
                                            ├── Rules → Planner → Executor → Journal/Undo
                                            ├── Metadata L0 (Pillow/ffprobe/XMP)
                                            ├── AI-lite (OCR, zero-shot, embeddings)
                                            └── VectorIndex (sqlite-vec | hybrid | ann | exact)
Workers: parser-worker — отдельный процесс с rlimits/timeouts (POSIX) и process group;
seccomp/Seatbelt/Job Object и egress enforcement — M3, политика в security/sandbox/policy.yaml
```

Подробнее: [`docs/architecture.md`](docs/architecture.md), ADR-0001…0005.

## Тесты

```bash
pytest
```

Наборы: unit (правила, пути, даты), integration (scan→plan→apply→undo),
fault-injection (ENOSPC/kill, undo), security (path traversal, auth/CSRF,
инъекции/PII), vector/search, XMP/privacy, lifecycle, CLI/API.

## Research-трек MIAS

- [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) — 16 угроз, payload'ы, защиты.
- [`security/`](security/) — red-team корпус, политика песочницы, parser-worker с rlimits.
- [`SECURITY_NOTES.md`](SECURITY_NOTES.md), [`SECURITY.md`](SECURITY.md) — разбор и
  ответственное раскрытие.
- Легаси-пакет `attack_surface/` — исходный PoC извлечения метаданных; продукт
  живёт в `kadr/`.

## Ограничения и честность

VLM на CPU медленный (10–25 с/кадр по ожиданиям M0), поэтому **не входит в Lite**;
на CPU-only он доступен только как явная ночная задача (experimental). Веса в
v0.1 не поставляются до завершения лицензионного аудита; эмбеддинги работают на
concept-hash fallback. Полный список — [`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md).

## Лицензия

MIT. См. [`LICENSE`](LICENSE), [`LICENSES.md`](LICENSES.md),
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
