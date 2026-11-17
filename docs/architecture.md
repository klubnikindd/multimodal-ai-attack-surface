# Архитектура Kadr

```
UI Shell (Tauri 2, системный webview)
   │ IPC + HTTP, только 127.0.0.1, случайный порт, bearer-токен, CSRF
   ▼
Engine API (Python, FastAPI) ── kadr/api.py
   ├── Catalog SQLite (WAL, FTS5, миграции) ── kadr/catalog
   ├── Scanner / L0 metadata ── kadr/scanner.py, kadr/metadata
   ├── Rules DSL → Planner → Executor → Journal/Undo
   │     kadr/rules → kadr/planner.py → kadr/executor.py
   ├── AI-lite (OCR, zero-shot, concept embeddings) ── kadr/ai
   ├── VectorIndex (sqlite-vec | hybrid | ann | exact) ── kadr/vector
   ├── XMP sidecar / privacy presets ── kadr/xmp.py
   └── Security (paths, PII/injection, auth, sandbox) ── kadr/security
   │
   ▼
Workers: parser-worker — отдельный процесс с rlimits/timeouts/process-group
   • Pillow / ffprobe / tesseract                 ← недоверенный вход (POSIX rlimits)
   • seccomp/Seatbelt/Job Object и egress enforcement — M3, не заявляются в v0.1
   • ONNX Runtime (эмбеддинги/OCR) — слот после M0-аудита весов
   • llama.cpp server (GGUF) — только по требованию, не резидент
```

## Инварианты

1. Падение/компромисс воркера не роняет UI и не портит каталог.
2. parser-worker не делает сетевых вызовов; OS-level egress/seccomp enforcement — M3.
3. Единственный путь к ФС-мутациям — исполнитель утверждённого плана.
4. Оригиналы неизменны по умолчанию; copy + verify; undo; никогда не delete.
5. Домен не знает, какой бэкенд внутри `VectorIndex`; деградация видима в UI.
6. Дерево процессов принадлежит оболочке: process group / Job Object /
   PDEATHSIG, watchdog, единый `engine_shutdown()`.

## Поток данных

```
scan → meta_raw (immutable) → assets/files/volumes
     → analyze (OCR, zero-shot, embeddings) → ai/embeddings/tags/fts
     → plan (rules + assets) → plans/plan_items (draft)
     → apply (journal до действия) → files в library_root
     → undo (reverse journal, проверка hash) → файлы возвращены/в trash
```

## Легаси

`attack_surface/` — исходный PoC извлечения метаданных (v1). Он оставлен для
совместимости и research-демо; продукт и новые тесты используют пакет `kadr/`.
