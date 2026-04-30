-- WebHarvest schema. Loaded on Postgres first run.

CREATE TABLE IF NOT EXISTS sources (
    id                 BIGSERIAL PRIMARY KEY,
    url                TEXT NOT NULL UNIQUE,
    label              TEXT,
    schema             JSONB NOT NULL DEFAULT '{}'::jsonb,
    anchor             TEXT,
    identity_key       TEXT[] NOT NULL DEFAULT '{}',
    pagination         JSONB NOT NULL DEFAULT '{}'::jsonb,
    refresh_cron       TEXT,
    -- Per source we pick a primary model (its entities are persisted) and
    -- a list of challenger models (run on the same snapshot for measurement,
    -- not persisted). Both are OpenRouter slugs e.g. "openai/gpt-4o".
    primary_model      TEXT,
    comparison_models  TEXT[] NOT NULL DEFAULT '{}',
    -- Cached DOM anchoring recipe. The LLM produces this on first run; every
    -- subsequent poll applies it via BeautifulSoup with no LLM cost. NULL
    -- means "no anchors yet, next run goes through the LLM."
    anchors            JSONB,
    last_anchored_at   TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS snapshots (
    id          BIGSERIAL PRIMARY KEY,
    source_id   BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    status_code INT,
    html        TEXT NOT NULL,
    bytes       INT NOT NULL
);

CREATE INDEX IF NOT EXISTS snapshots_source_idx ON snapshots(source_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id            BIGSERIAL PRIMARY KEY,
    source_id     BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    snapshot_id   BIGINT REFERENCES snapshots(id) ON DELETE SET NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    -- For multi-model runs: each model's result is a separate row, all
    -- sharing the same snapshot_id. The primary run is the one whose
    -- entities make it into the entities table. Challenger runs record
    -- cost/latency/confidence/entity_count for comparison only.
    backend       TEXT,            -- the model slug (or "heuristic", legacy)
    is_primary    BOOLEAN NOT NULL DEFAULT TRUE,
    confidence    REAL,
    entity_count  INT NOT NULL DEFAULT 0,
    new_count     INT NOT NULL DEFAULT 0,
    updated_count INT NOT NULL DEFAULT 0,
    stale_count   INT NOT NULL DEFAULT 0,
    cost_usd      NUMERIC(10,6) NOT NULL DEFAULT 0,
    -- Jaccard agreement of this run's identity-keys with the primary run
    -- in the same snapshot. NULL on the primary itself.
    agreement     REAL,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS runs_source_idx ON runs(source_id, started_at DESC);
CREATE INDEX IF NOT EXISTS runs_snapshot_idx ON runs(snapshot_id);

CREATE TABLE IF NOT EXISTS entities (
    id           BIGSERIAL PRIMARY KEY,
    source_id    BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    identity     TEXT NOT NULL,
    data         JSONB NOT NULL,
    confidence   REAL NOT NULL DEFAULT 1.0,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_run_id  BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    stale        BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(source_id, identity)
);

CREATE INDEX IF NOT EXISTS entities_source_idx ON entities(source_id, last_seen DESC);

-- Granular change log: one row per (entity, field) value change. Lets us
-- answer "how has this entity drifted over time?" without re-fetching, and
-- powers the Grafana Postgres-datasource panels for field-level analysis.
CREATE TABLE IF NOT EXISTS entity_changes (
    id          BIGSERIAL PRIMARY KEY,
    entity_id   BIGINT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    source_id   BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    run_id      BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    field       TEXT NOT NULL,
    old_value   JSONB,
    new_value   JSONB,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS entity_changes_entity_idx ON entity_changes(entity_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS entity_changes_source_field_idx ON entity_changes(source_id, field, changed_at DESC);
