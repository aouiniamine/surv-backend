-- name: CreateOrganization :one
INSERT INTO organizations (name)
VALUES (sqlc.arg(name))
RETURNING id, name, created_at;

-- name: AddOrganizationMember :exec
INSERT INTO user_organization_relation (user_id, organization_id, role)
VALUES (sqlc.arg(user_id), sqlc.arg(organization_id), sqlc.arg(role));

-- name: ListUserOrganizations :many
SELECT o.id, o.name, o.created_at, r.role
FROM organizations AS o
JOIN user_organization_relation AS r ON r.organization_id = o.id
WHERE r.user_id = sqlc.arg(user_id)
ORDER BY o.created_at DESC, o.id DESC;
