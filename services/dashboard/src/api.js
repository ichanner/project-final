const BASE = "/api";

async function jsonFetch(url, init) {
  const r = await fetch(url, init);
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json())?.detail || ""; } catch { /* ignore */ }
    throw new Error(`${url}: ${r.status}${detail ? " — " + detail : ""}`);
  }
  return r.json();
}

export const listSources    = ()         => jsonFetch(`${BASE}/sources`);
export const listRuns       = ()         => jsonFetch(`${BASE}/runs`);
export const listEntities   = (sid)      => jsonFetch(`${BASE}/sources/${sid}/entities`);
export const triggerRun     = (sid)      => jsonFetch(`${BASE}/sources/${sid}/run`, { method: "POST" });
export const sourceChanges  = (sid)      => jsonFetch(`${BASE}/sources/${sid}/changes`);
export const entityHistory  = (sid, eid) => jsonFetch(`${BASE}/sources/${sid}/entities/${eid}/history`);
export const deleteSource   = (sid)      => jsonFetch(`${BASE}/sources/${sid}`, { method: "DELETE" });
export const reAnchor       = (sid)      => jsonFetch(`${BASE}/sources/${sid}/re-anchor`, { method: "POST" });
export const getAnchors     = (sid)      => jsonFetch(`${BASE}/sources/${sid}/anchors`);
export const getSnapshot    = (sid)      => jsonFetch(`${BASE}/sources/${sid}/snapshot`);
export const listAlertRules = (sid)      => jsonFetch(`${BASE}/sources/${sid}/alert-rules`);
export const listAlerts     = (sid)      => jsonFetch(`${BASE}/sources/${sid}/alerts?limit=20`);
export const createAlertRule = (sid, payload) =>
  jsonFetch(`${BASE}/sources/${sid}/alert-rules`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
export const patchAlertRule = (rid, patch) =>
  jsonFetch(`${BASE}/alert-rules/${rid}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
export const deleteAlertRule = (rid) =>
  jsonFetch(`${BASE}/alert-rules/${rid}`, { method: "DELETE" });

export const patchSource = (sid, patch) =>
  jsonFetch(`${BASE}/sources/${sid}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });

export const createSource = (payload) =>
  jsonFetch(`${BASE}/sources`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
