# Surv backend agent rules

These rules apply only to work inside `surv-backend/`.

- Do not run Git commands or stage, commit, or push files. The user handles all Git operations.

## Database and service rules

- Keep application queries in `db/queries/<domain>/*.sql` so `sqlc` can generate Python accessors. Keep schema changes in `db/migrations/`.
- Define database access through named `sqlc` queries and consume generated async methods only from each domain's `repo.py`. Do not put raw SQL in controllers or services.
- When a new filter or pagination shape is needed, add or update the owning domain's SQL query first, run `sqlc generate` from this directory, then update the repository and service.
- Prefer named `sqlc.arg(...)` parameters, and `sqlc.narg(...)` where a nullable parameter is appropriate. Keep pagination and filter inputs explicit and type-safe across the API DTO, service, repository, and generated query.
- Avoid unnecessary database round trips. If a use case fetches one row only to fetch a related row by its foreign key, consider one joined `sqlc` query that returns the needed data.
- Treat repeated per-entity fetches in services, repositories, and response builders as a possible N+1 query pattern. Prefer a batch query, join, or query that returns the related data together. Measure query plans and result sizes before choosing a larger query.
- Make a narrow raw-SQL exception only when a query is genuinely too complex, dynamic, or awkward for `sqlc` to model. Keep it inside the owning repository, parameterize every value, and document why `sqlc` was unsuitable.
- Keep each domain under `src/domains/<domain>/`, with business logic in `service.py`, HTTP behavior in `controller.py`, request and response schemas in `dto.py`, and PostgreSQL interaction in `repo.py`. Keep shared application code in `src/core/` and the FastAPI entry point in `src/main.py`. Do not edit `src/generated/` query modules by hand.
- Construct each repository and service once during application lifespan and retrieve the existing service through dependencies. Keep them stateless across requests; acquire and release database connections inside repository operations.
- Use PostgreSQL 18 `uuidv7()` for entity IDs and keep the UUIDv7 checks in migrations. Use one transaction for user, organization, and ADMIN membership creation.
- Use bounded `VARCHAR(n)` columns for short string fields. Match API DTO maximum lengths to their database column limits; do not add `TEXT` columns unless a use case needs them.
- Every project must belong to an organization. Scope project inserts and reads to the current user's organization membership in SQL; do not expose projects through unscoped queries.
- Send OTPs with the SMTP mailer and Jinja2 templates. Keep Redis OTP and validation marker behavior in `AuthService` using the shared Redis client; do not create a Redis repository or store. Store registration OTPs at `register:<email>` and validated email markers at `register:validated:<email>`. Do not impose a resend cooldown or invalid-OTP attempt limit unless requested. Complete registration using the email in the body without a registration token. Return signed JWT bearer tokens in the login response body; validate incoming tokens and extract the user ID from the `Authorization` header on protected routes. JWTs expire after three days and must never be stored in Redis. Never set authentication cookies or log OTPs or bearer tokens.
