import { useEffect, useState, useCallback } from "react";
import { createSource, listEntities, listRuns, listSources, triggerRun } from "./api";

const fmt = (d) => (d ? new Date(d).toLocaleString() : "—");

// Short label for the long OpenRouter model slugs.
const shortModel = (m) => {
  if (!m) return "—";
  if (m === "heuristic") return "heuristic";
  // "anthropic/claude-sonnet-4" -> "claude-sonnet-4"
  return m.includes("/") ? m.split("/").slice(-1)[0] : m;
};

function BackendBadge({ backend, error }) {
  if (error) return <span className="badge err">error</span>;
  if (!backend) return <span className="muted">—</span>;
  const cls = backend === "heuristic" ? "badge local" : "badge cloud";
  return <span className={cls} title={backend}>{shortModel(backend)}</span>;
}

function Sources({ sources, onRun, onSelect, busyId }) {
  return (
    <div className="panel">
      <h2>Sources</h2>
      <table>
        <thead>
          <tr><th>ID</th><th>Label</th><th>URL</th><th>Model</th><th>Identity key</th><th></th></tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.id}>
              <td>{s.id}</td>
              <td>{s.label || <span className="muted">—</span>}</td>
              <td><a href={s.url} target="_blank" rel="noreferrer">{s.url}</a></td>
              <td className="muted">{shortModel(s.model)}</td>
              <td className="muted">{(s.identity_key || []).join(", ") || "—"}</td>
              <td className="row">
                <button onClick={() => onRun(s.id)} disabled={busyId === s.id}>
                  {busyId === s.id ? "Running…" : "Run"}
                </button>
                <button className="secondary" onClick={() => onSelect(s.id)}>Entities</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Runs({ runs }) {
  return (
    <div className="panel">
      <h2>Recent runs</h2>
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Source</th><th>Started</th><th>Finished</th><th>Backend</th>
            <th>Confidence</th><th>Entities</th><th>+/Δ/stale</th><th>Cost</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{r.source_id}</td>
              <td>{fmt(r.started_at)}</td>
              <td>{fmt(r.finished_at)}</td>
              <td><BackendBadge backend={r.backend} error={r.error} /></td>
              <td>{r.confidence != null ? Number(r.confidence).toFixed(2) : "—"}</td>
              <td>{r.entity_count ?? 0}</td>
              <td className="muted">{r.new_count ?? 0}/{r.updated_count ?? 0}/{r.stale_count ?? 0}</td>
              <td>${Number(r.cost_usd ?? 0).toFixed(4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Entities({ sourceId, entities }) {
  if (!sourceId) return null;
  if (entities.length === 0) {
    return (
      <div className="panel">
        <h2>Entities — source {sourceId}</h2>
        <p className="muted">No entities yet. Trigger a run to extract.</p>
      </div>
    );
  }
  const fields = Array.from(
    new Set(entities.flatMap((e) => Object.keys(e.data || {}))),
  );
  return (
    <div className="panel">
      <h2>Entities — source {sourceId}</h2>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            {fields.map((f) => <th key={f}>{f}</th>)}
            <th>Confidence</th>
            <th>Last seen</th>
            <th>State</th>
          </tr>
        </thead>
        <tbody>
          {entities.map((e) => (
            <tr key={e.id}>
              <td>{e.id}</td>
              {fields.map((f) => <td key={f}>{String(e.data?.[f] ?? "")}</td>)}
              <td>{e.confidence != null ? Number(e.confidence).toFixed(2) : "—"}</td>
              <td>{fmt(e.last_seen)}</td>
              <td>{e.stale ? <span className="badge err">stale</span> : <span className="badge local">live</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const MODEL_CHOICES = [
  "",
  "anthropic/claude-sonnet-4",
  "openai/gpt-4o",
  "meta-llama/llama-3.3-70b-instruct",
  "google/gemini-2.0-flash-001",
];

function NewSourceForm({ onCreated }) {
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [identityKey, setIdentityKey] = useState("");
  const [model, setModel] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!url) return;
    setBusy(true);
    try {
      await createSource({
        url,
        label: label || null,
        identity_key: identityKey ? identityKey.split(",").map((s) => s.trim()).filter(Boolean) : [],
        model: model || null,
      });
      setUrl(""); setLabel(""); setIdentityKey(""); setModel("");
      onCreated();
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel" onSubmit={submit}>
      <h2>Add source</h2>
      <div className="row">
        <input className="grow" placeholder="https://example.com/listings" value={url} onChange={(e) => setUrl(e.target.value)} />
        <input placeholder="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} />
        <input placeholder="Identity key (comma-separated)" value={identityKey} onChange={(e) => setIdentityKey(e.target.value)} />
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          {MODEL_CHOICES.map((m) => (
            <option key={m} value={m}>{m ? shortModel(m) : "default model"}</option>
          ))}
        </select>
        <button disabled={busy || !url}>{busy ? "Adding…" : "Add"}</button>
      </div>
    </form>
  );
}

export default function App() {
  const [sources, setSources] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selected, setSelected] = useState(null);
  const [entities, setEntities] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([listSources(), listRuns()]);
      setSources(s);
      setRuns(r);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    listEntities(selected).then(setEntities).catch((e) => setError(e.message));
  }, [selected, runs]);

  const handleRun = async (id) => {
    setBusyId(id);
    try {
      await triggerRun(id);
      await refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="app">
      <header>
        <h1>WebHarvest</h1>
        <nav>
          <a href="/grafana/" target="_blank" rel="noreferrer">Grafana</a>
          <a href="/prometheus/" target="_blank" rel="noreferrer">Prometheus</a>
        </nav>
      </header>

      {error && <div className="panel" style={{ borderColor: "var(--err)" }}><span className="badge err">error</span> {error}</div>}

      <NewSourceForm onCreated={refresh} />
      <Sources sources={sources} onRun={handleRun} onSelect={setSelected} busyId={busyId} />
      <Runs runs={runs} />
      <Entities sourceId={selected} entities={entities} />
    </div>
  );
}
