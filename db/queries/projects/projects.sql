-- name: CreateProject :one
INSERT INTO projects (organization_id, name)
SELECT sqlc.arg(organization_id), sqlc.arg(name)
WHERE EXISTS (
    SELECT 1
    FROM user_organization_relation
    WHERE user_id = sqlc.arg(user_id)
      AND organization_id = sqlc.arg(organization_id)
)
RETURNING id, organization_id, name, created_at;

-- name: GetProject :one
SELECT p.id, p.organization_id, p.name, p.created_at
FROM projects AS p
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE p.id = sqlc.arg(id)
  AND r.user_id = sqlc.arg(user_id);

-- name: ListProjectsForUser :many
SELECT p.id, p.organization_id, p.name, p.created_at
FROM projects AS p
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE r.user_id = sqlc.arg(user_id)
ORDER BY p.created_at DESC, p.id DESC;
