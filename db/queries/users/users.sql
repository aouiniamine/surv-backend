-- name: CreateUser :one
INSERT INTO users (email, first_name, last_name)
VALUES (sqlc.arg(email), sqlc.arg(first_name), sqlc.arg(last_name))
RETURNING id, email, first_name, last_name, created_at;

-- name: GetUserByEmail :one
SELECT id, email, first_name, last_name, created_at
FROM users
WHERE email = sqlc.arg(email);

-- name: GetUserById :one
SELECT id, email, first_name, last_name, created_at
FROM users
WHERE id = sqlc.arg(id);
