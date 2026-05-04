const BASE = "/api";

async function jsonFetch(url, init) {
  const r = await fetch(url, init);
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json())?.detail || ""; } catch { }
    throw new Error(`${url}: ${r.status}${detail ? " — " + detail : ""}`);
  }
  return r.json();
}

export const listSources    = ()         => jsonFetch(`${BASE}/sources`);
export const listEntities   = (sid)      => jsonFetch(`${BASE}/sources/${sid}/entities`);
export const triggerRun     = (sid)      => jsonFetch(`${BASE}/sources/${sid}/run`, { method: "POST" });
export const entityHistory  = (sid, eid) => jsonFetch(`${BASE}/sources/${sid}/entities/${eid}/history`);
export const deleteSource   = (sid)      => jsonFetch(`${BASE}/sources/${sid}`, { method: "DELETE" });
export const reAnchor       = (sid)      => jsonFetch(`${BASE}/sources/${sid}/re-anchor`, { method: "POST" });
export const getAnchors     = (sid)      => jsonFetch(`${BASE}/sources/${sid}/anchors`);
export const getSnapshot    = (sid)      => jsonFetch(`${BASE}/sources/${sid}/snapshot`);

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
