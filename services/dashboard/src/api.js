const BASE = "/api";

export async function listSources() {
  const r = await fetch(`${BASE}/sources`);
  if (!r.ok) throw new Error(`sources: ${r.status}`);
  return r.json();
}

export async function listRuns() {
  const r = await fetch(`${BASE}/runs`);
  if (!r.ok) throw new Error(`runs: ${r.status}`);
  return r.json();
}

export async function listEntities(sourceId) {
  const r = await fetch(`${BASE}/sources/${sourceId}/entities`);
  if (!r.ok) throw new Error(`entities: ${r.status}`);
  return r.json();
}

export async function triggerRun(sourceId) {
  const r = await fetch(`${BASE}/sources/${sourceId}/run`, { method: "POST" });
  if (!r.ok) throw new Error(`run: ${r.status}`);
  return r.json();
}

export async function createSource(payload) {
  const r = await fetch(`${BASE}/sources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(`create: ${r.status}`);
  return r.json();
}
