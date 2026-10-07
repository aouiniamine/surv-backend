# Surv Agent requirements

**Status:** Proposed requirements · **Date:** 2026-10-07  
**Applies to:** `surv-backend/` and `surv-project/apps/client/`  
**Decisions:** [Agent integration ADR](ADR-001-SURV-AGENT-INTEGRATION.md), [AI providers ADR](ADR-002-AI-PROVIDERS.md)  
**Delivery sequence:** [Design and implementation plan](SURV_AGENT_DESIGN_PLAN.md)

## 1. Purpose and scope

Surv Agent helps an authorized user build or revise a project's static application. It works in the project's draft area, reports what it did, and lets the user review a site preview, agent output text, and edited-file diffs before an explicit publish action. The first model backend is a **local Ollama API**. Anthropic and OpenAI are later integrations behind the same application-owned interface.

The first release builds static files with a root `index.html`, matching Surv's current hosting contract. Running tenant application servers, autonomous publication, customer-supplied model endpoints, and arbitrary skill installation are outside scope. This document states requirements; the linked ADRs explain decisions, and the plan orders the work. All paths and APIs described here are planned, not implemented today.

`project_id` in API paths means the UUID `projects.id`. The backend resolves it through an organization-scoped query, then uses the validated seven-character `projects.public_id` for `${PROJECT_UPLOADS_ROOT}/{public_id}/dev/`. The live deployment remains at `${PROJECT_UPLOADS_ROOT}/{public_id}/public/`.

## 2. Functional requirements

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| F-01 | Admins and Developers can start a run for a project with a bounded prompt and an allowlisted skill selection. One project has at most one active run. | API returns a run ID promptly; concurrent start returns a conflict; nonmembers and QA cannot start. |
| F-02 | The agent creates/edits a private candidate copy of `dev/workspace/`, builds into a candidate static output, and installs a validated revision into `dev/public/` only after success. | Failed, cancelled, or invalid builds leave the last successful draft and live site unchanged. |
| F-03 | Runs move through `queued`, `running`, and exactly one terminal state: `succeeded`, `failed`, or `cancelled`. Cancellation is idempotent. | State transitions and worker-restart recovery are tested. |
| F-04 | The client can read ordered agent output text and safe tool/status summaries while a run progresses and after it ends. Reconnection resumes from a sequence cursor. | No missing or duplicated displayed events after reconnect; failed runs retain useful output. |
| F-05 | The client can list added, modified, and deleted source files and open a bounded per-file diff relative to the source snapshot taken at run start. Binary/oversized files show metadata instead of raw content. | Text diffs match the baseline and candidate; failed-run edits remain reviewable; paths are relative and safe. |
| F-06 | Members, including QA, can view the latest validated draft site through a private preview with working assets and SPA fallback. | Preview loads HTML and subresources, is labeled Draft, and remains separate from the live URL. |
| F-07 | An Admin or Developer can publish a specified draft revision only through a distinct, explicit action. | A stale revision conflicts; the existing deployment path creates the same live status and backup behavior as manual upload. |
| F-08 | Members can list and inspect recent runs for their project, including model, skill versions, timestamps, state, usage where known, safe failure reason, output, and diffs. | Cursor pagination and organization isolation are tested. |
| F-09 | Operator-installed, versioned skills can be selected by task. Impeccable is available for applicable frontend work. | Run record identifies exact skill versions; unavailable or invalid skills fail safely before work begins. |
| F-10 | The first provider is an operator-configured, tool-capable local Ollama model. Users do not choose a provider or arbitrary endpoint in the first release. | Startup checks endpoint/model availability; a representative file-tool task succeeds in a controlled pilot. |

### Client design requirements

The project page must present a run composer and three clearly separated review views: **Agent output**, **Changed files**, and **Draft preview**. Output indicates progress, tool actions, failures, and completion without exposing raw chain-of-thought. Changed files show a summary count and file statuses, then a readable per-file diff with additions/deletions and an explicit note that the comparison is from the run's starting source, not the live deployment. Draft preview identifies its revision and whether it is newer than the live deployment. Publish requires a confirmation showing the exact draft revision and live destination. A failed build still shows output and diffs, but has no new publishable revision.

Loading, empty, reconnecting, failed, cancelled, and stale-revision states must have distinct messages. The client must not infer authorization from visibility alone; server checks remain authoritative. Use the existing typed HTTP layer in `surv-project/apps/client/app/lib/http/` and place feature UI in `app/views/project/` or a dedicated agent view rather than constructing API URLs in components. Preview iframe content is untrusted and must not gain access to console state.

## 3. Security and data requirements

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| S-01 | Every run, event, diff, draft, preview, cancel, and publish lookup is bound to the project and current user's organization membership. Admin/Developer can mutate; QA can read/preview. | Cross-organization and guessed run/change IDs yield no data. |
| S-02 | Model-issued file/build tools can access only that run's candidate workspace. Enforce host-side path validation and an isolated process/container mount boundary. | Traversal, symlink/hard-link, special-file, and other-project/live-tree attempts are denied. |
| S-03 | The isolated runner has bounded CPU, memory, disk, file count, runtime, output, and network egress. It cannot reach Ollama, backend services, metadata endpoints, or secrets. | Resource and network-denial tests pass; exhausting a limit fails the run without affecting live files. |
| S-04 | The Ollama endpoint is trusted operator configuration accessible only to the worker. Future provider keys stay server-side and never enter prompts, generated files, browser responses, or the runner. | Secret scan of logs/events/artifacts and network tests pass. |
| S-05 | Project files, skill text, model output, and fetched content are untrusted instructions. Only Surv's fixed tool registry and policies grant actions. No model tool can publish. | Prompt-injection and malicious-skill tests cannot widen tool permissions or publish. |
| S-06 | Draft preview is authenticated on HTML and every asset request, isolated from the console origin, and served with private/no-store caching, restrictive CSP, and no referrer leakage. | Outsiders cannot load assets; iframe scripts cannot read console data. |
| S-07 | Agent text, tool summaries, and diffs are size limited and redacted before persistence or display. Render text/diffs as inert text, not executable HTML. | Secret fixtures are absent from responses/logs; XSS payloads render literally. |
| S-08 | Draft replacement and publish use a cross-process project lock and revision/checksum validation. Manual upload and restore use the same publication lock. | Concurrent run/publish/upload/restore tests cannot create mixed or stale deployments. |
| S-09 | Prompt, run event, diff, candidate, and build artifacts have documented retention and deletion policies; authorization is checked again when retrieving retained artifacts. | Expired artifacts are inaccessible; deleting a project removes its agent records and files. |

For browser preview, a short-lived, project-scoped session on an isolated origin is preferred. The normal JWT must not appear in iframe URLs. If the deployment topology cannot reliably send preview cookies, add an authenticated same-origin proxy/BFF before release. No public draft URL is an acceptable fallback.

## 4. Backend architecture and SOLID boundaries

The agent is a first-class domain under `src/domains/agent/`, with its own `/v1/agent/...` router. It owns orchestration, state, events, diffs, draft preview, skills, and provider integration. The projects domain owns project metadata and the live deploy/backup primitive. The agent domain calls a narrow project-deployment interface instead of reaching into project repository SQL or duplicating publication logic.

```text
src/domains/agent/
  controller.py         # HTTP DTO mapping and status codes
  dto.py                # request/response validation
  model.py              # domain entities and value objects
  service.py            # use cases; depends on interfaces
  repo.py               # named sqlc queries only
  worker.py             # run lifecycle and durable job handling
  workspace.py          # candidate, diff, draft revision operations
  tools.py              # fixed, validated tool registry
  skills.py             # operator skill manifests and selection
  providers/
    base.py             # provider interface and normalized types
    ollama.py           # initial native /api/chat adapter
    anthropic.py        # later
    openai.py           # later
```

This is a target dependency layout, not a mandate for one class per file. Use small Python `Protocol` interfaces or abstract base classes where behavior genuinely varies, constructor injection through application lifespan, and explicit typed domain objects. Avoid a generic framework that exposes every provider-specific feature before it is needed.

The provider seam can be expressed as a narrow asynchronous protocol (illustrative types, not a prescribed file implementation):

```python
class ModelProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    def capabilities(self, model_id: str) -> ModelCapabilities: ...

    def stream(
        self, request: ModelRequest
    ) -> AsyncIterator[ModelEvent]: ...
```

`ModelRequest`, `ModelEvent`, and `ModelCapabilities` are Surv-owned types. A startup registry maps allowlisted provider IDs to injected adapter instances. It rejects an unknown provider, unavailable model, or missing required capability before a run starts. Cancellation propagates through the async stream; the orchestrator remains responsible for state transitions and tool execution.

| Principle | Required design choice |
| --- | --- |
| Single responsibility | Controller maps HTTP; service enforces use cases; repository performs agent SQL; workspace manages files; provider adapter translates model protocol; tool executor applies host policy. |
| Open/closed | Register a new provider adapter against the stable provider contract; do not add `if provider == ...` branches throughout `AgentService`. |
| Liskov substitution | Each adapter passes the same contract suite for text, zero/multiple tool calls, stream interruption, cancellation, errors, and usage. Unsupported capabilities are declared and rejected before a run, not silently ignored. |
| Interface segregation | Keep model generation, tool execution, skill loading, file storage, and publication behind separate narrow interfaces; providers cannot receive a deployment or filesystem service. |
| Dependency inversion | `AgentService` and worker depend on provider/tool/repository/workspace interfaces supplied at startup, not concrete SDK clients or global singletons. |

The common model contract should carry `provider_id`, `model_id`, capabilities, normalized messages, ordered text deltas, tool-call ID/name/JSON arguments, finish reason, usage, and typed errors. The orchestrator owns the tool loop: validate the requested tool name and schema, execute through the restricted tool executor, persist a safe summary, and send the result back through the adapter. Provider adapters must never execute tools themselves. Represent cancellation and timeouts in the common contract. If a provider lacks reliable tool calling, fail its capability check rather than asking it to emit shell commands as prose.

| ID | Architecture requirement | Acceptance evidence |
| --- | --- | --- |
| A-01 | Agent routes, SQL, state, and artifacts have one domain owner under `src/domains/agent/`; publication uses a narrow project service interface. | No agent route under `/v1/projects` and no direct project SQL in agent services. |
| A-02 | Provider adapters implement the Surv-owned model protocol and declare capabilities; agent use cases depend only on that protocol. | Adding a fake or second adapter needs no change to controller, service, DTOs, or tool executor. |
| A-03 | Tool execution, workspace access, skill loading, and publication are separate injected interfaces with host-enforced policy. | A provider adapter cannot import or obtain the publish service or unrestricted filesystem handle. |
| A-04 | All enabled providers pass one contract suite and produce the same normalized event/error shapes. | Ollama, Anthropic, and OpenAI adapters each pass the shared suite before enablement. |

Keep migrations in `db/migrations/`, named SQL in `db/queries/agent/`, generated accessors in `src/generated/agent/`, and calls to those accessors in `src/domains/agent/repo.py`. Use UUIDv7 IDs, bounded database strings, organization-scoped reads, and explicit pagination. Wire long-lived stateless services once in `src/main.py` lifespan and expose them through `src/core/dependencies.py`. Do not hand-edit generated query modules. PostgreSQL is the durable source for run state and event cursors; disk holds protected candidate/diff artifacts with checksums. Define reconciliation for a crash between disk and database updates.

## 5. Provider integration requirements

### Initial Ollama adapter

Configure `OLLAMA_BASE_URL` and `OLLAMA_MODEL` on the worker. Use Ollama's native `/api/chat` with a model confirmed to support tool calls. Stream text to normalized events and collect tool-call chunks before dispatch; do not execute incomplete arguments. Keep the full assistant tool-call message and corresponding tool results in the model conversation as required by the selected model. Check endpoint availability and model readiness at startup and report a clear run failure if either disappears. Ollama has no per-token API charge, but runs still have token, step, time, memory, and concurrency budgets. The browser never calls Ollama directly. [Ollama documents streaming tool calls](https://ollama.com/blog/streaming-tool).

### Adding Anthropic and OpenAI later

Adding a **model within an existing provider** should require configuration, capability validation, and pilot tests, without editing `AgentService`. Adding a **new provider** should require one adapter, provider configuration, and the common contract tests. The rollout sequence is:

1. Implement `AnthropicProvider` or `OpenAIProvider` behind the same provider interface, mapping native streaming, tool calls, results, stop reasons, usage, errors, and cancellation to normalized types. Keep provider SDK types inside that adapter.
2. Add server-side credentials, endpoint/model allowlists, data-policy classification, budget/pricing configuration, and startup checks. Never let a project prompt supply an endpoint or key.
3. Run shared contract tests with recorded/fake provider responses, then opt-in integration tests using test credentials and synthetic projects. Compare static build quality, tool reliability, latency, and cost with the Ollama baseline.
4. Enable the provider for selected organizations only after policy review. Pin the provider and model for each run; never switch providers after a tool action unless a replay-safe checkpoint is proven. Preserve draft and run state on provider failure.

Anthropic's client tool loop returns `tool_use` blocks and expects matching `tool_result` blocks; that mapping belongs solely in its adapter. [Anthropic tool-use documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works) describes the protocol. OpenAI's Responses API returns function calls identified by `call_id` and accepts corresponding `function_call_output`; that mapping belongs solely in its adapter. [OpenAI function-calling documentation](https://developers.openai.com/api/docs/guides/function-calling) describes the protocol. These protocol differences must not appear in controller, service, repository, tool executor, or client DTOs.

## 6. Quality gates and release criteria

The common provider contract suite must run against the fake adapter and every enabled adapter. Domain tests must cover role checks, run state transitions, event replay, diff correctness, failed-run artifacts, revision conflicts, idempotent publish, and cross-project isolation. Storage and runner tests must cover path escapes, resource limits, crash recovery, and concurrency with manual deploy/restore. Browser tests must verify authenticated preview subresources, script isolation, output/diff rendering, reconnect behavior, and confirmation before publish.

Release the first version only when an Admin/Developer can start a local Ollama run, see its text and diffs, preview the validated draft, and publish a fixed revision; QA can review but cannot mutate; and every failure path preserves the existing live deployment. Later Anthropic or OpenAI support is complete only when its adapter passes the same contract and security gates without changes to agent use cases or client contracts.
