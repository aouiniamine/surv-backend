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
RETURNING id, organization_id, name, public_id, created_at;

-- name: GetProject :one
SELECT p.id, p.organization_id, p.name, p.public_id, p.created_at, r.role
FROM projects AS p
LEFT JOIN user_organization_relation AS r
  ON r.organization_id = p.organization_id AND r.user_id = sqlc.arg(user_id)
WHERE p.id = sqlc.arg(id);

-- name: ListProjectsForUser :many
SELECT p.id, p.organization_id, p.name, p.public_id, p.created_at
FROM projects AS p
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE r.user_id = sqlc.arg(user_id)
ORDER BY p.created_at DESC, p.id DESC;

-- name: ListOrganizationProjects :many
SELECT p.id, p.organization_id, p.name, p.public_id, p.created_at
FROM projects AS p
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE p.organization_id = sqlc.arg(organization_id)
  AND r.user_id = sqlc.arg(user_id)
ORDER BY p.created_at DESC, p.id DESC;

-- name: GetDeployableProject :one
SELECT p.id, p.organization_id, p.name, p.public_id, p.created_at
FROM projects AS p
JOIN user_organization_relation AS r ON r.organization_id = p.organization_id
WHERE p.public_id = sqlc.arg(public_id)
  AND r.user_id = sqlc.arg(user_id)
  AND r.role IN ('ADMIN', 'DEVELOPER');
