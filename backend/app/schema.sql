CREATE TABLE IF NOT EXISTS documents (
    id                BIGSERIAL PRIMARY KEY,
    filename          TEXT NOT NULL UNIQUE,
    active_version_id BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per uploaded revision of a document. The file bytes live here so that
-- "file saved" and "job queued" commit in the same transaction.
CREATE TABLE IF NOT EXISTS document_versions (
    id          BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_no  INT NOT NULL,
    sha256      TEXT NOT NULL,
    content     BYTEA,
    size_bytes  INT NOT NULL,
    -- queued | extracting | chunking | indexing | ready | failed | superseded
    status      TEXT NOT NULL DEFAULT 'queued',
    error       TEXT,
    title       TEXT,
    doc_version TEXT,
    doc_owner   TEXT,
    chunk_count INT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    indexed_at  TIMESTAMPTZ,
    UNIQUE (document_id, version_no)
);
CREATE INDEX IF NOT EXISTS document_versions_sha ON document_versions (sha256);

CREATE TABLE IF NOT EXISTS chunks (
    id         BIGSERIAL PRIMARY KEY,
    version_id BIGINT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    ordinal    INT NOT NULL,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    heading    TEXT NOT NULL DEFAULT '',
    page       INT,
    clause     TEXT,
    line       INT
);
CREATE INDEX IF NOT EXISTS chunks_version ON chunks (version_id);

-- The work queue. A job is claimed with FOR UPDATE SKIP LOCKED and held by a lease.
CREATE TABLE IF NOT EXISTS jobs (
    id           BIGSERIAL PRIMARY KEY,
    version_id   BIGINT NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    -- queued | running | done | failed
    state        TEXT NOT NULL DEFAULT 'queued',
    attempts     INT NOT NULL DEFAULT 0,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    leased_until TIMESTAMPTZ,
    worker       TEXT,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_claim ON jobs (state, run_after);

-- Bumped whenever the searchable set changes, so the API knows to reload its index.
CREATE TABLE IF NOT EXISTS index_state (
    id         INT PRIMARY KEY CHECK (id = 1),
    generation BIGINT NOT NULL
);
INSERT INTO index_state (id, generation) VALUES (1, 0) ON CONFLICT DO NOTHING;
