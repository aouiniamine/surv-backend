-- migrate:up
ALTER TABLE agent_run ADD COLUMN skill_id VARCHAR(80);
ALTER TABLE agent_run ADD COLUMN skill_version VARCHAR(40);

-- migrate:down
ALTER TABLE agent_run DROP COLUMN skill_version;
ALTER TABLE agent_run DROP COLUMN skill_id;
