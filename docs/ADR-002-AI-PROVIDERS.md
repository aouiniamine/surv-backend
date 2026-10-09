# ADR-002: AI providers for Surv Agent

> **Current implementation update (9 October 2026):** Surv Agent now reads and edits `{public_id}/dev/public` directly. It no longer has a draft record, candidate tree, revision-gated draft publish, or `/draft` endpoint. Failed or cancelled runs can leave edits in the dev app. The live app changes only through explicit publish. See [the current architecture](SURV_AGENT_IMPLEMENTATION_ARCHITECTURE.md) for the implemented flow; older draft and candidate descriptions below record the original design.

- **Status:** Accepted; Ollama and Gemini adapters implemented
- **Date:** 2026-10-07
- **Updated:** 2026-10-08
- **Owners:** Surv backend
- **Related:** [ADR-001: Surv Agent integration](ADR-001-SURV-AGENT-INTEGRATION.md)

## Context

Surv Agent needs a model that can plan, produce code, and request constrained file/build tools. Provider availability, model quality, rate limits, pricing, and data policies change. Binding the agent loop to one provider SDK would make development and future migration harder. Free development options are useful, but their quotas and model availability are unsuitable as a production promise.

## Decision

Put a **provider adapter** behind the Surv Agent orchestration layer. The orchestrator owns prompts, skill selection, the tool registry, workspace authorization, run budgets, and the tool execution loop. A provider adapter translates only model messages, tool-call requests and results, streaming events, usage, and errors. Model output never executes a shell command or writes a file without passing through Surv's tool validator.

The first implementation used a local Ollama API; the second adapter uses Gemini's `streamGenerateContent` REST API. `AGENT_PROVIDER=ollama|gemini` selects one adapter for the API/worker process. Ollama uses its native `/api/chat` protocol, `OLLAMA_BASE_URL`, and `OLLAMA_MODEL`. Gemini uses `GEMINI_API_KEY` in the `x-goog-api-key` header and `GEMINI_MODEL`; its adapter preserves provider-native function-call IDs and thought signatures when sending tool results back. The key and provider selection stay server-side. A queued run records its provider and model; the worker fails it if those settings change before execution rather than routing it silently to another provider. A provider's advertised OpenAI-compatible endpoint is a transport convenience, not proof of identical tool semantics.

### Adapter contract

| Surface | Required behavior |
| --- | --- |
| Identity | Stable provider ID, exact model ID, capability flags, and configuration version per run. |
| Messages | Normalize system/developer/user/assistant/tool roles without losing tool-call IDs or ordering. |
| Tools | Return structured tool name and JSON arguments; reject unknown tools and invalid schemas before execution. |
| Response | Normalize ordered text and tool calls; preserve native data needed to continue a tool-use turn. Ollama streams from `/api/chat`; Gemini consumes `streamGenerateContent` SSE events and assembles a complete model turn. |
| Control | Support cancellation, request timeout, bounded retries for transient errors, and a maximum model/tool-step count; honor `Retry-After` for future cloud adapters. |
| Accounting | Record input/output tokens when reported and `unknown` when unavailable; estimate API cost from a versioned price table only for future paid providers. |

Do not silently retry a partially executed tool call on another provider. A fallback may occur before the first tool action, or after a checkpoint that proves all prior tool actions completed and will not replay. Preserve the provider and model chosen for every run and show them in diagnostics.

## Provider candidates

| Provider | Role in Surv | Development-cost option | Caveat |
| --- | --- | --- | --- |
| [Ollama](https://ollama.com/blog/streaming-tool) | Implemented local adapter and default | No per-token API charge on self-hosted hardware | Hardware, model license, quality, and tool-call reliability are the operator's responsibility. Keep the API private. |
| [OpenAI](https://platform.openai.com/docs/quickstart/make-your-first-api-request) | Possible later cloud adapter | Paid API; use budget-limited test account | Usage-based charges; do not assume a free API tier. |
| [Anthropic](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works) | Possible later cloud adapter | Paid API; use budget-limited test account | Tool-result format and stop reasons need their own adapter. |
| [Google Gemini API](https://ai.google.dev/gemini-api/docs/pricing) | Implemented cloud adapter, selected with `AGENT_PROVIDER=gemini` | Free tier for eligible models with free input/output tokens | Free-tier content may be used to improve Google's products; model access and quotas vary. Check [active limits](https://ai.google.dev/gemini-api/docs/rate-limits) before a pilot. |
| [GroqCloud](https://console.groq.com/docs/rate-limits) | Fast development experiments with supported open models | Free plan with model-specific limits | Limits are shared at organization level and may change; a 429 needs backoff. |
| [OpenRouter](https://openrouter.ai/pricing/) | Optional model comparison gateway | Free models and a free account quota | Another processor is in the data path; free access is limited and underlying model/provider may change. Pin model and route where supported. |

These are **options**, not simultaneous dependencies. Avoid fixed free-quota numbers in configuration or product copy; verify current eligibility, region, limits, data terms, and model tool support at onboarding and periodically afterward. Free-tier providers are disabled for customer project data by default until their data handling terms are accepted for that use.

## Configuration and data handling

- Local Ollama needs no cloud API key. Store the Gemini key in the server environment or secret store, never in the browser, a project workspace, prompts, logs, or generated application files. The worker receives a narrow provider client, not raw keys.
- Allow the worker to reach only the configured private Ollama endpoint and the fixed Google Gemini HTTPS endpoint when selected. Treat model-suggested URLs as untrusted and block access to metadata services, private networks, and the backend's internal services.
- Send the minimum relevant files to the provider. Exclude `.env`, credentials, uploaded secrets, unrelated projects, and the current live deployment unless the user explicitly requests a comparison and access rules permit it. Show users that project content may be sent to the selected provider.
- Set per-run token, tool-step, wall-time, and local compute/concurrency ceilings and organization-level usage limits. Stop a run when a ceiling is reached. Make retry behavior visible and honor provider `Retry-After` when present for future cloud adapters.
- Record provider, model, token usage when reported, local duration, error category, and configuration version. Key alias and estimated API cost apply only to future keyed/paid providers. Persist the bounded, redacted agent output needed for the client run view separately from operational telemetry; do not put full prompts, generated code, or raw model traces in general logs.
- Only operators can add a provider or endpoint. Organization-level provider choice, if later exposed, must be limited to the operator allowlist and compatible data-policy class.

## Rollout and acceptance

1. Implement the Ollama adapter and a fake adapter used by tests. Verify ordered text chunks, single and multiple tool calls, malformed arguments, streaming interruption, timeout, cancellation, and usage accounting. Confirm the selected local model actually emits reliable tool calls before enabling real file tools.
2. Pilot local Ollama on static app generation, edit requests, recovery from a failing build, and skill-assisted UI refinement. Measure code quality, latency, memory use, and run completion rate on the target hardware.
3. Gemini is available behind the same provider contract. Test its tool-use loop with an actual key and accept its data handling terms before sending customer content. Add further cloud adapters only when needed.
4. Keep the selected model configurable. If Ollama is unavailable or the model fails, fail or pause the run safely while preserving the last valid draft; never trigger publication.

## Rejected alternatives

- One provider SDK throughout the agent would mix model protocol with workspace and tool policy.
- Automatic lowest-cost routing for each turn would make behavior and data handling unpredictable and can replay tool calls incorrectly.
- Browser-held provider keys or arbitrary user-supplied endpoints would expose credentials and create an SSRF path.

## Sources checked 2026-10-07 and 2026-10-08

- [OpenAI API quickstart and tools](https://platform.openai.com/docs/quickstart/make-your-first-api-request)
- [Anthropic tool-use loop](https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works)
- [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) and [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling), [thought signatures](https://ai.google.dev/gemini-api/docs/thought-signatures), and [Generate Content API](https://ai.google.dev/api/generate-content)
- [Groq free-plan limits](https://console.groq.com/docs/rate-limits)
- [OpenRouter pricing and free plan](https://openrouter.ai/pricing/)
- [Ollama tool support](https://ollama.com/blog/tool-support)
- [Ollama streaming tool calls](https://ollama.com/blog/streaming-tool)
