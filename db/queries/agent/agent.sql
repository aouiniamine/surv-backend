-- name: GetAgentProject :one
SELECT p.id, p.public_id, p.status, r.role
FROM projects AS p
JOIN user_organization_relation AS r
  ON r.organization_id = p.organization_id
WHERE p.id = sqlc.arg(project_id) AND r.user_id = sqlc.arg(user_id);

-- name: CreateAgentRun :one
INSERT INTO agent_run (project_id, created_by, prompt, model_id, skill_id, skill_version)
VALUES (sqlc.arg(project_id), sqlc.arg(user_id), sqlc.arg(prompt), sqlc.arg(model_id),
        sqlc.narg(skill_id), sqlc.narg(skill_version))
RETURNING id, project_id, created_by, status, provider_id, model_id, error_message,
          draft_revision, skill_id, skill_version, created_at, started_at, completed_at;

-- name: GetAgentRun :one
SELECT ar.id, ar.project_id, ar.created_by, ar.status, ar.provider_id, ar.model_id,
       ar.error_message, ar.draft_revision, ar.skill_id, ar.skill_version,
       ar.created_at, ar.started_at, ar.completed_at
FROM agent_run AS ar
JOIN projects AS p ON p.id = ar.project_id
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE ar.id = sqlc.arg(run_id) AND ar.project_id = sqlc.arg(project_id)
  AND r.user_id = sqlc.arg(user_id);

-- name: ListAgentRuns :many
SELECT ar.id, ar.project_id, ar.created_by, ar.status, ar.provider_id, ar.model_id,
       ar.error_message, ar.draft_revision, ar.skill_id, ar.skill_version,
       ar.created_at, ar.started_at, ar.completed_at
FROM agent_run AS ar
JOIN projects AS p ON p.id = ar.project_id
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE ar.project_id = sqlc.arg(project_id) AND r.user_id = sqlc.arg(user_id)
ORDER BY ar.created_at DESC, ar.id DESC
LIMIT 30;

-- name: ClaimAgentRun :one
UPDATE agent_run SET status = 'running', started_at = now(), heartbeat_at = now()
WHERE id = (
    SELECT id FROM agent_run WHERE status = 'queued'
    ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1
)
RETURNING id, project_id, created_by, prompt, model_id, skill_id, skill_version;

-- name: HeartbeatAgentRun :exec
UPDATE agent_run SET heartbeat_at = now() WHERE id = sqlc.arg(run_id) AND status = 'running';

-- name: FinishAgentRun :exec
UPDATE agent_run
SET status = sqlc.arg(status), error_message = sqlc.narg(error_message),
    draft_revision = sqlc.narg(draft_revision), completed_at = now()
WHERE id = sqlc.arg(run_id) AND status = 'running';

-- name: CancelAgentRun :exec
UPDATE agent_run
SET status = 'cancelled', completed_at = now()
WHERE id = sqlc.arg(run_id) AND status IN ('queued', 'running');

-- name: GetAgentRunStatus :one
SELECT status FROM agent_run WHERE id = sqlc.arg(run_id);

-- name: LockAgentRunStatus :one
SELECT status FROM agent_run WHERE id = sqlc.arg(run_id) FOR UPDATE;

-- name: FailStaleAgentRuns :exec
UPDATE agent_run SET status = 'failed', error_message = 'Worker interrupted', completed_at = now()
WHERE status = 'running' AND heartbeat_at < now() - interval '2 minutes';

-- name: AddAgentEvent :one
INSERT INTO agent_run_event (run_id, kind, content)
VALUES (sqlc.arg(run_id), sqlc.arg(kind), sqlc.arg(content))
RETURNING sequence;

-- name: ListAgentEvents :many
SELECT e.sequence, e.kind, e.content, e.created_at
FROM agent_run_event AS e
JOIN agent_run AS ar ON ar.id = e.run_id
JOIN projects AS p ON p.id = ar.project_id
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE e.run_id = sqlc.arg(run_id) AND ar.project_id = sqlc.arg(project_id)
  AND r.user_id = sqlc.arg(user_id) AND e.sequence > sqlc.arg(after_sequence)
ORDER BY e.sequence LIMIT 100;

-- name: AddAgentChange :exec
INSERT INTO agent_file_change (run_id, path, change_type, diff_text)
VALUES (sqlc.arg(run_id), sqlc.arg(path), sqlc.arg(change_type), sqlc.narg(diff_text));

-- name: ListAgentChanges :many
SELECT c.id, c.path, c.change_type, c.diff_text IS NOT NULL AS has_diff
FROM agent_file_change AS c
JOIN agent_run AS ar ON ar.id = c.run_id
JOIN projects AS p ON p.id = ar.project_id
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE c.run_id = sqlc.arg(run_id) AND ar.project_id = sqlc.arg(project_id)
  AND r.user_id = sqlc.arg(user_id)
ORDER BY c.path LIMIT 1000;

-- name: GetAgentChange :one
SELECT c.id, c.path, c.change_type, c.diff_text
FROM agent_file_change AS c
JOIN agent_run AS ar ON ar.id = c.run_id
JOIN projects AS p ON p.id = ar.project_id
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE c.id = sqlc.arg(change_id) AND c.run_id = sqlc.arg(run_id)
  AND ar.project_id = sqlc.arg(project_id) AND r.user_id = sqlc.arg(user_id);

-- name: UpsertAgentDraft :exec
INSERT INTO agent_draft (project_id, revision, source_run_id)
VALUES (sqlc.arg(project_id), sqlc.arg(revision), sqlc.arg(run_id))
ON CONFLICT (project_id) DO UPDATE
SET revision = EXCLUDED.revision, source_run_id = EXCLUDED.source_run_id,
    published_revision = NULL, created_at = now();

-- name: MarkAgentDraftPublished :exec
UPDATE agent_draft SET published_revision = sqlc.arg(revision)
WHERE project_id = sqlc.arg(project_id) AND revision = sqlc.arg(revision);

-- name: ClearAgentDraftPublication :exec
UPDATE agent_draft SET published_revision = NULL WHERE project_id = sqlc.arg(project_id);

-- name: GetAgentDraft :one
SELECT d.revision, d.source_run_id, d.published_revision, d.created_at
FROM agent_draft AS d
JOIN projects AS p ON p.id = d.project_id
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE d.project_id = sqlc.arg(project_id) AND r.user_id = sqlc.arg(user_id);
