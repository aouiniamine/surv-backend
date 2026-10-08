# Surv Agent integration: design and implementation plan

**Status:** Planning document · **Date:** 2026-10-07  
**Decisions:** [Agent integration](ADR-001-SURV-AGENT-INTEGRATION.md) · [AI providers](ADR-002-AI-PROVIDERS.md)  
**Current deployment contract:** [Deployments and backups](APPS_DEPLOYMENTS_AND_BACKUPS.md)

The [requirements document](SURV_AGENT_REQUIREMENTS.md) defines the behavior and acceptance criteria for these steps.

## Goal and first release

Let an authorized organization member ask Surv Agent to create or revise a **static** application, read its output text and edited-file diffs, inspect a private draft site, and explicitly publish a chosen draft revision. Agent activity must not change the live deployment until promotion. The first release supports one active run per project and one locally hosted Ollama model; the adapter boundary allows more providers later.

Implementation note (October 2026): the backend agent domain, PostgreSQL queue and review records, bounded static-file tools, Ollama adapter, draft workspace, preview API, client review panel, and explicit publish flow are implemented. Impeccable is offered as a versioned, reviewed guidance excerpt; the original launcher and scripts are not executed with tenant files. A local `qwen3:4b` run created a draft through the full worker loop in an isolated temporary workspace. The production preview origin/cookie topology, broader browser and concurrency tests, operational retention, and a real project acceptance run remain release gates.

The user-facing project ID is `projects.id` (UUID). Storage uses `projects.public_id` (seven characters). Thus the requested `<project_id>/dev/public` maps to `${PROJECT_UPLOADS_ROOT}/{public_id}/dev/public/` after an authorized database lookup. The current live tree remains `${PROJECT_UPLOADS_ROOT}/{public_id}/public/`. `dev/public/` contains the built static site; optional source and dependencies live in `dev/workspace/`. A deployed ZIP contains built files, not necessarily editable source. New agent projects start from a controlled template; editing a previously uploaded app from source requires a separate source import. Static-file-only edits may use the deployed build as read-only context if authorized.

## Component design

```text
Project page
  -> /v1/agent run/publish API -> AgentService -> agent SQL + job queue
                                           -> draft metadata + project lock
  -> output stream + file diffs -> AgentService -> protected run artifacts
  -> private draft preview ----> AgentService -> project static-serving primitive

Worker -> local Ollama adapter -> model tool request -> Surv tool validator
                                             -> isolated runner (dev/workspace)
                                             -> candidate build -> validator
                                             -> revision-checked draft replacement

Explicit publish -> fixed draft revision -> validated ZIP -> existing deploy path
                                                     -> public/ + backups + DB status
```

The API and worker may be separate processes but share PostgreSQL and persistent project storage. Redis already exists for OTPs; a queue may use Redis, but PostgreSQL remains the durable source of run state. `src/domains/agent/` owns its controller, DTOs, service, repository, run events, diffs, draft preview, and publish orchestration. The projects domain continues to own project records and the existing deployment/backup operation; the agent domain calls that operation through a narrow service interface. The API never runs a long model/build loop within a request. Only the worker connects to Ollama, and the isolated build environment cannot reach its endpoint.

## Contracts to implement

### Persistence

Create an `agent_run` table with UUIDv7 `id`, `project_id` FK, `created_by` FK, bounded `status`, `provider_id = ollama`, bounded `model_id`, skill-version references, prompt reference or encrypted storage policy, `draft_revision` (nullable), timestamps, token counters, and a bounded error code. Add `agent_run_event` with `(run_id, sequence)` for ordered, bounded output text, tool-action summaries, and status events. Add file-change metadata with path, change type, size, and diff-artifact reference; store bounded diff artifacts in protected run storage, including for failed runs, under an explicit retention policy. Persist a project draft record with `revision`, `source_run_id`, checksum, and creation time; the draft record must correspond to the files currently served. Use migrations under `db/migrations/`, named queries under `db/queries/agent/`, regenerate `sqlc`, and access it only through `src/domains/agent/repo.py`.

Keep the state machine small:

```text
queued -> running -> succeeded
                  -> failed
         \--------> cancelled
queued ----------> cancelled
```

Cancellation is idempotent. A worker lease/heartbeat identifies abandoned `running` jobs after restart; recovery marks them failed and cleans only that run's staging tree. A successful run means the candidate has been validated and installed as the latest draft, not published. Record the model and exact skill versions used even if the run fails.

### Agent output and file changes

Stream the agent's user-visible output text as ordered events with a monotonic sequence; persist those events so the client can resume after a reconnect using its last sequence. Separate model text from tool-action summaries and error/status events. Redact credentials and sensitive file content before persistence; cap each event and total run output. Do not expose raw chain-of-thought or unrestricted tool stdout. The client shows the text in a run transcript, with a clear running/failed/completed state.

Before a run edits files, snapshot the project's current editable source. Work on a private candidate copy. When the run stops, compare that baseline with the candidate and produce a per-file manifest: added, modified, deleted (and renamed if reliably detected). Store a bounded unified diff for text files and a metadata-only entry for binary or oversized files. Paths are relative to `dev/workspace/`; never emit absolute server paths. This is a **run-baseline-to-candidate source diff**, not a comparison against the deployed `public/` tree. Keep the diff for a failed run if edits were made, even though its candidate is not installed as the latest draft. Run artifacts are access controlled and expire by policy.

### API sketch

The paths below are proposed contracts owned by the agent router, mounted at `/v1/agent`. Add DTOs and error mapping before client integration. Use UUID project IDs and organization-scoped SQL for every protected operation. No agent endpoint belongs under `/v1/projects`.

| Method and path | Role | Result |
| --- | --- | --- |
| `POST /v1/agent/{project_id}/runs` | Admin, Developer | Accept `{prompt, skills?}`; Ollama model is configured by the operator. Return `202` and run ID. |
| `GET /v1/agent/{project_id}/runs/{run_id}` | Member, including QA | State, usage, draft revision, output summary, safe error details. |
| `GET /v1/agent/{project_id}/runs` | Member | Recent runs with cursor pagination. |
| `GET /v1/agent/{project_id}/runs/{run_id}/events?after={sequence}` | Member | Ordered, paginated output/status events; optional SSE transport uses the same sequence. |
| `GET /v1/agent/{project_id}/runs/{run_id}/changes` | Member | Changed-file manifest with change type and diff availability. |
| `GET /v1/agent/{project_id}/runs/{run_id}/changes/{change_id}` | Member | Bounded text diff or binary/oversize metadata. |
| `POST /v1/agent/{project_id}/runs/{run_id}/cancel` | Admin, Developer | Request cancellation; return current state. |
| `GET /v1/agent/{project_id}/draft` | Member | Current draft revision and preview availability. |
| `POST /v1/agent/{project_id}/preview-session` | Member | Establish a short-lived, project-scoped preview session. |
| `GET /app/{public_id}/dev/{asset_path:path}` | Preview session | Serve only the current validated draft and its assets, including the root iframe at `/app/{public_id}/dev/`. |
| `POST /v1/agent/{project_id}/publish` | Admin, Developer | Require `{revision}` and an idempotency key; return live URL or a conflict. |

Use `404` for an inaccessible/nonexistent project where disclosure would matter, `409` for an active run or stale draft revision, and `422` for an invalid prompt/skill/model or invalid draft. Cap prompt size and event payloads. The preview-session endpoint must never put the normal JWT in a URL. Serve preview assets from an isolated origin with a short-lived HttpOnly, Secure, project-scoped session cookie and `Cache-Control: private, no-store`; verify current project membership on each asset request so revocation takes effect. Prevent referrer leakage and frame access to the console origin. If the deployment topology cannot support reliable authenticated iframe assets (including browser third-party-cookie restrictions), ship a same-origin preview proxy/BFF before exposing preview. Do not weaken the preview to a public route or long-lived URL token.

### Workspace and publication

Use `ProjectStorage.project_dir(public_id)` only after the project lookup. Suggested disk layout:

```text
{public_id}/
  public/                 # live; current code owns this
  current.zip             # live archive
  backups/                # existing backups
  dev/
    workspace/            # editable project source
    public/               # latest validated draft
    .runs/{run_id}/        # private candidate and temporary files
```

The runner edits only its private candidate copy of the project workspace and build directory; the persisted `dev/workspace/` remains the baseline until a successful run is installed. A model tool cannot address `public/`, `current.zip`, backups, another project, or the backend repository. Enforce this in the host tool dispatcher and at the process/container mount boundary. Reject traversal, symlinks, hard links, special files, and paths that escape after resolution. Do not trust `cwd` or model-provided paths as a security boundary. Restrict network egress, subprocess duration, CPU, memory, file count, disk bytes, and output bytes. Mount approved skills read-only. Avoid shared `node_modules` or build caches across tenants unless safely isolated.

Validate the candidate against the current static deployment contract: root `index.html`, safe file types and paths, at most 10,000 entries, at most 1 GiB extracted content, and a ZIP at most 20 MiB for promotion. Reuse validation logic from `src/domains/projects/storage.py`; do not assume its ZIP extractor alone validates an already-unpacked directory. After validation, create an immutable revision checksum and replace `dev/workspace/` and `dev/public/` under the project lock also used by promotion. Use versioned candidate directories and a recovery record so both source and built output resolve to the same committed revision after a crash; do not assume two directory renames are atomic together. Retain the previous draft until replacement succeeds. PostgreSQL and disk are not one transaction.

Publishing first verifies role, the supplied revision, checksum, and current draft under a **project-wide publication lock**. It packages a snapshot and uses the existing deploy/backup recording path. Manual ZIP upload and backup restore must acquire the same lock; the current implementation has no such lock. An in-process mutex is insufficient with multiple workers, so use a cross-process lock such as a PostgreSQL advisory lock or durable lease and document its crash behavior. Do not expose `publish` as a model tool. A repeated request with the same idempotency key must not create another backup.

## Implementation steps and completion gates

| Step | Work | Completion gate |
| --- | --- | --- |
| 1. Lock and storage foundations | Add the project-wide publish lock to upload and restore; refactor static asset lookup/rewriting to accept a validated root and URL prefix; add draft staging and file validation. | Concurrent upload/restore tests serialize; existing live and backup URLs still work; path-escape tests fail closed. |
| 2. Agent domain and API | Add migration, named `sqlc` queries, generated accessors, `src/domains/agent/` (`controller.py`, `dto.py`, `service.py`, `repo.py`, `model.py`), dependencies, and app wiring. Mount only under `/v1/agent`. Implement run state, access checks, cancellation, events, and change manifest/detail routes. | Role matrix and organization-isolation tests pass; API returns a run ID without blocking; no agent route appears under `/v1/projects`. |
| 3. Isolated worker and diffs | Add durable queue consumption, leases, one-run-per-project coordination, runner limits, safe file tools, baseline/candidate snapshots, candidate validation, draft replacement, ordered output events, and bounded per-file diffs. Use a fake provider first. | Restart/cancel/failure tests preserve live files and the last valid draft; event replay and failed-run diffs work; cross-project and symlink attacks are rejected. |
| 4. Local Ollama adapter | Implement the normalized message/tool/usage interface from ADR-002 against local `/api/chat`. Configure endpoint and model, check tool capability, and add timeouts, budgets, local resource monitoring, and secret redaction. | Contract tests cover malformed tools, partial streams, cancellation, exhausted budgets, and Ollama unavailability; selected model completes representative tool calls; runner cannot reach the Ollama endpoint. |
| 5. Skills | Add signed or checksum-pinned operator skill manifests and a read-only skill mount. Integrate Impeccable for applicable frontend tasks, including its context and task reference flow. | Run record shows exact skill versions; a hostile skill instruction cannot widen tool or file permissions. |
| 6. Private preview | Add preview session, authenticated asset route, isolated origin/proxy, CSP, and SPA asset rewriting; test a built draft with nested assets and refresh. | QA can preview; outsiders cannot; preview never resolves to live or another project's files. |
| 7. Client and promotion | Add a dedicated project-level agent page at `/workspace/{organization_id}/projects/{project_id}/agent` in `../surv-project/apps/client/app/views/agent/`, with the breadcrumb `Workspace / <project> / Surv Agent`. Show output during a run, reveal changed files after it ends, and showcase the private `/app/{public_id}/dev/` draft with a full-window action. Add server-side revision check and idempotent promotion. | User can read text during a run, inspect diffs after it ends (including a failed run), open the draft in a new window, and explicitly publish; live URL and backups change only after publish; stale revisions return `409`. |
| 8. Rollout and operations | Add feature flag, per-organization limits, storage/event retention, metrics, alerting, and a pilot with synthetic projects. Document worker/storage recovery. | Pilot meets reliability, cost, isolation, and preview checks before enabling customer content. |

Each step can be reviewed independently. Steps 1–3 establish a safe draft and diff pipeline with a fake provider; steps 4–5 enable local Ollama and skills; steps 6–7 expose the workflow to users. Do not call the feature complete without output text, file diffs, site preview, and explicit publish.

## Verification matrix

| Area | Required cases |
| --- | --- |
| Authorization | Admin/Developer create, cancel, publish; QA read/preview; nonmember and wrong-organization requests denied. |
| Isolation | Traversal, symlink/hard-link escape, special files, other-project paths, live deployment paths, secret files, and disallowed network destinations. |
| Durability | Worker crash before/after candidate swap, database failure after filesystem change, queue redelivery, duplicate publish, and abandoned lease. |
| Concurrency | Two runs for one project; run plus publish; publish plus manual upload; publish plus restore; two different projects in parallel. |
| Static output | Missing `index.html`, oversized archive/tree, unsafe ZIP, nested SPA route, root-relative assets, MIME type, and missing assets. |
| Browser preview | Authentication on HTML and subresources, cookie behavior in target deployment topology, no cache/referrer leakage, sandbox and CSP, no console-origin access. |
| Agent output and diffs | Ordered chunks and reconnect cursor, redaction, file add/edit/delete, binary and oversized files, failed-run changes, path normalization, and organization authorization. |
| Provider and skills | Invalid tool JSON, unsupported local model, timeout, cancellation, budget exhaustion, Ollama outage, malicious prompt injection, unavailable Impeccable launcher. |

## Operational decisions before the pilot

Set concrete values for project disk quota, maximum run duration/steps, prompt retention, output/diff artifact retention, organization concurrency, and local model resource budget in configuration. Choose the Ollama model and trusted worker endpoint, then test tool-call quality on the target hardware. Select the preview origin and verify its cookie behavior in the actual console/API deployment topology. Choose the worker queue and cross-process lock implementation, then document cleanup after a crash. Decide how users export source in `dev/workspace/`; Surv currently stores only static deployments. These values do not change the isolation and explicit-publish requirements above.
