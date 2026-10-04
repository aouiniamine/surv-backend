# Surv backend architecture

This document applies to `surv-backend/` only. Surv's backend is a FastAPI application organized by business domain. PostgreSQL 18 stores application data and generates UUIDv7 entity IDs. Each domain owns its SQL, `sqlc` generates typed async query methods, and repositories call those methods through SQLAlchemy's async Core connection with the `asyncpg` driver. SQLAlchemy is a connection and execution layer here; there are no ORM models. Redis holds short-lived OTP challenges and login sessions.

## Boundaries

```text
HTTP request -> controller -> service -> repository -> sqlc query -> PostgreSQL
                   DTOs       |                 generated code
                              +-> Redis client -> Redis
```

Each domain contains:

- `controller.py`: routes, dependency injection, and HTTP error mapping.
- `dto.py`: Pydantic request and response schemas.
- `service.py`: use cases, business rules, and coordination.
- `repo.py`: PostgreSQL interactions for that domain only.
- `model.py` and `errors.py` where domain objects and failures are needed.

Controllers do not issue queries. Services do not import FastAPI or generated database classes. Repositories translate generated rows into domain models. HTTP DTOs and generated row types do not cross into the service's business rules. `AuthService` receives the shared Redis client and owns OTP, registration marker, and login session behavior; `AuthRepository` accesses PostgreSQL only. See [AGENTS.md](AGENTS.md) for enforceable query and round-trip rules.

## Current layout

```text
surv-backend/
├── AGENTS.md
├── ARCHITECTURE.md
├── README.md
├── Makefile
├── .env.example
├── compose.yaml
├── pyproject.toml
├── sqlc.yaml
├── db/
│   ├── migrations/
│   └── queries/{projects,users,organizations}/
├── src/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── redis.py
│   │   ├── email.py
│   │   └── dependencies.py
│   ├── domains/
│   │   ├── auth/
│   │   ├── users/
│   │   ├── organizations/
│   │   └── projects/             # each domain has controller, dto, service, repo
│   ├── generated/                # sqlc output; never hand-edit query modules
│   └── templates/                # Jinja2 OTP email templates
└── tests/
```

`auth` coordinates identity flows, `users` owns user records, and `organizations` owns organizations and memberships. `projects` remains a minimal vertical slice. Domain code lives under `src/domains/`; shared application wiring lives under `src/core/`. For each new SQL-owning domain, add `db/queries/<domain>/`, a corresponding `sql` entry in `sqlc.yaml`, generated output under `src/generated/<domain>/`, and the four required domain files. Auth composes the user and organization repositories for registration, so it has no separate SQL query directory.

## Database lifecycle

`db/migrations/` is the schema source. dbmate applies its ordered `-- migrate:up` and `-- migrate:down` files. `sqlc` parses those migrations but does not apply them. Queries live in `db/queries/<domain>/*.sql` and use named parameters such as `sqlc.arg(name)`; use `sqlc.narg(name)` for nullable parameters when appropriate. Generated Python output is updated when queries or migrations change.

User emails use `VARCHAR(254)`, first and last names use `VARCHAR(100)`, organization names use `VARCHAR(150)`, and project names use `VARCHAR(120)`. The API email request limit matches the database column.

The pinned official Python plugin generates an `AsyncQuerier` that accepts a SQLAlchemy `AsyncConnection`. The engine uses `postgresql+asyncpg` under the hood. Application settings accept a regular `postgresql://` URL so dbmate and the API can share `DATABASE_URL`; `core/config.py` converts it to the async driver URL for SQLAlchemy. The Python plugin's PostgreSQL support is [beta](https://docs.sqlc.dev/en/latest/reference/language-support.html), so generated behavior should be checked against a real PostgreSQL database as query complexity grows.

The application creates one bounded async engine, one Redis client, and one instance of each repository and service per worker during FastAPI lifespan. FastAPI dependencies retrieve existing services from application state on every request. Services and repositories stay stateless across requests. The auth service uses the shared Redis client directly; no Redis repository or store is constructed. Registration uses one PostgreSQL transaction to create the user, organization, and ADMIN membership. A repository acquires a connection only for the duration of a query or transaction. Keep SMTP and other network calls outside PostgreSQL transactions. The engine and Redis client close on shutdown.

## Data model and access boundaries

The role enum is `ADMIN`, `DEVELOPER`, or `QA`. `user_organization_relation` has a composite `(user_id, organization_id)` primary key, making users and organizations many-to-many. The first user in a new organization receives `ADMIN`. Each project has a required `organization_id` foreign key. Organization deletion is restricted while projects exist.

Project creation and reads require a user who belongs to the project's organization. The project SQL performs the membership check in the same query as the insert or read.

Entity IDs are PostgreSQL UUIDv7 values created by `uuidv7()` and enforced by a version check. Redis session tokens are random bearer secrets, not entity IDs. PostgreSQL 18 is required for the migration functions.

## Performance and correctness

- Async I/O lets other requests run while a query waits; query shape and indexes determine database speed. Inspect slow queries with `EXPLAIN (ANALYZE, BUFFERS)`.
- Keep the pool bounded and size it against PostgreSQL's connection limit and the number of API workers. Avoid long transactions and unbounded database concurrency.
- Select the columns needed by the use case, avoid repeated per-row lookups, and use batch or joined queries when they reduce round trips without creating oversized result sets.
- Prefer keyset pagination for large changing collections. Keep filter and cursor parameters explicit from DTO through SQL.
- Enforce business invariants in services and critical uniqueness, checks, and foreign keys in PostgreSQL. Use one transaction for atomic multi-step writes.
- Keep the Redis OTP, validation, and session keys private and short-lived. Never log OTPs or bearer tokens.

## References

- [FastAPI async behavior](https://fastapi.tiangolo.com/async/) and [lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [sqlc Python plugin](https://github.com/sqlc-dev/sqlc-gen-python), [named parameters](https://docs.sqlc.dev/en/latest/howto/named_parameters.html), and [migration parsing](https://docs.sqlc.dev/en/latest/howto/ddl.html)
- [dbmate commands](https://github.com/amacneil/dbmate#commands)
- [PostgreSQL UUIDv7](https://www.postgresql.org/docs/18/functions-uuid.html), [Redis asyncio](https://redis.readthedocs.io/en/latest/examples/asyncio_examples.html), [aiosmtplib](https://aiosmtplib.readthedocs.io/en/latest/usage.html), and [Jinja templates](https://jinja.palletsprojects.com/en/stable/api/)
