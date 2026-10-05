-- migrate:up
ALTER TABLE projects ADD COLUMN public_id VARCHAR(20);

CREATE UNIQUE INDEX projects_public_id_key ON projects (public_id);

DO $$
DECLARE
    project_row RECORD;
    candidate VARCHAR(20);
BEGIN
    FOR project_row IN SELECT id FROM projects LOOP
        LOOP
            candidate := substr(md5(gen_random_uuid()::text), 1, 7);
            EXIT WHEN NOT EXISTS (SELECT 1 FROM projects WHERE public_id = candidate);
        END LOOP;
        UPDATE projects SET public_id = candidate WHERE id = project_row.id;
    END LOOP;
END $$;

ALTER TABLE projects
    ALTER COLUMN public_id SET NOT NULL,
    ADD CONSTRAINT projects_public_id_format CHECK (public_id ~ '^[a-z0-9]{7}$');

-- migrate:down
ALTER TABLE projects DROP COLUMN public_id;
