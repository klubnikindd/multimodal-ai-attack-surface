-- Kadr catalog schema (ТЗ v3 §7). Источник истины — SQLite; файлы производны.
-- Миграции выполняет kadr.db.Catalog.migrate(); DDL идемпотентен.

CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS volumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT,
    label TEXT,
    root_path TEXT NOT NULL UNIQUE,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'other',
    media_class TEXT NOT NULL DEFAULT 'unknown',
    scene_type TEXT,
    bytes INTEGER NOT NULL DEFAULT 0,
    width INTEGER,
    height INTEGER,
    duration_ms INTEGER,
    taken_at TEXT,
    taken_at_source TEXT,
    date_confidence REAL NOT NULL DEFAULT 0,
    tz TEXT,
    camera_make TEXT,
    camera_model TEXT,
    lens TEXT,
    iso INTEGER,
    exposure_time TEXT,
    f_number REAL,
    focal_length REAL,
    orientation INTEGER,
    gps_lat REAL,
    gps_lon REAL,
    phash TEXT,
    quality_json TEXT,
    rating INTEGER,
    label TEXT,
    description TEXT,
    title TEXT,
    injection_suspected INTEGER NOT NULL DEFAULT 0,
    pii_suspected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_taken_at ON assets(taken_at);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
CREATE INDEX IF NOT EXISTS idx_assets_scene ON assets(scene_type);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    volume_id INTEGER NOT NULL REFERENCES volumes(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    ext TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    is_primary INTEGER NOT NULL DEFAULT 1,
    exists_flag INTEGER NOT NULL DEFAULT 1,
    UNIQUE(volume_id, rel_path)
);

CREATE INDEX IF NOT EXISTS idx_files_asset ON files(asset_id);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(rel_path);

CREATE TABLE IF NOT EXISTS meta_raw (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tool TEXT NOT NULL,
    tool_version TEXT,
    json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meta_raw_asset ON meta_raw(asset_id);

CREATE TABLE IF NOT EXISTS ai (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    task TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_rev TEXT NOT NULL DEFAULT '',
    prompt_rev TEXT NOT NULL DEFAULT '',
    output_json TEXT NOT NULL,
    confidence REAL,
    runtime_ms REAL,
    device TEXT,
    source TEXT NOT NULL DEFAULT 'model',
    experimental INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(asset_id, task, model_id, model_rev, prompt_rev)
);

CREATE INDEX IF NOT EXISTS idx_ai_asset_task ON ai(asset_id, task);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES tags(id) ON DELETE SET NULL,
    alias_of INTEGER REFERENCES tags(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS asset_tags (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'model',
    confidence REAL,
    verified_by TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(asset_id, tag_id, source)
);

CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    bbox_json TEXT NOT NULL,
    embedding BLOB,
    cluster_id TEXT,
    person_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT,
    consent_flag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    dim INTEGER NOT NULL,
    index_generation INTEGER NOT NULL DEFAULT 1,
    quantization TEXT NOT NULL DEFAULT 'float32',
    vec BLOB NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(asset_id, model_id, index_generation)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_generation ON embeddings(model_id, index_generation);

CREATE TABLE IF NOT EXISTS vector_index_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    backend TEXT NOT NULL,
    model_id TEXT NOT NULL DEFAULT '',
    index_generation INTEGER NOT NULL DEFAULT 1,
    vector_count INTEGER NOT NULL DEFAULT 0,
    dim INTEGER NOT NULL DEFAULT 0,
    quantization TEXT NOT NULL DEFAULT 'float32',
    built_at TEXT,
    build_progress REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'unavailable',
    reason TEXT,
    measured_p95_ms REAL,
    updated_at TEXT NOT NULL,
    UNIQUE(backend, model_id, index_generation)
);

CREATE TABLE IF NOT EXISTS device_bench (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    os TEXT NOT NULL,
    cores INTEGER,
    ram_gb REAL,
    gpu TEXT,
    task TEXT NOT NULL,
    model_rev TEXT,
    ms_per_item REAL NOT NULL,
    measured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('static', 'smart')),
    name TEXT NOT NULL UNIQUE,
    query_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ord INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL,
    yaml TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    mode TEXT NOT NULL,
    root TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    approved_at TEXT,
    applied_at TEXT,
    undone_at TEXT
);

CREATE TABLE IF NOT EXISTS plan_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    asset_id INTEGER REFERENCES assets(id) ON DELETE SET NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    action TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    collision INTEGER NOT NULL DEFAULT 0,
    review_reason TEXT,
    rule_id TEXT,
    hash TEXT,
    tags_json TEXT,
    UNIQUE(plan_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_plan_items_plan ON plan_items(plan_id, seq);

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    action TEXT NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    hash TEXT,
    done_at TEXT,
    undone_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_journal_plan ON journal(plan_id, seq);

CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    payload_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_state ON review_queue(state, severity);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    progress REAL NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    eta_seconds INTEGER,
    throttle_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
