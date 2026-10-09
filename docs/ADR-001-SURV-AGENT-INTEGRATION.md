# ADR-001: Surv Agent integration and isolated development workspace

> **Current implementation update (9 October 2026):** Surv Agent now reads and edits `{public_id}/dev/public` directly. It no longer has a draft record, candidate tree, revision-gated draft publish, or `/draft` endpoint. Failed or cancelled runs can leave edits in the dev app. The live app changes only through explicit publish. See [the current architecture](SURV_AGENT_IMPLEMENTATION_ARCHITECTURE.md) for the implemented flow; older draft and candidate descriptions below record the original design.

- **Status:** Proposed
- **Date:** 2026-10-07
- **Owners:** Surv backend and client
- **Related:** [ADR-002: AI providers](ADR-002-AI-PROVIDERS.md), [Deployments and backups](APPS_DEPLOYMENTS_AND_BACKUPS.md)

See the [requirements](SURV_AGENT_REQUIREMENTS.md) for testable behavior and the [design and implementation plan](SURV_AGENT_DESIGN_PLAN.md) for phased steps, API sketches, and verification gates.

## Context

Surv currently accepts a ZIP of a built static application and serves it from `${PROJECT_UPLOADS_ROOT}/{public_id}/public/`. Upload and restore replace that directory and update deployment status and backup history. There is no application-building agent, development preview, or draft-to-live promotion flow. An agent must be able to create and revise an application without changing the current deployment.

The API uses a UUID `projects.id` for project metadata and a seven-character `projects.public_id` for disk paths and public URLs. In this ADR, the requested `<project_id>/dev/public` is the **logical** project draft. Its physical path is `${PROJECT_UPLOADS_ROOT}/{public_id}/dev/public/` after resolving the UUID through an organization-scoped project lookup. Do not interpolate a caller-supplied UUID or public ID into a filesystem path without this lookup and path validation.

## Decision

Introduce a Surv Agent as an asynchronous, project-scoped job in its own backend domain, `src/domains/agent/`, with routes under `/v1/agent/`. The agent domain owns runs, output events, change diffs, draft preview, and promotion orchestration; it uses the projects domain's authorized project/deployment primitives without placing agent routes under `/v1/projects/`. It writes only inside the selected project's `dev/` tree. Its publishable static output is `dev/public/`, with `index.html` at its root. Run candidates stay under `dev/.runs/` until validated and committed to `dev/public/`. The agent must never write live `public/`, `current.zip`, or `backups/` directly.

```text
${PROJECT_UPLOADS_ROOT}/{public_id}/
├── public/                 # current live deployment; existing flow owns this
├── current.zip             # current live archive; existing flow owns this
├── backups/                # existing deployment history
└── dev/
    ├── public/             # agent draft, served only by authenticated preview
    └── .runs/              # isolated candidate files during runs
```

The first implementation targets Surv's current **static hosting** contract. The agent may generate a static app or build source into static files. Running arbitrary project application servers is outside this decision.

## Agent workflow and boundaries

1. An authenticated Admin or Developer starts a run for a project UUID with a prompt and an optional approved skill set. The API checks organization membership before creating the run. QA may inspect the draft and run report, but may not start, cancel, or publish a run.
2. A worker acquires a per-project development lock, records a run ID and state (`queued`, `running`, `succeeded`, `failed`, `cancelled`), snapshots the starting source state, and creates a fresh staging directory under `dev/`. Existing `dev/public/` remains viewable until a new candidate passes validation.
3. The agent reads only project instructions and files explicitly mounted for the run. File tools are rooted at a private candidate copy under `dev/.runs/{run_id}/candidate/`; `dev/public/` is its starting snapshot until the run succeeds. Every path is resolved after normalization; reject traversal, symlinks, hard links, and links that escape the candidate. Run shell/build tools in a separate process or container with CPU, memory, disk, time, output, and network limits. Do not mount backend source, other projects, deployment credentials, database sockets, or provider keys into that runtime.
4. The worker validates the candidate with the same static archive limits as deployment: root `index.html`, safe regular files, bounded entry count and extracted size. It then atomically swaps the candidate into `dev/public/`. A failed or cancelled run leaves the last successful draft intact.
5. The draft has a distinct, protected preview at `GET /app/{public_id}/dev/...`, with organization authentication and no shared cache with the live `/app/{public_id}/` site. The client uses a sandboxed iframe and clearly labels the view **Draft**; clicking its card opens a full preview in a new window. Alongside the site preview it shows the agent's output text and, after the run ends, a per-file diff against the source snapshot taken when that run began. Text and diffs are separate authenticated agent-domain resources, so they can be inspected even if a build fails. Asset rewriting and SPA fallback reuse the existing static-serving behavior with a different root. The preview path requires a short-lived session on every asset request and never falls through to live files.
6. Publishing is a separate explicit Admin or Developer action. Package a snapshot of the validated `dev/public/` as a ZIP and send it through the existing deployment publication path and backup transaction. Recheck access and candidate revision under a per-project publish lock; reject stale revisions. Publishing changes live `public/` and backup history only through this path. The agent cannot invoke publish as a model tool.

The run API should expose the run ID, project ID, state, timestamps, ordered output events, a changed-file summary and per-file diff, draft revision, and preview URL. Persist durable state and authorization in PostgreSQL through the `agent` domain; keep its SQL in `db/queries/agent/`, generated accessors in `src/generated/agent/`, and schema changes in migrations. Redact secrets from output and cap event/diff size. A diff compares the run's starting source snapshot with its candidate source; label this basis in the client, since it is not a diff against the live deployment. The worker should be restartable: interrupted runs become failed or resumable by an explicit rule, never silently published. Use a project lock for both agent draft replacement and promotion so concurrent runs cannot overwrite each other or publish a half-written candidate.

## Skills integration

Skills are versioned, operator-installed instruction packages, not arbitrary files supplied by an end user or fetched during a run. A manifest records skill ID, version or digest, supported task types, required tools, and license. The server selects an allowlisted set for the run and logs which versions were used. Skill text is lower priority than Surv's system rules, workspace boundary, and user request. Treat project files, web pages, and tool output as untrusted input; they cannot grant new tools or extend the writable root.

For frontend work, support **Impeccable** as an optional skill. The client repository already contains `.agents/skills/impeccable/SKILL.md` and its launcher. The agent should load the installed skill from an approved copy or mounted version, call its context command once when applicable, then follow its task-specific references for design, audit, or polish. Its browser and shell helpers must run in the same restricted development workspace; any instruction to modify a live route or unrelated repository is denied. Other skills follow the same manifest, permission, and versioning contract. Skill output can inform a draft, but cannot bypass build validation or authorize publication.

## Consequences and implementation sequence

- The live URL and existing upload/restore behavior stay authoritative. A draft exists independently and may be deleted without changing a deployment.
- Development preview needs authenticated asset requests. The client must pass authorization safely; a short-lived, project-scoped preview token or authenticated proxy is preferable to putting the normal bearer token in iframe URLs. Preview content should have a restrictive CSP and no access to the Surv console origin.
- Disk usage increases because source, build cache, and draft are retained. Apply per-project quotas, cleanup for failed staging directories, and retention for run logs.
- Start with one worker, local Ollama, and one project lock, then add queues and capacity controls as demand grows. Deliver run state, output text, and file diffs with isolated draft creation; next add site preview; finally add explicit promotion through the existing deploy path.

## Rejected alternatives

- Letting the model edit `public/` directly risks changing the live site before review and bypasses backups.
- Treating `dev/public/` as a public static route leaks unpublished work and may expose generated content to unauthenticated visitors.
- Giving skills independent permissions makes a prompt or third-party package capable of escaping the project's workspace.

## Acceptance criteria

- A run for project A cannot read or write project B, backend files, or project A's live `public/` and backups, including through traversal or symlinks.
- Failed and cancelled runs preserve the previous draft and current deployment.
- Draft preview requires project membership; published and draft URLs resolve to different roots.
- Promotion requires a separate authorized action, validates a fixed draft revision, and creates the same backup behavior as a manual deployment.
- Run records identify provider, model, skill versions, tool actions, usage, and failure reason without storing secrets in logs.
- A member can read ordered agent output and each changed file's diff, including after a failed build; binary or oversized changes are identified without exposing unsafe content.
