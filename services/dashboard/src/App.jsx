import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import {
  createSource, deleteSource, entityHistory, getAnchors, getSnapshot,
  listEntities, listRuns, listSources, patchSource, reAnchor, triggerRun,
} from "./api";

// -------------------- helpers --------------------

const fmt = (d) => (d ? new Date(d).toLocaleString() : "—");
const shortModel = (m) => (m && m.includes("/") ? m.split("/").slice(-1)[0] : (m || "—"));
const isNumericLike = (v) =>
  typeof v === "number" || (typeof v === "string" && v && !Number.isNaN(parseFloat(v)));
const numify = (v) => {
  if (typeof v === "number") return v;
  if (typeof v !== "string") return NaN;
  return parseFloat(v.replace(/[^0-9.\-]/g, ""));
};

function useTick(ms = 5000) {
  const [, setT] = useState(0);
  useEffect(() => { const id = setInterval(() => setT((x) => x + 1), ms); return () => clearInterval(id); }, [ms]);
}

function relTime(iso) {
  if (!iso) return "never";
  const d = new Date(iso); const s = Math.round((Date.now() - d.getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

// -------------------- primitives --------------------

function Badge({ kind, children, title }) {
  const cls = kind === "err" ? "badge err" : kind === "primary" ? "badge cloud" : kind === "muted" ? "badge muted-badge" : "badge local";
  return <span className={cls} title={title}>{children}</span>;
}

function Pill({ on, children, onClick, title }) {
  return (
    <button type="button" className={`pill ${on ? "on" : ""}`} onClick={onClick} title={title}>
      {children}
    </button>
  );
}

// -------------------- sparkline --------------------

function Sparkline({ values, width = 140, height = 32 }) {
  if (!values || values.length < 2) return <span className="muted small">—</span>;
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;
  const step = (width - 4) / (values.length - 1);
  const pts = values.map((v, i) => `${(2 + i * step).toFixed(1)},${(2 + (height - 4) * (1 - (v - min) / range)).toFixed(1)}`);
  const last = values[values.length - 1], first = values[0];
  const trend = last > first ? "up" : last < first ? "down" : "flat";
  return (
    <svg width={width} height={height} className={`spark spark-${trend}`}>
      <polyline fill="none" stroke="currentColor" strokeWidth="1.5" points={pts.join(" ")} />
      <circle cx={pts[pts.length - 1].split(",")[0]} cy={pts[pts.length - 1].split(",")[1]} r="2" fill="currentColor" />
    </svg>
  );
}

// -------------------- cron editor --------------------

const CRON_PRESETS = [
  { label: "1m",  cron: "* * * * *"    },
  { label: "2m",  cron: "*/2 * * * *"  },
  { label: "5m",  cron: "*/5 * * * *"  },
  { label: "15m", cron: "*/15 * * * *" },
  { label: "1h",  cron: "0 * * * *"    },
  { label: "6h",  cron: "0 */6 * * *"  },
];

function CronEditor({ cron, onChange, compact }) {
  const [value, setValue] = useState(cron || "");
  const [saving, setSaving] = useState(false);
  useEffect(() => setValue(cron || ""), [cron]);

  const matchPreset = CRON_PRESETS.find((p) => p.cron === cron);
  const live = !!cron;
  const display = live ? (matchPreset ? `every ${matchPreset.label}` : cron) : "manual";

  const commit = async (next) => {
    if (next === (cron || "")) return;
    setSaving(true);
    try {
      await onChange(next || null);
      setValue(next);
    } finally { setSaving(false); }
  };

  return (
    <div className={compact ? "cron-inline" : "cron-block"}>
      <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <span className={`cron-dot ${live ? "live" : ""}`} />
        <strong className={`small ${live ? "" : "muted"}`}>{display}</strong>
        {saving && <span className="muted small">saving…</span>}
      </div>
      <div className="row" style={{ gap: 4, flexWrap: "wrap", marginTop: 6 }}>
        {CRON_PRESETS.map((p) => (
          <Pill key={p.label} on={value === p.cron} onClick={() => commit(p.cron)} title={p.cron}>
            {p.label}
          </Pill>
        ))}
        <Pill on={!value} onClick={() => commit("")} title="manual only">off</Pill>
      </div>
      <div className="row" style={{ marginTop: 6 }}>
        <input
          className="grow mono small"
          placeholder="* * * * * (5-field cron)"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(value); } }}
        />
        <button type="button" className="small-btn" onClick={() => commit(value)} disabled={saving || value === (cron || "")}>save</button>
      </div>
    </div>
  );
}

// -------------------- schema builder --------------------

const FIELD_TYPES = ["string", "number", "boolean", "string[]"];

function fieldsToSchema(fields) {
  const out = {};
  for (const f of fields) {
    const name = (f.name || "").trim();
    if (!name) continue;
    out[name] = f.type === "string[]" ? { type: "array", items: { type: "string" } } : { type: f.type };
  }
  return Object.keys(out).length ? { fields: out } : { fields: { value: { type: "string" } } };
}

function SchemaBuilder({ fields, setFields }) {
  const update = (i, patch) => { const n = fields.slice(); n[i] = { ...n[i], ...patch }; setFields(n); };
  const remove = (i) => setFields(fields.filter((_, idx) => idx !== i));
  const add = () => setFields([...fields, { name: "", type: "string" }]);
  return (
    <div className="schema-builder">
      <div className="muted small" style={{ marginBottom: 8 }}>
        Schema fields — first field is the implicit identity for dedup. Add fields you want extracted.
      </div>
      {fields.map((f, i) => (
        <div className="row schema-row" key={i}>
          <input className="grow" placeholder={i === 0 ? "field name (e.g. title)" : "field name"} value={f.name} onChange={(e) => update(i, { name: e.target.value })} />
          <select value={f.type} onChange={(e) => update(i, { type: e.target.value })}>
            {FIELD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          {i === 0 && <Badge kind="muted" title="implicit identity">id</Badge>}
          <button type="button" className="ghost" onClick={() => remove(i)} disabled={fields.length === 1} title="remove field">×</button>
        </div>
      ))}
      <button type="button" className="secondary" onClick={add} style={{ marginTop: 8 }}>+ field</button>
    </div>
  );
}

// -------------------- new source form --------------------

const ALL_MODELS = [
  "anthropic/claude-sonnet-4",
  "openai/gpt-4o",
  "meta-llama/llama-3.3-70b-instruct",
  "google/gemini-2.0-flash-001",
];

const PRESETS = {
  HN: {
    url: "https://news.ycombinator.com/",
    label: "HN front page",
    anchor: "the list of front-page submissions with their points and authors",
    fields: [
      { name: "title", type: "string" }, { name: "points", type: "number" },
      { name: "user", type: "string" },  { name: "comments", type: "number" },
    ],
    cron: "*/2 * * * *",
  },
  DeFi: {
    url: "https://www.blockchain.com/explorer/defi",
    label: "DeFi protocols",
    anchor: "the list of DeFi protocols with their TVL, price, and 24h change",
    fields: [
      { name: "name", type: "string" },
      { name: "token", type: "string" },
      { name: "price_usd", type: "number" },
      { name: "tvl_usd", type: "number" },
      { name: "change_24h_pct", type: "number" },
    ],
    cron: "*/2 * * * *",
  },
  Lobsters: {
    url: "https://lobste.rs/",
    label: "Lobste.rs",
    anchor: "the list of recent stories with their submitter and tags",
    fields: [
      { name: "title", type: "string" }, { name: "submitter", type: "string" },
      { name: "tags", type: "string[]" }, { name: "comments", type: "number" },
    ],
    cron: "*/5 * * * *",
  },
};

function NewSourceForm({ onCreated }) {
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [anchor, setAnchor] = useState("");
  const [fields, setFields] = useState([{ name: "title", type: "string" }]);
  const [primary, setPrimary] = useState(ALL_MODELS[0]);
  const [challengers, setChallengers] = useState(new Set(ALL_MODELS.slice(1)));
  const [cron, setCron] = useState("");
  const [showJson, setShowJson] = useState(false);
  const [busy, setBusy] = useState(false);

  const toggleChallenger = (m) => {
    const n = new Set(challengers); n.has(m) ? n.delete(m) : n.add(m); setChallengers(n);
  };

  const loadPreset = (key) => {
    const p = PRESETS[key];
    setUrl(p.url); setLabel(p.label); setAnchor(p.anchor); setFields(p.fields); setCron(p.cron);
  };

  const payload = useMemo(() => ({
    url, label: label || null, anchor: anchor || null,
    schema: fieldsToSchema(fields),
    primary_model: primary,
    comparison_models: Array.from(challengers).filter((m) => m !== primary),
    refresh_cron: cron || null,
  }), [url, label, anchor, fields, primary, challengers, cron]);

  const submit = async (alsoRun) => {
    if (!url || !primary) return;
    setBusy(true);
    try {
      const { id } = await createSource(payload);
      if (alsoRun) await triggerRun(id);
      setUrl(""); setLabel(""); setAnchor(""); setCron("");
      setFields([{ name: "title", type: "string" }]);
      onCreated();
    } finally { setBusy(false); }
  };

  return (
    <form className="panel" onSubmit={(e) => { e.preventDefault(); submit(false); }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Add source</h2>
        <div className="row" style={{ gap: 4 }}>
          <span className="muted small">presets:</span>
          {Object.keys(PRESETS).map((k) => (
            <button type="button" key={k} className="ghost" onClick={() => loadPreset(k)}>{k}</button>
          ))}
        </div>
      </div>

      <div className="row" style={{ marginTop: 14 }}>
        <input className="grow" placeholder="URL — any page with structured-ish data" value={url} onChange={(e) => setUrl(e.target.value)} />
        <input placeholder="Label (optional)" value={label} onChange={(e) => setLabel(e.target.value)} />
      </div>

      <div className="row" style={{ marginTop: 8 }}>
        <input className="grow" placeholder='Anchor — semantic description, e.g. "the recent stories list"' value={anchor} onChange={(e) => setAnchor(e.target.value)} />
      </div>

      <div style={{ marginTop: 14 }}>
        <SchemaBuilder fields={fields} setFields={setFields} />
      </div>

      <div className="row" style={{ marginTop: 14, flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
        <div>
          <div className="muted small" style={{ marginBottom: 4 }}>Primary model</div>
          <select value={primary} onChange={(e) => setPrimary(e.target.value)}>
            {ALL_MODELS.map((m) => <option key={m} value={m}>{shortModel(m)}</option>)}
          </select>
        </div>
        <div>
          <div className="muted small" style={{ marginBottom: 4 }}>Challengers</div>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {ALL_MODELS.filter((m) => m !== primary).map((m) => (
              <Pill key={m} on={challengers.has(m)} onClick={() => toggleChallenger(m)}>
                {shortModel(m)}
              </Pill>
            ))}
          </div>
        </div>
        <div className="grow">
          <div className="muted small" style={{ marginBottom: 4 }}>Refresh schedule</div>
          <CronEditor cron={cron} onChange={(c) => setCron(c || "")} />
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        <button type="button" className="ghost" onClick={() => setShowJson((v) => !v)}>
          {showJson ? "▾" : "▸"} JSON preview
        </button>
        {showJson && <pre className="json-preview">{JSON.stringify(payload, null, 2)}</pre>}
      </div>

      <div className="row" style={{ marginTop: 14 }}>
        <button disabled={busy || !url}>{busy ? "Adding…" : "Add"}</button>
        <button type="button" onClick={() => submit(true)} disabled={busy || !url}>
          {busy ? "Running…" : "Add + run"}
        </button>
      </div>
    </form>
  );
}

// -------------------- sources panel --------------------

function Sources({ sources, onRun, onSelect, busyId, selectedId, onCronChange, onDelete, onReAnchor, onInspectSnapshot }) {
  useTick(5000);
  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Sources</h2>
        <span className="muted small">{sources.length} configured</span>
      </div>
      <div className="sources-grid" style={{ marginTop: 10 }}>
        {sources.map((s) => (
          <div key={s.id} className={`source-card ${selectedId === s.id ? "sel" : ""}`}>
            <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <div className="row" style={{ gap: 8 }}>
                  <span className="src-id">#{s.id}</span>
                  <strong>{s.label || <span className="muted">unlabeled</span>}</strong>
                  {s.has_anchors
                    ? <Badge kind="local" title={`anchored ${relTime(s.last_anchored_at)}`}>anchored ✓</Badge>
                    : <Badge kind="muted" title="no cached anchors — first run will use the LLM">unanchored</Badge>}
                </div>
                <a className="src-url" href={s.url} target="_blank" rel="noreferrer">{s.url}</a>
              </div>
              <div className="row" style={{ gap: 6 }}>
                <button onClick={() => onRun(s.id)} disabled={busyId === s.id}>
                  {busyId === s.id ? "Running…" : "Run"}
                </button>
                <button className="secondary" onClick={() => onSelect(s.id)}>
                  {selectedId === s.id ? "Hide" : "Inspect"}
                </button>
                <button className="ghost" onClick={() => onReAnchor(s.id)} title="invalidate cached anchors — next run uses LLM">re-anchor</button>
                <button className="ghost" onClick={() => onInspectSnapshot(s.id)} title="see what the LLM/BS4 actually received">snapshot</button>
                <button className="ghost danger" onClick={() => onDelete(s.id)} title="delete">×</button>
              </div>
            </div>

            <div className="src-meta">
              <div>
                <span className="muted small">primary</span>
                <Badge kind="primary" title={s.primary_model}>{shortModel(s.primary_model)}</Badge>
              </div>
              {(s.comparison_models || []).length > 0 && (
                <div>
                  <span className="muted small">vs</span>
                  <span className="small">{(s.comparison_models || []).map(shortModel).join(", ")}</span>
                </div>
              )}
              <div>
                <span className="muted small">fields</span>
                <span className="small mono">{(s.schema_field_names || []).join(", ") || "—"}</span>
              </div>
              <div>
                <span className="muted small">poll</span>
                <CronEditor cron={s.refresh_cron} compact onChange={(c) => onCronChange(s.id, c)} />
              </div>
              <div>
                <span className="muted small">last run</span>
                <span className="small">{relTime(s.last_run_at)}</span>
              </div>
              <div>
                <span className="muted small">anchored</span>
                <span className="small">{s.has_anchors ? relTime(s.last_anchored_at) : <span className="muted">never</span>}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function SnapshotModal({ snapshot, onClose }) {
  if (!snapshot) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Snapshot #{snapshot.snapshot_id}</h2>
          <button className="ghost" onClick={onClose}>close</button>
        </div>
        <div className="row" style={{ gap: 18, marginTop: 8 }}>
          <span className="muted small">fetched: {new Date(snapshot.fetched_at).toLocaleString()}</span>
          <span className="muted small">status: {snapshot.status_code}</span>
          <span className="muted small">size: {(snapshot.bytes / 1024).toFixed(1)} KB</span>
        </div>
        <pre className="snapshot-html">{snapshot.html}</pre>
      </div>
    </div>
  );
}

// -------------------- entity history (inline) --------------------

function ChangeLine({ change }) {
  const oldS = change.old_value == null ? "∅" : JSON.stringify(change.old_value);
  const newS = change.new_value == null ? "∅" : JSON.stringify(change.new_value);
  const oldN = numify(change.old_value);
  const newN = numify(change.new_value);
  const isNum = !Number.isNaN(oldN) && !Number.isNaN(newN);
  const dir = isNum ? (newN > oldN ? "up" : newN < oldN ? "down" : "flat") : "flat";
  return (
    <div className="change-line">
      <span className="muted small">{relTime(change.changed_at)}</span>
      <span className="field">{change.field}</span>
      <span className="old">{oldS}</span>
      <span className={`arrow arrow-${dir}`}>→</span>
      <span className="new">{newS}</span>
      {isNum && newN !== oldN && (
        <span className={`delta delta-${dir}`}>
          {dir === "up" ? "▲" : "▼"} {Math.abs(((newN - oldN) / Math.max(Math.abs(oldN), 1e-9)) * 100).toFixed(1)}%
        </span>
      )}
    </div>
  );
}

function FieldHistory({ history, field }) {
  const series = useMemo(() => {
    const events = history.changes.filter((c) => c.field === field);
    const init = numify(history.current?.[field]);
    const reversed = events.slice().reverse();  // oldest -> newest
    const values = [];
    let running = init;
    if (reversed.length === 0 || Number.isNaN(numify(reversed[0].old_value))) {
      // no numeric history; bail
    } else {
      values.push(numify(reversed[0].old_value));
      for (const c of reversed) values.push(numify(c.new_value));
    }
    return values.filter((v) => !Number.isNaN(v));
  }, [history, field]);

  if (series.length < 2) return null;
  return (
    <div className="row field-history-row">
      <span className="muted small mono">{field}</span>
      <Sparkline values={series} />
      <span className="muted small">{series.length} samples</span>
    </div>
  );
}

function ExpandedEntity({ sourceId, entityId, currentData, onClose }) {
  const [history, setHistory] = useState(null);
  const [err, setErr] = useState(null);
  const refresh = useCallback(() => {
    entityHistory(sourceId, entityId).then(setHistory).catch((e) => setErr(e.message));
  }, [sourceId, entityId]);
  useEffect(() => { refresh(); }, [refresh]);

  // Numeric fields suitable for sparklines
  const numericFields = useMemo(() => {
    if (!currentData) return [];
    return Object.entries(currentData).filter(([, v]) => isNumericLike(v)).map(([k]) => k);
  }, [currentData]);

  return (
    <div className="entity-expanded">
      {err && <div className="badge err">{err}</div>}
      {!history && !err && <div className="muted small">loading history…</div>}
      {history && (
        <>
          {numericFields.length > 0 && (
            <div className="hist-sparks">
              {numericFields.map((f) => <FieldHistory key={f} history={history} field={f} />)}
            </div>
          )}
          <div className="hist-changes">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="muted small">{history.changes.length} field change{history.changes.length === 1 ? "" : "s"}</span>
              <button className="ghost small-btn" onClick={refresh}>refresh</button>
            </div>
            {history.changes.length === 0 ? (
              <div className="muted small" style={{ padding: 8 }}>
                No drift recorded yet. Re-run or wait for the next scheduled poll.
              </div>
            ) : (
              <div className="change-list">
                {history.changes.slice().reverse().map((c) => <ChangeLine key={c.id} change={c} />)}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// -------------------- entities table --------------------

function Entities({ sourceId, entities, onRefresh }) {
  const [openId, setOpenId] = useState(null);
  if (!sourceId) return null;
  const fields = Array.from(new Set(entities.flatMap((e) => Object.keys(e.data || {}))));

  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Entities — source {sourceId}</h2>
        <span className="muted small">click a row for change history</span>
      </div>
      {entities.length === 0 ? (
        <p className="muted" style={{ marginTop: 12 }}>No entities yet. Hit Run, or wait for the cron tick.</p>
      ) : (
        <table className="entities-table" style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>ID</th>
              {fields.map((f) => <th key={f}>{f}</th>)}
              <th>Updates</th>
              <th>Last seen</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {entities.map((e) => {
              const open = openId === e.id;
              return (
                <>
                  <tr key={e.id} className={`entity-row ${open ? "open" : ""} ${e.stale ? "stale" : ""}`} onClick={() => setOpenId(open ? null : e.id)}>
                    <td><span className="muted">#{e.id}</span></td>
                    {fields.map((f) => {
                      const v = e.data?.[f];
                      const display = Array.isArray(v) ? v.join(", ") : (v == null ? "" : String(v));
                      return <td key={f} className={isNumericLike(v) ? "num" : ""}>{display}</td>;
                    })}
                    <td>
                      {e.update_count > 0 ? <Badge kind="primary">{e.update_count}</Badge> : <span className="muted small">—</span>}
                    </td>
                    <td className="muted small">{relTime(e.last_seen)}</td>
                    <td>{e.stale ? <Badge kind="err">stale</Badge> : <span className="caret">{open ? "▾" : "▸"}</span>}</td>
                  </tr>
                  {open && (
                    <tr key={`${e.id}-exp`} className="entity-expand-row">
                      <td colSpan={fields.length + 4}>
                        <ExpandedEntity sourceId={sourceId} entityId={e.id} currentData={e.data} />
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// -------------------- runs --------------------

function Runs({ runs }) {
  const grouped = useMemo(() => {
    const out = []; let last = null;
    for (const r of runs) {
      const key = r.snapshot_id ?? `solo-${r.id}`;
      if (!last || last.key !== key) { last = { key, rows: [] }; out.push(last); }
      last.rows.push(r);
    }
    return out;
  }, [runs]);
  useTick(5000);

  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Recent runs</h2>
        <span className="muted small">grouped by snapshot</span>
      </div>
      <table className="runs-table" style={{ marginTop: 10 }}>
        <thead>
          <tr>
            <th>Snap</th><th>Run</th><th>Src</th><th>When</th>
            <th>Model</th><th>Role</th>
            <th>Conf</th><th>Ents</th><th>Agree</th><th>+/Δ/×</th><th>Cost</th>
          </tr>
        </thead>
        <tbody>
          {grouped.map((g) => g.rows.map((r, i) => (
            <tr key={r.id} className={i === 0 ? "snap-first" : ""}>
              {i === 0 ? <td rowSpan={g.rows.length} className="muted small">{r.snapshot_id ?? "—"}</td> : null}
              <td>#{r.id}</td>
              <td>{r.source_id}</td>
              <td className="muted small">{relTime(r.started_at)}</td>
              <td className="mono small" title={r.backend}>{shortModel(r.backend)}</td>
              <td>
                {r.error ? <Badge kind="err">error</Badge>
                  : r.is_primary ? <Badge kind="primary">primary</Badge>
                  : <Badge>chal.</Badge>}
              </td>
              <td>{r.confidence != null ? Number(r.confidence).toFixed(2) : "—"}</td>
              <td>{r.entity_count ?? 0}</td>
              <td>{r.agreement != null ? <span className={agreeColor(r.agreement)}>{Number(r.agreement).toFixed(2)}</span> : <span className="muted">—</span>}</td>
              <td className="muted small">
                {r.is_primary ? `${r.new_count ?? 0}/${r.updated_count ?? 0}/${r.stale_count ?? 0}` : "—"}
              </td>
              <td className="num small">${Number(r.cost_usd ?? 0).toFixed(4)}</td>
            </tr>
          )))}
        </tbody>
      </table>
    </div>
  );
}

const agreeColor = (a) => a >= 0.97 ? "agree-hi" : a >= 0.85 ? "agree-mid" : "agree-lo";

// -------------------- app --------------------

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
    if (!selected) { setEntities([]); return; }
    const load = () => listEntities(selected).then(setEntities).catch((e) => setError(e.message));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [selected, runs]);

  const handleRun = async (id) => {
    setBusyId(id);
    try { await triggerRun(id); await refresh(); }
    catch (e) { setError(e.message); }
    finally { setBusyId(null); }
  };

  const handleCronChange = async (id, cron) => {
    try { await patchSource(id, { refresh_cron: cron }); await refresh(); }
    catch (e) { setError(e.message); }
  };

  const handleDelete = async (id) => {
    if (!confirm("Delete this source and all its data?")) return;
    try { await deleteSource(id); if (selected === id) setSelected(null); await refresh(); }
    catch (e) { setError(e.message); }
  };

  const [snapshot, setSnapshot] = useState(null);

  const handleReAnchor = async (id) => {
    if (!confirm("Invalidate cached anchors? Next run will use the LLM (incurs cost).")) return;
    try { await reAnchor(id); await refresh(); }
    catch (e) { setError(e.message); }
  };

  const handleInspectSnapshot = async (id) => {
    try { setSnapshot(await getSnapshot(id)); }
    catch (e) { setError(e.message); }
  };

  return (
    <div className="app">
      <header>
        <div>
          <h1>WebHarvest</h1>
          <span className="muted small">multi-model bake-off • per-source polling • field-level drift</span>
        </div>
        <nav>
          <a href="/grafana/" target="_blank" rel="noreferrer">Grafana</a>
          <a href="/prometheus/" target="_blank" rel="noreferrer">Prometheus</a>
        </nav>
      </header>
      {error && <div className="panel err-panel"><Badge kind="err">error</Badge> <span className="small">{error}</span></div>}
      <NewSourceForm onCreated={refresh} />
      <Sources
        sources={sources} onRun={handleRun} onSelect={(id) => setSelected(selected === id ? null : id)}
        busyId={busyId} selectedId={selected}
        onCronChange={handleCronChange} onDelete={handleDelete}
        onReAnchor={handleReAnchor} onInspectSnapshot={handleInspectSnapshot}
      />
      <SnapshotModal snapshot={snapshot} onClose={() => setSnapshot(null)} />
      {selected && <Entities sourceId={selected} entities={entities} />}
      <Runs runs={runs} />
    </div>
  );
}
