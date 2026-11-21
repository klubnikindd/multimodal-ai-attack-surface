import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import "./App.css";

interface EngineStatus {
  running: boolean;
  base_url: string | null;
}

function App() {
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    invoke<EngineStatus>("engine_status")
      .then(setStatus)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="container">
      <h1>Kadr</h1>
      <p>Engine (Python sidecar):</p>
      <dl>
        <dt>running</dt>
        <dd>{status ? String(status.running) : "…"}</dd>
        <dt>base_url</dt>
        <dd>{status?.base_url ?? "—"}</dd>
      </dl>
      {error && <p className="error">{error}</p>}
    </main>
  );
}

export default App;
