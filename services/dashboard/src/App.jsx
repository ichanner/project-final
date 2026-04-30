import { useEffect, useState, useCallback, useMemo } from "react";
import { createSource, listEntities, listRuns, listSources, triggerRun } from "./api";

const fmt = (d) => (d ? new Date(d).toLocaleString() : "—");
const shortModel = (m) => {
  if (!m) return "—";
  return m.includes("/") ? m.split("/").slice(-1)[0] : m;
};

const ALL_MODELS = [
  "anthropic/claude-sonnet-4",
  "openai/gpt-4o",
  "meta-llama/llama-3.3-70b-instruct",
  "google/gemini-2.0-flash-001",
];

function Badge({ kind, children, title }) {
  const cls = kind === "err" ? "badge err" : kind === "primary" ? "badge cloud" : "badge local";
  return <span className={cls} title={title}>{children}</span>;
}

function Sources({ sources, onRun, onSelect, busyId }) {
  return (
    <div className="panel">
      <h2>Sources</h2>
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Label</th><th>URL</th>
            <th>Primary</th><th>Challengers</th><th>Identity key</th><th></th>
          </tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr key={s.id}>
              <td>{s.id}</td>
              <td>{s.label || <span className="muted">—</span>}</td>
              <td><a href={s.url} target="_blank" rel="noreferrer">{s.url}</a></td>
              <td>{s.primary_model ? <Badge kind="primary" title={s.primary_model}>{shortModel(s.primary_model)}</Badge> : <span className="muted">—</span>}</td>
              <td className="muted">
                {(s.comparison_models || []).length === 0
                  ? "—"
                  : (s.comparison_models || []).map(shortModel).join(", ")}
              </td>
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
  // Group runs by snapshot_id so the bake-off rows visually cluster.
  const grouped = useMemo(() => {
    const out = [];
    let last = null;
    for (const r of runs) {
      const key = r.snapshot_id ?? `solo-${r.id}`;
      if (!last || last.key !== key) {
        last = { key, rows: [] };
        out.push(last);
      }
      last.rows.push(r);
    }
    return out;
  }, [runs]);

  return (
    <div className="panel">
      <h2>Recent runs (grouped by snapshot)</h2>
      <table>
        <thead>
          <tr>
            <th>Snap</th><th>Run</th><th>Source</th><th>Started</th>
            <th>Model</th><th></th>
            <th>Conf.</th><th>Entities</th><th>Agree</th><th>+/Δ/stale</th><th>Cost</th>
          </tr>
        </thead>
        <tbody>
          {grouped.map((g) => g.rows.map((r, i) => (
            <tr key={r.id} className={i === 0 ? "snap-first" : ""}>
              {i === 0 ? <td rowSpan={g.rows.length} className="muted">{r.snapshot_id ?? "—"}</td> : null}
              <td>{r.id}</td>
              <td>{r.source_id}</td>
              <td>{fmt(r.started_at)}</td>
              <td title={r.backend}>{shortModel(r.backend)}</td>
              <td>
                {r.error ? <Badge kind="err">error</Badge>
                  : r.is_primary ? <Badge kind="primary">primary</Badge>
                  : <Badge>challenger</Badge>}
              </td>
              <td>{r.confidence != null ? Number(r.confidence).toFixed(2) : "—"}</td>
              <td>{r.entity_count ?? 0}</td>
              <td>{r.agreement != null ? Number(r.agreement).toFixed(2) : <span className="muted">—</span>}</td>
              <td className="muted">
                {r.is_primary ? `${r.new_count ?? 0}/${r.updated_count ?? 0}/${r.stale_count ?? 0}` : <span className="muted">—</span>}
              </td>
              <td>${Number(r.cost_usd ?? 0).toFixed(4)}</td>
            </tr>
          )))}
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
  const fields = Array.from(new Set(entities.flatMap((e) => Object.keys(e.data || {}))));
  return (
    <div className="panel">
      <h2>Entities — source {sourceId} (from primary model)</h2>
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
              <td>{e.stale ? <Badge kind="err">stale</Badge> : <Badge>live</Badge>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function NewSourceForm({ onCreated }) {
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [identityKey, setIdentityKey] = useState("");
  const [primary, setPrimary] = useState(ALL_MODELS[0]);
  const [challengers, setChallengers] = useState(new Set(ALL_MODELS.slice(1)));
  const [busy, setBusy] = useState(false);

  const toggleChallenger = (m) => {
    const next = new Set(challengers);
    if (next.has(m)) next.delete(m); else next.add(m);
    setChallengers(next);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!url || !primary) return;
    setBusy(true);
    try {
      await createSource({
        url,
        label: label || null,
        identity_key: identityKey ? identityKey.split(",").map((s) => s.trim()).filter(Boolean) : [],
        primary_model: primary,
        comparison_models: Array.from(challengers).filter((m) => m !== primary),
      });
      setUrl(""); setLabel(""); setIdentityKey("");
      onCreated();
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="panel" onSubmit={submit}>
      <h2>Add source — multi-model bake-off</h2>
      <div className="row">
        <input className="grow" placeholder="https://example.com/listings" value={url} onChange={(e) => setUrl(e.target.value)} />
        <input placeholder="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} />
        <input placeholder="Identity key (comma-separated)" value={identityKey} onChange={(e) => setIdentityKey(e.target.value)} />
      </div>
      <div className="row" style={{ marginTop: "0.5rem", flexWrap: "wrap", gap: "0.75rem" }}>
        <label>
          <span className="muted" style={{ marginRight: "0.5rem" }}>Primary:</span>
          <select value={primary} onChange={(e) => setPrimary(e.target.value)}>
            {ALL_MODELS.map((m) => <option key={m} value={m}>{shortModel(m)}</option>)}
          </select>
        </label>
        <div className="row" style={{ gap: "0.5rem" }}>
          <span className="muted">Challengers:</span>
          {ALL_MODELS.filter((m) => m !== primary).map((m) => (
            <label key={m} style={{ fontSize: "0.85rem" }}>
              <input type="checkbox" checked={challengers.has(m)} onChange={() => toggleChallenger(m)} />
              {" "}{shortModel(m)}
            </label>
          ))}
        </div>
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
      setSources(s); setRuns(r); setError(null);
    } catch (e) { setError(e.message); }
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
    try { await triggerRun(id); await refresh(); }
    catch (e) { setError(e.message); }
    finally { setBusyId(null); }
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
      {error && <div className="panel" style={{ borderColor: "var(--err)" }}><Badge kind="err">error</Badge> {error}</div>}
      <NewSourceForm onCreated={refresh} />
      <Sources sources={sources} onRun={handleRun} onSelect={setSelected} busyId={busyId} />
      <Runs runs={runs} />
      <Entities sourceId={selected} entities={entities} />
    </div>
  );
}
