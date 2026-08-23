# CHIEF five-pass refinement — 2026-08-23

## Research synthesis

Current assistants are converging on a durable agent loop, typed tools, multimodal input, local/cloud routing, scoped memory, and explicit consent. OpenAI Responses exposes structured function tools, constrained tool choice, streaming lifecycle events, shell/computer/MCP tools, and multimodal items. Apple Foundation Models uses provider protocols, dynamic profiles, guided generation, tool calling, on-device/PCC routes, and token accounting. Gemini 3.5 Flash integrates computer use into a general model. LangGraph persists checkpoints for retries and human approval. MCP standardizes tools/resources while requiring audience-bound authorization and consent. Home Assistant separates wake word, STT, intent, and TTS so voice components remain replaceable and local-first.

Key sources: [OpenAI Responses](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses), [Apple Foundation Models](https://developer.apple.com/documentation/foundationmodels/), [Gemini computer use](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-computer-use-gemini-3-5-flash/), [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), [MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization), [Home Assistant voice pipelines](https://developers.home-assistant.io/docs/voice/pipelines/).

## Pass 1 — architecture, configuration, and model routing

1. Corrected the malformed abstract `ModelProvider.generate` contract.
2. Added a validated environment-backed settings boundary.
3. Made Ollama URL configurable.
4. Made the local model configurable.
5. Added validated model timeouts.
6. Added a maximum provider response size.
7. Reject empty prompts before provider I/O.
8. Added ordered provider fallback routing.
9. Added per-route success/failure attempt records.
10. Added response latency telemetry.
11. Made CORS origins configurable.
12. Changed private-LAN UI access to explicit opt-in.

## Pass 2 — memory, tools, permissions, and audit

1. Bound approval proposals to exact tool names and arguments with SHA-256.
2. Added five-minute approval expiry.
3. Made pending-call consumption atomic at the session boundary.
4. Added a short review code to confirmation text.
5. Exposed approval digest and expiry in queue telemetry.
6. Preserved explicit approve/cancel control.
7. Made audit writes thread-safe.
8. Bounded in-memory audit retention.
9. Added argument fingerprints without logging sensitive raw arguments.
10. Added tool execution duration telemetry.
11. Kept unknown tools deny-by-default.
12. Kept sensitive tools approval-only.

## Pass 3 — UI, performance, networking, and mobile

1. Added a shared bounded JSON request client.
2. Validate HTTP status and content type.
3. Added telemetry request deadlines.
4. Added cancellable long chat requests.
5. Persisted session identity across UI reloads.
6. Reduced telemetry polling frequency.
7. Paused polling while the page is hidden.
8. Refresh immediately when connectivity returns.
9. Surface offline state explicitly.
10. Abort outstanding work when the UI unmounts.
11. Made Vite loopback-only by default.
12. Added a separately named, intentional LAN development command.

## Pass 4 — reliability, memory hygiene, observability, and tests

1. Bounded session history to prevent unbounded memory growth.
2. Bounded individual message size.
3. Rejected empty session messages.
4. Normalized memory content.
5. Bounded memory content and tag counts.
6. Normalized and deduplicated tags.
7. Validated memory retrieval limits and thresholds.
8. Added recency as a deterministic retrieval tie-breaker.
9. Added request correlation IDs and latency logging.
10. Added no-store, nosniff, referrer, and device-permission headers.
11. Added a readiness endpoint distinct from liveness.
12. Added hardening, routing, configuration, approval-binding, and expiry tests.

## Pass 5 — cross-system regression and polish

1. Preserved the current `httpx2` test-client dependency after runtime verification.
2. Preserved exact single-provider error messages for operator clarity.
3. Made routing compatible with injected/test providers while retaining strict production contracts.
4. Modernized UTC datetime handling.
5. Corrected import ordering across the repository.
6. Marked planner constants as immutable class-level configuration.
7. Improved invalid-provider-response exception semantics.
8. Documented why the tool gateway intentionally contains unexpected adapter exceptions.
9. Updated the stale README status.
10. Expanded `.env.example` with safe defaults and LAN warnings.
11. Formatted the full Python codebase consistently.
12. Verified all Python tests, lint, dependency audit, and frontend production build.

## Subsequent expansion

The durable run/checkpoint store, schema-driven bounded plans, persistent chat approvals,
scoped temporal memory, protected remote/PWA boundary, and voice-provider interfaces originally
listed here as deferred work have since been implemented. The remaining production roadmap is
maintained in `docs/BEST_IN_CLASS_CHECKLIST.md`; it prioritizes live evidence connectors,
supervised always-on workers, a complete persistent plan-approval experience, production model
and streaming-voice adapters, isolated browser use, encrypted secrets/backups, and real-workload
evaluations. Those items require external credentials, owner policy, infrastructure, or staged
security validation and are not represented as complete.
