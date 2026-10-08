-- migrate:up
ALTER TABLE agent_draft ADD COLUMN published_revision VARCHAR(64);

-- migrate:down
ALTER TABLE agent_draft DROP COLUMN published_revision;
