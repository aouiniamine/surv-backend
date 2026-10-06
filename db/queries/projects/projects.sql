-- name: CreateProject :one
INSERT INTO projects (organization_id, name, public_id)
SELECT sqlc.arg(organization_id), sqlc.arg(name), sqlc.arg(public_id)
WHERE EXISTS (
    SELECT 1
    FROM user_organization_relation
    WHERE user_id = sqlc.arg(user_id)
      AND organization_id = sqlc.arg(organization_id)
      AND role IN ('ADMIN', 'DEVELOPER')
)
ON CONFLICT (public_id) DO NOTHING
RETURNING id, organization_id, name, public_id, status, created_at;

-- name: GetProject :one
SELECT p.id, p.organization_id, p.name, p.public_id, p.status, p.created_at, r.role
FROM projects AS p
LEFT JOIN user_organization_relation AS r
  ON r.organization_id = p.organization_id AND r.user_id = sqlc.arg(user_id)
WHERE p.id = sqlc.arg(id);

-- name: ListProjectsForUser :many
SELECT p.id, p.organization_id, p.name, p.public_id, p.status, p.created_at
FROM projects AS p
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE r.user_id = sqlc.arg(user_id)
ORDER BY p.created_at DESC, p.id DESC;

-- name: ListOrganizationProjects :many
SELECT p.id, p.organization_id, p.name, p.public_id, p.status, p.created_at
FROM projects AS p
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE p.organization_id = sqlc.arg(organization_id)
  AND r.user_id = sqlc.arg(user_id)
ORDER BY p.created_at DESC, p.id DESC;

-- name: GetDeployableProject :one
SELECT p.id, p.organization_id, p.name, p.public_id, p.status, p.created_at
FROM projects AS p
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE p.public_id = sqlc.arg(public_id)
  AND r.user_id = sqlc.arg(user_id)
  AND r.role IN ('ADMIN', 'DEVELOPER');

-- name: MarkProjectDeployed :exec
UPDATE projects SET status = 'DEPLOYED' WHERE id = sqlc.arg(project_id);

-- name: CreateProjectBackup :exec
INSERT INTO project_backup (project_id, archive_path)
VALUES (sqlc.arg(project_id), sqlc.arg(archive_path));

-- name: TrimProjectBackups :many
DELETE FROM project_backup AS old_backup
WHERE old_backup.project_id = sqlc.arg(project_id)
  AND id NOT IN (
      SELECT recent.id FROM project_backup AS recent
      WHERE recent.project_id = sqlc.arg(project_id)
      ORDER BY recent.created_at DESC, recent.id DESC
      LIMIT 3
  )
RETURNING old_backup.archive_path;

-- name: ListProjectBackups :many
SELECT b.id, b.archive_path, b.created_at
FROM project_backup AS b
JOIN projects AS p ON p.id = b.project_id
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE p.id = sqlc.arg(project_id)
  AND r.user_id = sqlc.arg(user_id)
ORDER BY b.created_at DESC, b.id DESC;

-- name: GetProjectBackup :one
SELECT id, archive_path, created_at
FROM project_backup
WHERE project_id = sqlc.arg(project_id)
  AND id = sqlc.arg(backup_id);
