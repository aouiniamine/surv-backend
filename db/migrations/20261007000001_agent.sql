-- migrate:up
CREATE TABLE agent_run (
    id UUID PRIMARY KEY DEFAULT uuidv7() CHECK (uuid_extract_version(id) = 7),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    prompt VARCHAR(8000) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    provider_id VARCHAR(50) NOT NULL DEFAULT 'ollama',
    model_id VARCHAR(150) NOT NULL,
    error_message VARCHAR(500),
    draft_revision VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX agent_run_one_active_project_idx ON agent_run (project_id)
    WHERE status IN ('queued', 'running');
CREATE INDEX agent_run_project_created_idx
    ON agent_run (project_id, created_at DESC, id DESC);

CREATE TABLE agent_run_event (
    run_id UUID NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
    sequence BIGINT GENERATED ALWAYS AS IDENTITY,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('text', 'tool', 'status', 'error')),
    content VARCHAR(4000) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, sequence)
);

CREATE TABLE agent_file_change (
    id UUID PRIMARY KEY DEFAULT uuidv7() CHECK (uuid_extract_version(id) = 7),
    run_id UUID NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
    path VARCHAR(512) NOT NULL,
    change_type VARCHAR(10) NOT NULL CHECK (change_type IN ('added', 'modified', 'deleted')),
    diff_text VARCHAR(30000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, path)
);

CREATE TABLE agent_draft (
    project_id UUID PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    revision VARCHAR(64) NOT NULL,
    source_run_id UUID NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- migrate:down
DROP TABLE agent_draft;
DROP TABLE agent_file_change;
DROP TABLE agent_run_event;
DROP TABLE agent_run;
