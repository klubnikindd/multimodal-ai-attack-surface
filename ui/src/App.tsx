import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import "./App.css";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function req(method: string, path: string, body?: object): Promise<any> {
  const text = await invoke<string>("engine_request", {
    method,
    path,
    body: body ? JSON.stringify(body) : null,
  });
  return JSON.parse(text);
}

function filename(p: string | null): string {
  return p ? (p.split(/[\\/]/).pop() ?? p) : "";
}

interface Card {
  id: number;
  kind: string;
  path: string | null;
  taken_at: string | null;
  tags: string[];
  score?: number;
}

type Tab = "gallery" | "status" | "review" | "jobs";

export default function App() {
  const [tab, setTab] = useState<Tab>("gallery");

  // gallery
  const [cards, setCards] = useState<Card[]>([]);
  const [thumbs, setThumbs] = useState<Record<number, string>>({});
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<string | null>(null);

  // other screens
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
      const res = await req("GET", "/v1/assets?limit=300");
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setCards((res.items || []).map((a: any) => ({
        id: a.id,
        kind: a.kind || "?",
        path: a.path || null,
        taken_at: a.taken_at || null,
        tags: a.tags || [],
      })));
      setStatus(null);
    } catch (e) {
      setError(String(e));
      setStatus(null);
    }
  }, []);

  const doSearch = useCallback(
    async (q: string) => {
      if (!q.trim()) {
        loadGallery();
        return;
      }
      setError(null);
      setStatus("Поиск…");
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const res = await req("POST", "/v1/search", { query: q, k: 200 });
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const hits = (res.hits || []) as any[];
        setCards(hits.map((h) => {
          const f = h.fields || {};
          return {
            id: h.asset_id,
            kind: f.kind || "?",
            path: f.path || null,
            taken_at: f.taken_at || null,
            tags: f.tags || [],
            score: h.score,
          };
        }));
        setStatus(null);
      } catch (e) {
        setError(String(e));
        setStatus(null);
      }
    },
    [loadGallery],
  );

  const loadStatus = useCallback(async () => {
    setError(null);
    try {
      setSysStatus(await req("GET", "/v1/system/status"));
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const loadReview = useCallback(async () => {
    setError(null);
    try {
      const res = await req("GET", "/v1/review?limit=100");
      setReview(res.items || []);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const loadJobs = useCallback(async () => {
    setError(null);
    try {
      const res = await req("GET", "/v1/jobs?limit=50");
      setJobs(res.items || []);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    loadGallery();
  }, [loadGallery]);

  useEffect(() => {
    for (const c of cards) {
      if (c.kind === "photo" && !thumbs[c.id]) {
        invoke<string>("engine_thumbnail", { assetId: c.id })
          .then((b64) => setThumbs((t) => ({ ...t, [c.id]: b64 })))
          .catch(() => {});
      }
    }
  }, [cards, thumbs]);

  useEffect(() => {
    if (tab === "status") loadStatus();
    if (tab === "review") loadReview();
    if (tab === "jobs") loadJobs();
  }, [tab, loadStatus, loadReview, loadJobs]);

  const kinds = Array.from(new Set(cards.map((c) => c.kind)));
  const filtered = kindFilter ? cards.filter((c) => c.kind === kindFilter) : cards;

  return (
    <main className="app">
      <header className="topbar">
        <h1>Kadr</h1>
        <nav className="tabs">
          {(["gallery", "status", "review", "jobs"] as Tab[]).map((t) => (
            <button
              key={t}
              className={tab === t ? "tab active" : "tab"}
              onClick={() => setTab(t)}
            >
              {t === "gallery" ? "Галерея" : t === "status" ? "Состояние" : t === "review" ? "Review" : "Jobs"}
            </button>
          ))}
        </nav>
      </header>

      {status && <p className="status">{status}</p>}
      {error && <p className="error">{error}</p>}

      {tab === "gallery" && (
        <>
          <div className="toolbar">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                doSearch(query);
              }}
            >
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Поиск (семантика + FTS)…"
              />
              <button type="submit">Найти</button>
              <button type="button" onClick={() => { setQuery(""); loadGallery(); }}>
                Все
              </button>
            </form>
            <div className="facets">
              <button className={!kindFilter ? "chip active" : "chip"} onClick={() => setKindFilter(null)}>
                Все
              </button>
              {kinds.map((k) => (
                <button
                  key={k}
                  className={kindFilter === k ? "chip active" : "chip"}
                  onClick={() => setKindFilter(kindFilter === k ? null : k)}
                >
                  {k}
                </button>
              ))}
            </div>
          </div>
          <section className="gallery">
            {filtered.map((c) => (
              <figure key={c.id} className="card">
                {thumbs[c.id] ? (
                  <img src={`data:image/jpeg;base64,${thumbs[c.id]}`} alt={filename(c.path)} loading="lazy" />
                ) : (
                  <div className="ph">{c.kind}</div>
                )}
                <figcaption>
                  <div className="nm" title={c.path ?? ""}>
                    {filename(c.path) || `#${c.id}`}
                  </div>
                  <div className="mt">
                    {c.kind} · {c.taken_at?.slice(0, 10) ?? "—"}
                    {c.score != null ? ` · ${c.score.toFixed(2)}` : ""}
                  </div>
                  {c.tags?.length > 0 && <div className="tg">{c.tags.slice(0, 5).join(", ")}</div>}
                </figcaption>
              </figure>
            ))}
            {filtered.length === 0 && !status && (
              <p className="empty">Нет файлов — запустите сканирование.</p>
            )}
          </section>
        </>
      )}

      {tab === "status" && sysStatus && (
        <section className="panel">
          <h2>Состояние системы</h2>
          <dl className="kv">
            <dt>Продукт</dt><dd>{sysStatus.product} {sysStatus.version}</dd>
            <dt>Профиль</dt><dd>{sysStatus.profile?.profile ?? "—"}</dd>
            <dt>Активов</dt><dd>{sysStatus.stats?.assets ?? "—"}</dd>
            <dt>Векторный индекс</dt>
            <dd>
              {sysStatus.vector_index?.backend ?? "—"} · {sysStatus.vector_index?.status ?? "—"}
              {sysStatus.vector_index?.vector_count != null ? ` (${sysStatus.vector_index.vector_count} векторов)` : ""}
            </dd>
            <dt>Офлайн</dt><dd>{sysStatus.offline ? "да" : "нет"}</dd>
          </dl>
          <h3>Модели</h3>
          <table className="tbl">
            <thead><tr><th>id</th><th>роль</th><th>статус</th><th>лицензия</th></tr></thead>
            <tbody>
              {(sysStatus.models?.models || []).map((m: { id: string; role: string; enabled: boolean; license: string }) => (
                <tr key={m.id}>
                  <td>{m.id}</td>
                  <td>{m.role}</td>
                  <td>{m.enabled ? "enabled" : "disabled"}</td>
                  <td>{m.license}</td>
                </tr>
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
