import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
import "./App.css";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function req(method: string, path: string, body?: object): Promise<any> {
  for (let attempt = 0; attempt < 40; attempt++) {
    try {
      const text = await invoke<string>("engine_request", { method, path, body: body ? JSON.stringify(body) : null });
      return JSON.parse(text);
    } catch (e) {
      if (String(e).includes("engine not ready") && attempt < 39) {
        await new Promise((r) => setTimeout(r, 500));
        continue;
      }
      throw e;
    }
  }
  throw new Error("engine not ready (timeout)");
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
  gps_lat: number | null;
  gps_lon: number | null;
  score?: number;
}

type Tab = "gallery" | "rules" | "map" | "timeline" | "status" | "review" | "jobs";
const PRESETS = ["date", "family", "wedding", "stock", "video"];
const COLORS = ["red", "gold", "green", "dodgerblue", "purple"];

function MapView({ assets, colors }: { assets: Card[]; colors: Record<number, number> }) {
  const pts = assets.filter((a) => a.gps_lat != null && a.gps_lon != null);
  if (pts.length === 0) {
    return <div className="map" style={{ height: 400, background: "#111", color: "#888", display: "grid", placeItems: "center" }}>нет GPS-координат</div>;
  }
  const W = 800, H = 500, PAD = 24;
  const lats = pts.map((a) => a.gps_lat as number);
  const lons = pts.map((a) => a.gps_lon as number);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const x = (lon: number) => PAD + ((lon - minLon) / (maxLon - minLon || 1)) * (W - 2 * PAD);
  const y = (lat: number) => PAD + ((maxLat - lat) / (maxLat - minLat || 1)) * (H - 2 * PAD);
  return (
    <svg className="map" viewBox={"0 0 " + W + " " + H} style={{ width: "100%", height: "auto", background: "#111", borderRadius: 8 }}>
      {pts.map((a) => (
        <circle key={a.id} cx={x(a.gps_lon as number)} cy={y(a.gps_lat as number)} r={4} fill={colors[a.id] ? COLORS[colors[a.id] - 1] : "#4a8"}>
          <title>{filename(a.path)}</title>
        </circle>
      ))}
      <text x={W / 2} y={H - 6} textAnchor="middle" fill="#888" fontSize={11}>офлайн: относительные GPS-координаты (lat/lon)</text>
    </svg>
  );
}

const FIELDS = ["kind", "ai.scene_type", "ai.event_type", "camera.make", "camera.model"];
const OPS = ["eq", "in", "gte", "lte", "contains", "exists"];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function whenToConditions(when: any): { field: string; op: string; value: string }[] {
  if (!when || typeof when !== "object" || when.all || when.any || when.not) return [];
  return Object.entries(when).map(([field, cond]) => {
    const [op, val] = Object.entries(cond as Record<string, unknown>)[0] || ["eq", ""];
    return { field, op, value: Array.isArray(val) ? val.join(", ") : String(val) };
  });
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function conditionsToWhen(conds: { field: string; op: string; value: string }[]): any {
  const when: Record<string, Record<string, unknown>> = {};
  for (const c of conds) {
    if (!c.field) continue;
    if (c.op === "exists") { when[c.field] = { exists: c.value === "true" }; continue; }
    let val: unknown = c.value;
    if (c.op === "in") val = c.value.split(",").map((s) => s.trim()).filter(Boolean);
    else if (c.op === "gte" || c.op === "lte") val = parseFloat(c.value) || 0;
    when[c.field] = { [c.op]: val };
  }
  return Object.keys(when).length ? when : null;
}

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
  const [lightbox, setLightbox] = useState<number | null>(null);
  const [largeThumbs, setLargeThumbs] = useState<Record<number, string>>({});
  const [selectedReview, setSelectedReview] = useState<Set<number>>(new Set());
  const [ratings, setRatings] = useState<Record<number, number>>(() => loadLS("ratings", {}));
  const [marks, setMarks] = useState<Record<number, boolean>>(() => loadLS("marks", {}));
  const [tagged, setTagged] = useState<Record<number, boolean>>(() => loadLS("tagged", {}));
  const [colors, setColors] = useState<Record<number, number>>(() => loadLS("colors", {}));

  const [preset, setPreset] = useState("date");
  const [mode, setMode] = useState("copy");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [presetsData, setPresetsData] = useState<Record<string, any>>({});
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [rulesData, setRulesData] = useState<any>(null);
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
    setError(null); setStatus("Загрузка…");
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const res = await req("GET", "/v1/assets?limit=5000");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setCards((res.items || []).map((a: any) => ({
        id: a.id, kind: a.kind || "?", path: a.path || null, taken_at: a.taken_at || null,
        tags: a.tags || [], gps_lat: a.gps_lat ?? null, gps_lon: a.gps_lon ?? null,
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
        return { id: h.asset_id, kind: f.kind || "?", path: f.path || null, taken_at: f.taken_at || null, tags: f.tags || [], gps_lat: null, gps_lon: null, score: h.score };
      }));
      setStatus(null);
    } catch (e) { setError(String(e)); setStatus(null); }
  }, [loadGallery]);

  const loadStatus = useCallback(async () => { try { setSysStatus(await req("GET", "/v1/system/status")); } catch (e) { setError(String(e)); } }, []);
  const loadReview = useCallback(async () => { try { setReview((await req("GET", "/v1/review?limit=100")).items || []); } catch (e) { setError(String(e)); } }, []);
  const loadJobs = useCallback(async () => { try { setJobs((await req("GET", "/v1/jobs?limit=50")).items || []); } catch (e) { setError(String(e)); } }, []);
  const loadPlans = useCallback(async () => { try { setPlans((await req("GET", "/v1/plans?limit=50")).items || []); } catch (e) { setError(String(e)); } }, []);
  const loadPresets = useCallback(async () => { try { setPresetsData((await req("GET", "/v1/presets")).presets || {}); } catch (e) { setError(String(e)); } }, []);

  const pickSource = async () => { const d = await open({ directory: true, title: "Выберите папку-источник" }); if (typeof d === "string") setSourceDir(d); };
  const pickDest = async () => { const d = await open({ directory: true, title: "Выберите папку назначения" }); if (typeof d === "string") setDestDir(d); };
  const doScan = async () => {
    if (!sourceDir) return;
    setStatus("Сканирование… (может занять время для большой папки)");
    try { await req("POST", "/v1/scan", { root: sourceDir }); await loadGallery(); setStatus(null); }
    catch (e) { setError(String(e)); setStatus(null); }
  };
  const revealDest = async () => { if (destDir) await openPath(destDir); else if (sourceDir) await openPath(sourceDir); };
  const revealAsset = async (id: number) => {
    try { /* eslint-disable-next-line @typescript-eslint/no-explicit-any */ const res = await req("GET", `/v1/assets/${id}/absolute-path`); await revealItemInDir(res.path); }
    catch (e) { setError(String(e)); }
  };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const updateSafety = (key: string, val: any) => setRulesData((d: any) => (d ? { ...d, safety: { ...d.safety, [key]: val } } : d));
  const updateRulePath = (idx: number, path: string) => setRulesData((d: any) => {
    if (!d) return d;
    const rules = [...d.rules];
    rules[idx] = { ...rules[idx], then: { ...rules[idx].then, path } };
    return { ...d, rules };
  });
  const addRule = () => setRulesData((d: any) => (d ? { ...d, rules: [...d.rules, { id: `custom${Date.now()}`, when: null, then: { path: "Custom/{date:%Y/%Y-%m}", tags: [] } }] } : d));
  const removeRule = (idx: number) => setRulesData((d: any) => (d ? { ...d, rules: d.rules.filter((_: any, i: number) => i !== idx) } : d));
  const updateCondition = (ruleIdx: number, condIdx: number, key: string, val: string) => setRulesData((d: any) => {
    if (!d) return d;
    const rules = [...d.rules];
    const conds = whenToConditions(rules[ruleIdx].when);
    if (rules[ruleIdx].when && conds.length === 0) return d; // вложенные all/any — не редактируем
    conds[condIdx] = { ...conds[condIdx], [key]: val };
    rules[ruleIdx] = { ...rules[ruleIdx], when: conditionsToWhen(conds) };
    return { ...d, rules };
  });
  const addCondition = (ruleIdx: number) => setRulesData((d: any) => {
    if (!d) return d;
    const rules = [...d.rules];
    const conds = whenToConditions(rules[ruleIdx].when);
    conds.push({ field: "kind", op: "eq", value: "photo" });
    rules[ruleIdx] = { ...rules[ruleIdx], when: conditionsToWhen(conds) };
    return { ...d, rules };
  });
  const removeCondition = (ruleIdx: number, condIdx: number) => setRulesData((d: any) => {
    if (!d) return d;
    const rules = [...d.rules];
    const conds = whenToConditions(rules[ruleIdx].when).filter((_, i) => i !== condIdx);
    rules[ruleIdx] = { ...rules[ruleIdx], when: conditionsToWhen(conds) };
    return { ...d, rules };
  });

  const buildPlan = async () => {
    setStatus("Построение плана…");
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const body: any = { library_root: destDir || undefined };
      if (rulesData) {
        body.rules = rulesData;
      } else {
        body.preset = preset;
      }
      const res = await req("POST", "/v1/plans", body);
      setPlan(res); await loadPlans(); setStatus(null);
    } catch (e) { setError(String(e)); setStatus(null); }
  };
  const applyPlan = async (id: number) => { setStatus("Применение…"); try { await req("POST", `/v1/plans/${id}/apply`, { force: false }); setStatus(null); } catch (e) { setError(String(e)); setStatus(null); } };
  const undoPlan = async (id: number) => { setStatus("Откат…"); try { await req("POST", `/v1/plans/${id}/undo`); setStatus(null); } catch (e) { setError(String(e)); setStatus(null); } };

    const cancelJob = async (id: number) => { try { await req("POST", `/v1/jobs/${id}/cancel`); await loadJobs(); } catch (e) { setError(String(e)); } };
  const toggleReview = (id: number) => setSelectedReview((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const resolveSelected = async () => {
    for (const id of selectedReview) { try { await req("POST", `/v1/review/${id}/resolve`); } catch (e) { setError(String(e)); } }
    setSelectedReview(new Set());
    await loadReview();
  };
  const exportCsv = () => {
    const header = ["id","kind","path","taken_at","tags","gps_lat","gps_lon"];
    const rows = cards.map((c) => [c.id, c.kind, `"${(c.path || "").replace(/"/g, '""')}"`, c.taken_at || "", `"${(c.tags || []).join("; ")}"`, c.gps_lat ?? "", c.gps_lon ?? ""]);
    const csv = [header.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = "kadr-catalog.csv"; a.click();
    URL.revokeObjectURL(url);
  };

  const exportJson = async () => {
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const res = await req("GET", "/v1/export/catalog?limit=5000");
      const blob = new Blob([JSON.stringify(res, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a"); a.href = url; a.download = "kadr-catalog.json"; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { setError(String(e)); }
  };

  useEffect(() => { loadGallery(); }, [loadGallery]);
  useEffect(() => {
    for (const c of cards) {
      if (c.kind === "photo" && !thumbs[c.id]) {
        invoke<string>("engine_thumbnail", { assetId: c.id }).then((b64) => setThumbs((t) => ({ ...t, [c.id]: b64 }))).catch(() => {});
      }
    }
  }, [cards, thumbs]);
  useEffect(() => { if (tab === "status") loadStatus(); if (tab === "review") loadReview(); if (tab === "jobs") loadJobs(); if (tab === "rules") { loadPlans(); loadPresets(); } }, [tab, loadStatus, loadReview, loadJobs, loadPlans]);
  useEffect(() => saveLS("kindFilter", kindFilter), [kindFilter]);
  useEffect(() => saveLS("sortBy", sortBy), [sortBy]);
  useEffect(() => {
    if (tab !== "jobs") return;
    const id = setInterval(loadJobs, 3000);
    return () => clearInterval(id);
  }, [tab, loadJobs]);
  useEffect(() => {
    const id = lightbox;
    if (id != null && !largeThumbs[id]) {
      invoke<string>("engine_thumbnail", { assetId: id, size: 1024 })
        .then((b64) => setLargeThumbs((t) => ({ ...t, [id]: b64 })))
        .catch(() => {});
    }
  }, [lightbox, largeThumbs]);
  useEffect(() => {
    const p = presetsData[preset];
    if (p) {
      setRulesData({
        safety: { ...(p.safety || {}), mode },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        rules: (p.rules || []).map((r: any) => ({ ...r, then: { ...(r.then || {}) } })),
      });
    }
  }, [preset, mode, presetsData]);

  const kinds = Array.from(new Set(cards.map((c) => c.kind)));
  const filtered = useMemo(() => {
    let list = kindFilter ? cards.filter((c) => c.kind === kindFilter) : cards;
    const s = [...list];
    if (sortBy === "taken_at") s.sort((a, b) => (b.taken_at ?? "").localeCompare(a.taken_at ?? ""));
    else if (sortBy === "name") s.sort((a, b) => filename(a.path).localeCompare(filename(b.path)));
    else if (sortBy === "kind") s.sort((a, b) => a.kind.localeCompare(b.kind));
    return s;
  }, [cards, kindFilter, sortBy]);

  const gpsAssets = useMemo(() => cards.filter((c) => c.gps_lat != null && c.gps_lon != null), [cards]);
  const timeline = useMemo(() => {
    const g: Record<string, Card[]> = {};
    for (const c of cards) {
      const key = (c.taken_at || "—").slice(0, 7);
      (g[key] = g[key] || []).push(c);
    }
    return Object.entries(g).sort((a, b) => b[0].localeCompare(a[0]));
  }, [cards]);

  // hotkeys: ←/→ навигация, Esc сброс, 1-5 рейтинг, X метка, Ctrl+1..5 цвет
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (tab !== "gallery" || filtered.length === 0) return;
      const idx = filtered.findIndex((c) => c.id === selected);
      if (e.key === "ArrowRight") setSelected(filtered[idx < filtered.length - 1 ? idx + 1 : 0].id);
      else if (e.key === "ArrowLeft") setSelected(filtered[idx > 0 ? idx - 1 : filtered.length - 1].id);
      else if (e.key === "Escape") { setSelected(null); setLightbox(null); }
      else if (selected != null && !e.ctrlKey && e.key >= "1" && e.key <= "5") {
        const r = parseInt(e.key, 10);
        setRatings((rr) => { const n = { ...rr, [selected]: r }; saveLS("ratings", n); return n; });
      } else if (selected != null && e.ctrlKey && e.key >= "1" && e.key <= "5") {
        const c = parseInt(e.key, 10);
        setColors((cc) => { const n = { ...cc, [selected]: c }; saveLS("colors", n); return n; });
      } else if (selected != null && (e.key === "x" || e.key === "X")) {
        setMarks((mm) => { const n = { ...mm, [selected]: !mm[selected] }; saveLS("marks", n); return n; });
      } else if (selected != null && (e.key === "t" || e.key === "T")) {
        setTagged((tt) => { const n = { ...tt, [selected]: !tt[selected] }; saveLS("tagged", n); return n; });
      } else if (selected != null && e.key === " ") {
        e.preventDefault();
        setLightbox(selected);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tab, filtered, selected]);

  const tabLabel = (t: Tab) => t === "gallery" ? "Галерея" : t === "rules" ? "Правила" : t === "map" ? "Карта" : t === "timeline" ? "Таймлайн" : t === "status" ? "Состояние" : t === "review" ? "Review" : "Jobs";

  return (
    <main className="app">
      <header className="topbar">
        <h1>Kadr</h1>
        <nav className="tabs">
          {(["gallery", "rules", "map", "timeline", "status", "review", "jobs"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "tab active" : "tab"} onClick={() => setTab(t)}>{tabLabel(t)}</button>
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
              <figure key={c.id} className={c.id === selected ? "card selected" : "card"} onClick={() => { setSelected(c.id); setLightbox(c.id); }}>
                {colors[c.id] && <span className="dot" style={{ background: COLORS[colors[c.id] - 1] }} />}
                {tagged[c.id] && <span className="tagged">T</span>}
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
                  </div>
                  <button className="reveal" title="Показать в папке" onClick={(e) => { e.stopPropagation(); revealAsset(c.id); }}>📂</button>
                </figcaption>
              </figure>
            ))}
            {filtered.length === 0 && !status && <p className="empty">Нет файлов — выберите «Откуда» и нажмите «Сканировать».</p>}
          </section>
        </>
      )}

      {tab === "map" && (
        <section className="panel">
          <h2>Карта (GPS)</h2>
          {gpsAssets.length === 0 ? (
            <p className="empty">Нет фото с GPS-координатами.</p>
          ) : (
            <MapView assets={gpsAssets} colors={colors} />
          )}
        </section>
      )}

      {tab === "timeline" && (
        <section className="panel">
          <h2>Таймлайн</h2>
          {timeline.length === 0 && <p className="empty">Нет файлов.</p>}
          {timeline.map(([date, items]) => (
            <div key={date}>
              <h3>{date}</h3>
              <div className="tl-grid">
                {items.map((c) => (
                  <figure key={c.id} className="card" onClick={() => setSelected(c.id)}>
                    {thumbs[c.id] ? <img src={`data:image/jpeg;base64,${thumbs[c.id]}`} alt="" loading="lazy" /> : <div className="ph">{c.kind}</div>}
                    <figcaption><div className="nm">{filename(c.path)}</div></figcaption>
                  </figure>
                ))}
              </div>
            </div>
          ))}
        </section>
      )}

      {tab === "rules" && (
        <section className="panel">
          <h2>Правила и планы</h2>
          <div className="toolbar">
            <button onClick={pickDest}>Куда (библиотека)… {destDir && <small>{destDir}</small>}</button>
            <select value={preset} onChange={(e) => setPreset(e.target.value)}>{PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}</select>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="copy">Копировать</option>
              <option value="move">Переместить</option>
              <option value="hardlink">Жёсткая ссылка</option>
            </select>
            <button onClick={buildPlan}>Построить план</button>
          </div>
          {rulesData && (
            <>
              <h3>Правила (редактируемые)</h3>
              <div className="safety">
                <label>Коллизии:
                  <select value={rulesData.safety?.collision ?? "suffix"} onChange={(e) => updateSafety("collision", e.target.value)}>
                    {["suffix", "skip", "ask", "hash"].map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                </label>
                <label>
                  <input type="checkbox" checked={!!rulesData.safety?.never_delete} onChange={(e) => updateSafety("never_delete", e.target.checked)} />
                  Никогда не удалять оригиналы
                </label>
              </div>
              <table className="tbl">
                <thead><tr><th>правило</th><th>куда (шаблон)</th><th>условие</th><th></th></tr></thead>
                <tbody>
                  {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
                  {rulesData.rules.map((r: any, i: number) => (
                    <tr key={r.id}>
                      <td>{r.id}</td>
                      <td><input value={r.then?.path ?? ""} onChange={(e) => updateRulePath(i, e.target.value)} /></td>
                      <td className="when">
                      {whenToConditions(r.when).map((c: { field: string; op: string; value: string }, ci: number) => (
                        <div key={ci} className="cond">
                          <select value={c.field} onChange={(e) => updateCondition(i, ci, "field", e.target.value)}>{FIELDS.map((f) => <option key={f} value={f}>{f}</option>)}</select>
                          <select value={c.op} onChange={(e) => updateCondition(i, ci, "op", e.target.value)}>{OPS.map((o) => <option key={o} value={o}>{o}</option>)}</select>
                          {c.op === "exists" ? (
                            <select value={c.value} onChange={(e) => updateCondition(i, ci, "value", e.target.value)}><option value="true">true</option><option value="false">false</option></select>
                          ) : (
                            <input value={c.value} placeholder="значение" onChange={(e) => updateCondition(i, ci, "value", e.target.value)} />
                          )}
                          <button onClick={() => removeCondition(i, ci)}>✕</button>
                        </div>
                      ))}
                      {r.when && whenToConditions(r.when).length === 0 && <code>{JSON.stringify(r.when)}</code>}
                      <button onClick={() => addCondition(i)}>+ условие</button>
                    </td>
                      <td><button onClick={() => removeRule(i)}>✕</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button onClick={addRule}>+ Добавить правило</button>
            </>
          )}
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
          <button onClick={exportCsv}>Экспорт (CSV)</button>
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
            {review.length > 0 && <button onClick={resolveSelected} disabled={selectedReview.size === 0}>Подтвердить выбранные ({selectedReview.size})</button>}
          {review.length === 0 && <p className="empty">Нет элементов на ревью.</p>}
          {review.map((r) => (
            <div key={r.id} className="row">
              <input type="checkbox" checked={selectedReview.has(r.id)} onChange={() => toggleReview(r.id)} />
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
              <span className="prog">{j.progress != null ? `${j.progress}%` : ""}</span> {(j.state === "running" || j.state === "queued") && <button onClick={() => cancelJob(j.id)}>Отменить</button>}
            </div>
          ))}
        </section>
      )}
      {lightbox != null && (
        <div className="lightbox" onClick={() => setLightbox(null)}>
          <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
            {largeThumbs[lightbox] ? <img src={"data:image/jpeg;base64," + largeThumbs[lightbox]} alt="" /> : <div className="ph">Загрузка…</div>}
            <div className="lightbox-meta">{filename(cards.find((c) => c.id === lightbox)?.path ?? null)}</div>
            <button className="lightbox-close" onClick={() => setLightbox(null)}>✕</button>
          </div>
        </div>
      )}
    </main>
  );
}
