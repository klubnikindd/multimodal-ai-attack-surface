import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import "./App.css";

interface Card {
  id: number;
  kind: string;
  path: string | null;
  taken_at: string | null;
  tags: string[];
  score?: number;
  reason?: string;
}

async function req(method: string, path: string, body?: object): Promise<Record<string, unknown>> {
  const text = await invoke<string>("engine_request", {
    method,
    path,
    body: body ? JSON.stringify(body) : null,
  });
  return JSON.parse(text) as Record<string, unknown>;
}

function filename(p: string | null): string {
  return p ? (p.split(/[\\/]/).pop() ?? p) : "";
}

export default function App() {
  const [cards, setCards] = useState<Card[]>([]);
  const [thumbs, setThumbs] = useState<Record<number, string>>({});
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadGallery = useCallback(async () => {
    setError(null);
    setStatus("Загрузка…");
    try {
      const res = await req("GET", "/v1/assets?limit=120");
      const items = (res.items as Array<Record<string, unknown>>) || [];
      setCards(items.map((a) => ({
        id: a.id as number,
        kind: (a.kind as string) || "?",
        path: (a.path as string) || null,
        taken_at: (a.taken_at as string) || null,
        tags: (a.tags as string[]) || [],
      })));
      setStatus(null);
    } catch (e) {
      setError(String(e));
      setStatus(null);
    }
  }, []);

  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      loadGallery();
      return;
    }
    setError(null);
    setStatus("Поиск…");
    try {
      const res = await req("POST", "/v1/search", { query: q, k: 120 });
      const hits = (res.hits as Array<Record<string, unknown>>) || [];
      setCards(hits.map((h) => {
        const f = (h.fields as Record<string, unknown>) || {};
        return {
          id: h.asset_id as number,
          kind: (f.kind as string) || "?",
          path: (f.path as string) || null,
          taken_at: (f.taken_at as string) || null,
          tags: (f.tags as string[]) || [],
          score: h.score as number,
          reason: h.reason as string,
        };
      }));
      setStatus(null);
    } catch (e) {
      setError(String(e));
      setStatus(null);
    }
  }, [loadGallery]);

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

  return (
    <main className="app">
      <header className="topbar">
        <h1>Kadr</h1>
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
          <button
            type="button"
            onClick={() => {
              setQuery("");
              loadGallery();
            }}
          >
            Все
          </button>
        </form>
      </header>
      {status && <p className="status">{status}</p>}
      {error && <p className="error">{error}</p>}
      <section className="gallery">
        {cards.map((c) => (
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
        {cards.length === 0 && !status && (
          <p className="empty">Нет файлов — запустите сканирование.</p>
        )}
      </section>
    </main>
  );
}
