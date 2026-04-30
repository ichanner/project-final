-- WebHarvest schema. Loaded on Postgres first run.

CREATE TABLE IF NOT EXISTS sources (
    id           BIGSERIAL PRIMARY KEY,
    url          TEXT NOT NULL UNIQUE,
    label        TEXT,
    schema       JSONB NOT NULL DEFAULT '{}'::jsonb,
    anchor       TEXT,
    identity_key TEXT[] NOT NULL DEFAULT '{}',
    pagination   JSONB NOT NULL DEFAULT '{}'::jsonb,
    refresh_cron TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
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
    id           BIGSERIAL PRIMARY KEY,
    source_id    BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    snapshot_id  BIGINT REFERENCES snapshots(id) ON DELETE SET NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    backend      TEXT,
    confidence   REAL,
    entity_count INT NOT NULL DEFAULT 0,
    new_count    INT NOT NULL DEFAULT 0,
    updated_count INT NOT NULL DEFAULT 0,
    stale_count  INT NOT NULL DEFAULT 0,
    cost_usd     NUMERIC(10,6) NOT NULL DEFAULT 0,
    error        TEXT
);

CREATE INDEX IF NOT EXISTS runs_source_idx ON runs(source_id, started_at DESC);

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

