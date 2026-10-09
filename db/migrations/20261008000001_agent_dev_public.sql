-- migrate:up
DROP TABLE agent_draft;
ALTER TABLE agent_run RENAME COLUMN draft_revision TO result_revision;

-- migrate:down
ALTER TABLE agent_run RENAME COLUMN result_revision TO draft_revision;
CREATE TABLE agent_draft (
    project_id UUID PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    revision VARCHAR(64) NOT NULL,
    source_run_id UUID NOT NULL REFERENCES agent_run(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_revision VARCHAR(64)
);
