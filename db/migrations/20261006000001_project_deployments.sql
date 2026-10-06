-- migrate:up
ALTER TABLE projects
    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'CREATED'
    CHECK (status IN ('CREATED', 'DEPLOYED'));

CREATE TABLE project_backup (
    id UUID PRIMARY KEY DEFAULT uuidv7() CHECK (uuid_extract_version(id) = 7),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    archive_path VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX project_backup_project_created_idx
    ON project_backup (project_id, created_at DESC, id DESC);

-- migrate:down
DROP TABLE project_backup;
ALTER TABLE projects DROP COLUMN status;
