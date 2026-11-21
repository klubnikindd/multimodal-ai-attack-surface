import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import "./App.css";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function req(method: string, path: string, body?: object): Promise<any> {
  const text = await invoke<string>("engine_request", { method, path, body: body ? JSON.stringify(body) : null });
  return JSON.parse(text);
}

function filename(p: string | null): string {
  return p ? (p.split(/[\\/]/).pop() ?? p) : "";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function loadLS<T>(key: string, def: T): T {
  try { const v = localStorage.getItem(key); return v ? JSON.parse(v) as T : def; } catch { return def; }
}
function saveLS(key: string, val: unknown) {
  try { localStorage.setItem(key, JSON.stringify(val)); } catch { /* ignore */ }
}

interface Card {
  id: number;
  kind: string;
  path: string | null;
  taken_at: string | null;
  tags: string[];
  score?: number;
}

type Tab = "gallery" | "rules" | "status" | "review" | "jobs";
const PRESETS = ["date", "family", "wedding", "stock", "video"];

export default function App() {
  const [tab, setTab] = useState<Tab>("gallery");

  const [sourceDir, setSourceDir] = useState<string | null>(null);
  const [destDir, setDestDir] = useState<string | null>(null);

  const [cards, setCards] = useState<Card[]>([]);
  const [thumbs, setThumbs] = useState<Record<number, string>>({});
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<string | null>(() => loadLS("kindFilter", null));
  const [sortBy, setSortBy] = useState<string>(() => loadLS("sortBy", "taken_at"));
  const [selected, setSelected] = useState<number | null>(null);
  const [ratings, setRatings] = useState<Record<number, number>>(() => loadLS("ratings", {}));
  const [marks, setMarks] = useState<Record<number, boolean>>(() => loadLS("marks", {}));

  const [preset, setPreset] = useState("date");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [plan, setPlan] = useState<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [plans, setPlans] = useState<any[]>([]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [sysStatus, setSysStatus] = useState<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [review, setReview] = useState<any[]>([]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [jobs, setJobs] = useState<any[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadGallery = useCallback(async () => {
    setError(null);
    setStatus("Загрузка…");
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const res = await req("GET", "/v1/assets?limit=1000");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setCards((res.items || []).map((a: any) => ({
        id: a.id, kind: a.kind || "?", path: a.path || null, taken_at: a.taken_at || null, tags: a.tags || [],
      })));
      setStatus(null);
    } catch (e) { setError(String(e)); setStatus(null); }
  }, []);

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) { loadGallery(); return; }
    setError(null); setStatus("Поиск…");
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const res = await req("POST", "/v1/search", { query: q, k: 500 });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const hits = (res.hits || []) as any[];
      setCards(hits.map((h) => {
        const f = h.fields || {};
        return { id: h.asset_id, kind: f.kind || "?", path: f.path || null, taken_at: f.taken_at || null, tags: f.tags || [], score: h.score };
      }));
      setStatus(null);
    } catch (e) { setError(String(e)); setStatus(null); }
  }, [loadGallery]);

  const loadStatus = useCallback(async () => { try { setSysStatus(await req("GET", "/v1/system/status")); } catch (e) { setError(String(e)); } }, []);
  const loadReview = useCallback(async () => { try { setReview((await req("GET", "/v1/review?limit=100")).items || []); } catch (e) { setError(String(e)); } }, []);
  const loadJobs = useCallback(async () => { try { setJobs((await req("GET", "/v1/jobs?limit=50")).items || []); } catch (e) { setError(String(e)); } }, []);
  const loadPlans = useCallback(async () => { try { setPlans((await req("GET", "/v1/plans?limit=50")).items || []); } catch (e) { setError(String(e)); } }, []);

  const pickSource = async () => { const d = await open({ directory: true, title: "Выберите папку-источник" }); if (typeof d === "string") setSourceDir(d); };
  const pickDest = async () => { const d = await open({ directory: true, title: "Выберите папку назначения" }); if (typeof d === "string") setDestDir(d); };
  const doScan = async () => {
    if (!sourceDir) return;
    setStatus("Сканирование… (может занять время для большой папки)");
    try {
      await req("POST", "/v1/scan", { root: sourceDir });
      await loadGallery();
      setStatus(null);
    } catch (e) { setError(String(e)); setStatus(null); }
  };
  const revealDest = async () => { if (destDir) await openPath(destDir); else if (sourceDir) await openPath(sourceDir); };

  const revealAsset = async (id: number) => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const res = await req("GET", `/v1/assets/${id}/absolute-path`);
      await revealItemInDir(res.path);
    } catch (e) { setError(String(e)); }
  };

  const buildPlan = async () => {
    setStatus("Построение плана…");
    try {
      const res = await req("POST", "/v1/plans", { preset, library_root: destDir || undefined });
      setPlan(res); await loadPlans(); setStatus(null);
    } catch (e) { setError(String(e)); setStatus(null); }
  };
  const applyPlan = async (id: number) => { setStatus("Применение…"); try { await req("POST", `/v1/plans/${id}/apply`, { force: false }); setStatus(null); } catch (e) { setError(String(e)); setStatus(null); } };
  const undoPlan = async (id: number) => { setStatus("Откат…"); try { await req("POST", `/v1/plans/${id}/undo`); setStatus(null); } catch (e) { setError(String(e)); setStatus(null); } };

  const exportJson = async () => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const res = await req("GET", "/v1/export/catalog?limit=5000");
      const blob = new Blob([JSON.stringify(res, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = "kadr-catalog.json"; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { setError(String(e)); }
  };

  useEffect(() => { loadGallery(); }, [loadGallery]);
  useEffect(() => {
    for (const c of cards) {
      if (c.kind === "photo" && !thumbs[c.id]) {
        invoke<string>("engine_thumbnail", { assetId: c.id })
          .then((b64) => setThumbs((t) => ({ ...t, [c.id]: b64 })))
          .catch(() => {});
      }
    }
  }, [cards, thumbs]);
  useEffect(() => { if (tab === "status") loadStatus(); if (tab === "review") loadReview(); if (tab === "jobs") loadJobs(); if (tab === "rules") loadPlans(); }, [tab, loadStatus, loadReview, loadJobs, loadPlans]);
  useEffect(() => saveLS("kindFilter", kindFilter), [kindFilter]);
  useEffect(() => saveLS("sortBy", sortBy), [sortBy]);

  const kinds = Array.from(new Set(cards.map((c) => c.kind)));
  const filtered = useMemo(() => {
    let list = kindFilter ? cards.filter((c) => c.kind === kindFilter) : cards;
    const s = [...list];
    if (sortBy === "taken_at") s.sort((a, b) => (b.taken_at ?? "").localeCompare(a.taken_at ?? ""));
    else if (sortBy === "name") s.sort((a, b) => filename(a.path).localeCompare(filename(b.path)));
    else if (sortBy === "kind") s.sort((a, b) => a.kind.localeCompare(b.kind));
    return s;
  }, [cards, kindFilter, sortBy]);

  // hotkeys: ←/→ навигация, Esc сброс, 1-5 рейтинг, X метка
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (tab !== "gallery" || filtered.length === 0) return;
      const idx = filtered.findIndex((c) => c.id === selected);
      if (e.key === "ArrowRight") { setSelected(filtered[idx < filtered.length - 1 ? idx + 1 : 0].id); }
      else if (e.key === "ArrowLeft") { setSelected(filtered[idx > 0 ? idx - 1 : filtered.length - 1].id); }
      else if (e.key === "Escape") { setSelected(null); }
      else if (selected != null && e.key >= "1" && e.key <= "5") {
        const r = parseInt(e.key, 10);
        setRatings((rr) => { const n = { ...rr, [selected]: r }; saveLS("ratings", n); return n; });
      } else if (selected != null && (e.key === "x" || e.key === "X")) {
        setMarks((mm) => { const n = { ...mm, [selected]: !mm[selected] }; saveLS("marks", n); return n; });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, filtered, selected]);

  return (
    <main className="app">
      <header className="topbar">
        <h1>Kadr</h1>
        <nav className="tabs">
          {(["gallery", "rules", "status", "review", "jobs"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>
              {t === "gallery" ? "Галерея" : t === "rules" ? "Правила" : t === "status" ? "Состояние" : t === "review" ? "Review" : "Jobs"}
            </button>
          ))}
        </nav>
      </header>

      {status && <p className="status">{status}</p>}
      {error && <p className="error">{error}</p>}

      {tab === "gallery" && (
        <>
          <div className="toolbar">
            <div className="folders">
              <button onClick={pickSource}>Откуда… {sourceDir && <small>{sourceDir}</small>}</button>
              <button onClick={pickDest}>Куда… {destDir && <small>{destDir}</small>}</button>
              <button onClick={doScan} disabled={!sourceDir}>Сканировать</button>
              <button onClick={revealDest}>Открыть папку</button>
            </div>
            <form onSubmit={(e) => { e.preventDefault(); doSearch(query); }}>
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Поиск…" />
              <button type="submit">Найти</button>
              <button type="button" onClick={() => { setQuery(""); loadGallery(); }}>Все</button>
            </form>
            <div className="facets">
              <button className={!kindFilter ? "chip active" : "chip"} onClick={() => setKindFilter(null)}>Все</button>
              {kinds.map((k) => (
                <button key={k} className={kindFilter === k ? "chip active" : "chip"} onClick={() => setKindFilter(kindFilter === k ? null : k)}>{k}</button>
              ))}
            </div>
            <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
              <option value="taken_at">По дате</option>
              <option value="name">По имени</option>
              <option value="kind">По типу</option>
            </select>
          </div>
          <section className="gallery">
            {filtered.map((c) => (
              <figure key={c.id} className={c.id === selected ? "card selected" : "card"} onClick={() => setSelected(c.id)}>
                {thumbs[c.id] ? (
                  <img src={`data:image/jpeg;base64,${thumbs[c.id]}`} alt={filename(c.path)} loading="lazy" />
                ) : (
                  <div className="ph">{c.kind}</div>
                )}
                <figcaption>
                  <div className="nm" title={c.path ?? ""}>{filename(c.path) || `#${c.id}`}</div>
                  <div className="mt">
                    {c.kind} · {c.taken_at?.slice(0, 10) ?? "—"}
                    {ratings[c.id] ? ` · ★${ratings[c.id]}` : ""}
                    {marks[c.id] ? " · ✗" : ""}
                    {c.score != null ? ` · ${c.score.toFixed(2)}` : ""}
                  </div>
                  <button
                    className="reveal"
                    title="Показать в папке"
                    onClick={(e) => { e.stopPropagation(); revealAsset(c.id); }}
                  >
                    📂
                  </button>
                </figcaption>
              </figure>
            ))}
            {filtered.length === 0 && !status && <p className="empty">Нет файлов — выберите «Откуда» и нажмите «Сканировать».</p>}
          </section>
        </>
      )}

      {tab === "rules" && (
        <section className="panel">
          <h2>Правила и планы</h2>
          <div className="toolbar">
            <button onClick={pickDest}>Куда (библиотека)… {destDir && <small>{destDir}</small>}</button>
            <select value={preset} onChange={(e) => setPreset(e.target.value)}>
              {PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <button onClick={buildPlan}>Построить план</button>
          </div>
          {plan?.tree_text && <pre className="tree">{plan.tree_text}</pre>}
          <h3>История планов</h3>
          {plans.length === 0 && <p className="empty">Планов нет.</p>}
          {plans.map((p) => (
            <div key={p.id} className="row">
              <span className="kind">#{p.id} {p.summary ?? ""}</span>
              <span className="st">{p.state}</span>
              <button onClick={() => applyPlan(p.id)}>Применить</button>
              <button onClick={() => undoPlan(p.id)}>Откатить</button>
            </div>
          ))}
        </section>
      )}

      {tab === "status" && sysStatus && (
        <section className="panel">
          <h2>Состояние системы</h2>
          <dl className="kv">
            <dt>Продукт</dt><dd>{sysStatus.product} {sysStatus.version}</dd>
            <dt>Профиль</dt><dd>{sysStatus.profile?.profile ?? "—"}</dd>
            <dt>Активов</dt><dd>{sysStatus.stats?.assets ?? "—"}</dd>
            <dt>Векторный индекс</dt><dd>{sysStatus.vector_index?.backend ?? "—"} · {sysStatus.vector_index?.status ?? "—"}{sysStatus.vector_index?.vector_count != null ? ` (${sysStatus.vector_index.vector_count})` : ""}</dd>
            <dt>Офлайн</dt><dd>{sysStatus.offline ? "да" : "нет"}</dd>
          </dl>
          <button onClick={exportJson}>Экспорт каталога (JSON)</button>
          <h3>Модели</h3>
          <table className="tbl">
            <thead><tr><th>id</th><th>роль</th><th>статус</th><th>лицензия</th></tr></thead>
            <tbody>
              {(sysStatus.models?.models || []).map((m: { id: string; role: string; enabled: boolean; license: string }) => (
                <tr key={m.id}><td>{m.id}</td><td>{m.role}</td><td>{m.enabled ? "enabled" : "disabled"}</td><td>{m.license}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {tab === "review" && (
        <section className="panel">
          <h2>Review inbox</h2>
          {review.length === 0 && <p className="empty">Нет элементов на ревью.</p>}
          {review.map((r) => (
            <div key={r.id} className="row">
              <span className="sev">{r.severity ?? "?"}</span>
              <span className="reason">{r.reason ?? r.state ?? `#${r.id}`}</span>
              <span className="st">{r.state}</span>
            </div>
          ))}
        </section>
      )}

      {tab === "jobs" && (
        <section className="panel">
          <h2>Jobs</h2>
          {jobs.length === 0 && <p className="empty">Нет задач.</p>}
          {jobs.map((j) => (
            <div key={j.id} className="row">
              <span className="kind">{j.kind}</span>
              <span className="st">{j.state}</span>
              <span className="prog">{j.progress != null ? `${j.progress}%` : ""}</span>
            </div>
          ))}
        </section>
      )}
    </main>
  );
}
