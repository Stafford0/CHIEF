# CHIEF architecture

Status: implemented pre-alpha snapshot as of 2026-08-23

CHIEF is the orchestration and control system, not an individual language model. The current
architecture is a local, single-owner FastAPI process with a React PWA, replaceable model and
connector interfaces, explicit execution policy, and durable SQLite state.

This document describes what the repository implements now. Future work is labeled as a
limit, not presented as an existing capability.

## Architectural principles

1. **Local-first operation.** Loopback access, a local Ollama model, and local SQLite state are
   the defaults.
2. **Human authority.** A model or API caller cannot manufacture approval for a sensitive
   action. Exact, expiring approval and an operator kill switch sit outside model output.
3. **CHIEF-owned boundaries.** Domain logic consumes provider, tool, connector, speech, and
   persistence contracts rather than depending directly on a vendor SDK.
4. **Evidence before confidence.** Memories, business entities, decisions, signals, and
   connector results carry explicit provenance, confidence, sensitivity, or validity where
   appropriate.
5. **Bounded execution.** Plans have step/time budgets; worker ticks claim one leased step;
   retries, cancellation, idempotency, and verification are durable state.
6. **Fail closed.** Unknown tools, undeclared connector scopes, missing consent, invalid
   arguments, stale approvals, untrusted origins, and disabled remote access are refused.
7. **Inspectability.** Deterministic scoring, durable state transitions, correlated audit
   records, and offline evaluations make behavior reviewable.

## System context

```text
┌─────────────────────────────────────────────────────────────────────────┐
│ Owner device                                                            │
│                                                                         │
│  React/Vite PWA             Built-in local UI        API client         │
│  - responsive dashboard     - minimal chat           - typed JSON       │
│  - push-to-talk             - same FastAPI origin    - bearer optional  │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │ HTTP
┌───────────────────────────────▼─────────────────────────────────────────┐
│ FastAPI process                                                         │
│                                                                         │
│  Request boundary                                                       │
│  host/origin checks · LAN deny/opt-in · bearer actor · rate/body limits │
│  request ID · security headers · execution kill switch                  │
│                                │                                        │
│  Conversation plane           │       Co-founder operating plane        │
│  sessions · memory · chat ─────┼────── work · foresight · decisions      │
│  deterministic commands       │       business graph · attention        │
│                                │                                        │
│  Intelligence plane           │       Execution control plane           │
│  model contract · router ──────┼────── tools · guard · approvals         │
│  local Ollama adapter          │       plans · runs · events · audit     │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
                  ┌─────────────▼──────────────┐
                  │ data/chief.db              │
                  │ domain tables · WAL        │
                  │ append-only audit chain    │
                  └────────────────────────────┘

Unwired boundaries: live business connectors, streaming voice providers,
notification delivery channels, browser/computer-use worker, cloud model adapters.
```

## Composition root and request boundary

`chief.core.app` is the current composition root. At import/startup it:

- validates environment-backed settings;
- creates the FastAPI application and middleware;
- configures the local Ollama provider and model router;
- initializes each SQLite-backed domain store against `data/chief.db`;
- builds the guarded standard tool registry and persistent audit log;
- builds the bounded plan executor, scheduler, and durable run engine;
- registers two safe run handlers: `briefing.generate` and `foresight.snapshot`;
- mounts the decision, business graph, and notification API router.

The HTTP boundary applies these controls before protected application routes:

1. Only `/health` and browser preflight are public; readiness details stay protected.
2. Non-loopback access is denied unless private-LAN mode is explicitly enabled.
3. Private-LAN mode cannot start without a 32-byte-or-longer bearer token.
4. Non-loopback clients are rate limited by source address.
5. Mutating browser requests with an `Origin` header must match the configured origins.
6. Requests are constrained by trusted hosts and a total body-size ceiling.
7. A request ID and an actor ID are attached to downstream audit/session context.
8. Responses receive no-store, clickjacking, MIME, referrer, permissions, and content-security
   headers. HSTS is emitted only when the request reaches CHIEF over HTTPS.

The bearer token currently identifies one operator by a stable token digest. This is not
multi-user authentication, RBAC, device enrollment, token rotation, or delegation.

## Conversation lifecycle

The `/chat` path deliberately resolves deterministic control commands before asking a model:

```text
request
  → authenticate/derive actor
  → load or create actor-owned durable session
  → resolve approve/cancel against one pending proposal, if present
  → resolve explicit remember/correct/forget command
  → resolve a narrowly supported deterministic tool intent
       → safe tool: guarded execution and audit
       → sensitive tool: store exact proposal and wait
  → retrieve relevant active memory
  → combine identity + memory + bounded conversation context
  → route to Ollama
  → persist user and assistant messages
```

Conversation sessions and messages survive process restarts. Sessions are actor-scoped.
Pending sensitive tool proposals carry a UUID, exact tool/argument digest, creation time, and
five-minute expiry. Consumption is transactional and leaves a tombstone so stale in-process
state cannot reinsert a used proposal. Approval and rejection lifecycle events are audited.

The chat planner is intentionally deterministic and narrow. It is not a general model-driven
agent loop, and a natural-language reply is never treated as proof that a durable run occurred.

## Intelligence and model routing

`chief.models` defines the replaceable model boundary:

- provider name and `generate` contract;
- declared privacy tier: local, private network, or cloud;
- structured-output, tool-calling, streaming, vision, and audio capabilities;
- cost tier and route requirements;
- provider/model/latency response metadata.

`ModelRouter` filters providers against requirements, tries compatible providers in order,
records attempts, counts consecutive runtime failures, and temporarily opens a per-provider
circuit after the configured threshold. A successful call closes that circuit.

The production composition currently registers only `OllamaProvider`, which calls the local
non-streaming `/api/generate` endpoint with bounded timeout and response size. Although the
router supports capability, privacy, and cost constraints, ordinary chat currently uses the
default route requirements. There are no production cloud, vision, audio, or embedding model
adapters.

## Memory and sessions

`chief.memory` stores explicit `MemoryRecord` objects rather than relying on opaque chat
history. Records include:

- semantic, episodic, decision, or procedural type;
- personal, organization, project, or session scope and optional scope ID;
- public, internal, confidential, or restricted sensitivity;
- source type/description plus optional URI, source ID, and observed time;
- confidence, importance, tags, temporal validity, expiry, and active state;
- supersession links for corrections.

Retrieval uses bounded FTS5 candidates when available and a deterministic ranking layer;
expired, inactive, and temporally invalid records are excluded. Explicit correction preserves
history by deactivating/superseding rather than silently rewriting the old fact. Explicit
forgetting deletes the target memory.

This is useful structured local memory, not a complete learned memory system. It has no
production embedding reranker, automatic consolidation, contradiction resolver, deletion
propagation from external sources, or real-workload memory-quality calibration.

## Guarded tool execution

Every executable local capability implements a CHIEF `Tool` and exposes a `ToolDefinition`
containing:

- a unique name and description;
- safe, controlled, or sensitive risk;
- whether approval is required;
- a machine-readable input schema;
- side-effect, idempotency, and timeout declarations.

The standard registry contains scoped directory listing, file reading, file search, system
status, process status, a no-argument allowlisted PowerShell read boundary, and sensitive
PowerShell/shell command tools. Filesystem tools resolve paths under the configured project
root. Command tools are not implicitly authorized by their presence in the registry.

The registry is the choke point: it rejects unknown tools, evaluates `ToolPolicy`, contains
adapter failures, and writes an audit event for denied, approval-required, successful, and
failed attempts. Raw arguments and results are not copied into the audit event; SHA-256
digests and timing are recorded instead.

Direct `/tools/execute` requests cannot supply an approval flag, so that endpoint can run only
what policy permits automatically. Sensitive chat execution requires the exact persisted
session proposal. This prevents a client from changing arguments after approval.

## Bounded plans and approval boundaries

`chief.agents` provides typed caller-bounded plans:

- a maximum of eight steps in the production executor;
- a plan-level duration budget;
- schema validation against registered tools before execution;
- ordered, fail-fast step outcomes with argument digests and durations;
- actor-bound, expiring, single-use approval grants for gated steps.

`/plans/validate` returns structural validity and the number of gated steps. `/plans/execute`
executes allowed steps and pauses at the first step without a matching grant.

The plan approval ledger is currently in memory and the HTTP API does not expose a complete
grant-issuance workflow. Consequently, sensitive plan steps safely remain awaiting approval in
the default application. Persistent chat proposals and plan grants are two separate mechanisms
that should eventually converge.

## Durable runs

`chief.runs` is the restart-safe execution substrate. A run contains ordered step
specifications. The SQLite store persists:

- run, step, and attempt status;
- input/result digests and idempotency keys;
- correlation IDs and error codes;
- worker lease token/owner/expiry;
- retry availability and maximum attempts;
- verification status and checkpoint data;
- append-only run event history;
- cancellation and restart recovery state.

`RunEngine.execute_once` claims at most one eligible step, invokes only an explicitly injected
handler, checkpoints the result, and advances or fails the run. Unknown handlers fail closed.
Handler exceptions become durable failures; retryable failures can be rescheduled; lost leases
do not claim success. A step marked verification-required cannot complete unless its handler
returns `VERIFIED`.

The API can create, inspect, list, and cancel runs, list steps, and advance one worker tick. No
background worker is started by the application. Continuous processing requires a supervised
external service, and the current composition exposes only the briefing and foresight snapshot
handlers.

## Events and schedules

`chief.events` separates time-based intent from action execution. Schedules support one-time,
fixed-interval, and daily cadence with an IANA time zone. The scheduler computes the next
occurrence and queues deduplicated events. The event store tracks pending, leased, completed,
failed, and dead-letter states with bounded retries.

`POST /scheduler/tick` queues at most one due event and obeys the global execution kill switch.
It does not execute arbitrary event payloads. No resident clock loop, webhook ingress, or
event-to-run dispatcher ships yet.

## Co-founder operating domains

These modules are durable structured state, not autonomous agents by themselves.

### Work

`chief.work` persists goals and tasks with status, priority, target/due dates, blockers, and
goal relationships. A deterministic briefing ranks active work and explains urgency. The API
provides goal/task create, list, and update operations plus the generated briefing.

### Foresight

`chief.foresight` persists risks, opportunities, anomalies, and trends plus assumptions and
KPIs. High-confidence signals require evidence references. Ranking is transparent: impact,
urgency, confidence, freshness, and irreversibility contribute visible score components.
This is attention support, not a calibrated prediction engine.

### Decisions

`chief.decisions` persists decision status, criteria, options, option scores, evidence,
assumptions, risks, provenance, confidence, owner, review time, and outcome fields. The scoring
service normalizes criterion weights, supports temporary weight overrides for sensitivity
inspection, and exposes every contribution. API endpoints save/list/get records and score a
stored decision. The current UI does not provide a decision workbench or outcome-learning loop.

### Business graph

`chief.business` models typed business entities and relationships with server-derived owner
scope, confidence, sensitivity, provenance, and validity intervals. The SQLite store supports
filtered reads and bounded graph traversal by direction, relationship kind, depth, nodes, and
edges. It is empty until trusted data is entered or synchronized; no entity resolution or live
connector population is wired into the application.

### Notifications and attention

`chief.notifications` persists notifications, deterministic attention decisions, delivery
attempts, and delivery receipts. Policy applies priority thresholds, quiet hours, per-recipient
daily interruption budgets, deduplication cooldowns, expiry, and acknowledgement state. It
chooses interrupt, digest, or suppress; it never sends the message. API endpoints create, list,
and acknowledge notifications. Delivery adapters and credentialed channels are absent.

## Integration boundary

`chief.integrations` is a provider-neutral library boundary rather than an active connector
fleet. A connector declares a manifest, capabilities, exact read/write scopes, health, and
rate-limit metadata. The registry requires:

1. explicit registration;
2. an exact declared scope with the correct access type;
3. an active principal-bound consent grant;
4. matching sync cursors for reads;
5. unexpired idempotency metadata for writes;
6. evidence whose connector/scope and content digest verify.

Registration never grants consent. Revocation records are retained. Connector outputs outside
their authorized boundary are rejected.

The registry and consent grants are currently in process, are not mounted in the application,
and no live GitHub, Stripe, email, calendar, CRM, support, analytics, storage, or intelligence
adapter is included. Credentials must never be placed in source or plain SQLite state.

## Voice and PWA boundary

`chief.voice` defines replaceable speech-to-text and text-to-speech protocols, audio/transcript
schemas, local/private/cloud processing policy, cooperative cancellation, and valid voice-state
transitions. There is no backend audio transport or production speech provider.

The React PWA currently supplies the user experience through browser speech APIs:

- listening starts only after an explicit push-to-talk gesture;
- a transcript is placed into the draft for review rather than sent automatically;
- spoken replies are off by default and interruptible;
- microphone audio is not retained by the UI;
- browser/OS speech processing may be local or cloud;
- microphone access requires a secure browser context and permission;
- camera access remains disabled.

The service worker caches only the static application shell and offline page. It does not cache
conversations, API responses, approvals, or telemetry. Offline mode cannot execute commands.

## Persistence and consistency

Most current durable components create their own tables in the shared `data/chief.db` file:

| Store | Durable state |
|---|---|
| Memory | records and FTS candidate index |
| Sessions | conversations, messages, pending proposals, consumption tombstones |
| Work | goals and tasks |
| Events | schedules and queued event lifecycle |
| Foresight | signals, assumptions, and KPIs |
| Runs | runs, steps, attempts, checkpoints, and run events |
| Decisions | complete decision record documents |
| Business | graph nodes and relationships |
| Notifications | notifications, attention decisions, attempts, and receipts |
| Audit | immutable tool/approval events and hash links |

Stores use independent short-lived SQLite connections and bounded busy timeouts. Key stores use
WAL; state transitions that must claim or consume exactly once use immediate transactions.
This design is intentionally simple for one owner on one host.

There is no centralized migration framework, encrypted-at-rest database layer, automated
backup/restore workflow, cross-domain transaction coordinator, or supported multi-process
write topology. Before distributed workers or multi-tenant use, CHIEF needs versioned
migrations, restore testing, measured concurrency limits, and likely a server database.

## Audit, logging, and evaluation

Tool decisions and approval lifecycle events are stored in an append-only SQLite table guarded
against update/delete by triggers. Each row includes the preceding hash and a hash of its own
canonical payload. Integrity can be checked through `/audit/integrity`; bounded pages are
available through `/audit/events`. Records can correlate request, actor, session, run, step,
and proposal IDs when supplied.

The hash chain detects accidental corruption and unsophisticated edits. A database owner can
recompute the chain, so this is not an externally anchored or cryptographically signed ledger.
Audit coverage is also not yet universal: ordinary work, graph, decision, foresight, and
notification mutations do not all emit audit records.

HTTP requests receive correlation IDs and structured completion logs with method, path, status,
and duration. Full model traces, connector traces, metrics export, alerting, retention controls,
and founder-readable incident reconstruction remain incomplete.

`chief.evals` runs deterministic checks over supplied observations. Cases can assert tool
choice, approval requirements, forbidden actions, evidence/citation markers, memory recall, and
latency. Suite thresholds cover weighted score, case pass rate, critical failures, and check
rates. The runner does not call models, tools, or external systems; a separate harness must
capture representative observations.

## Lifecycle guarantees and non-guarantees

Implemented guarantees at the current boundary:

- unregistered tools and undeclared connector scopes do not execute;
- direct callers cannot mark their own sensitive tool request approved;
- chat approvals are exact, expiring, actor/session-bound, and single-use;
- the global kill switch pauses execution entry points;
- a leased run step cannot report completion after losing its claim;
- verification-required steps do not complete without verified output;
- session and workflow state survive a normal process restart;
- audit-chain continuity can be checked;
- the PWA does not cache private API data.

Not guaranteed in pre-alpha:

- safe unattended 24/7 operation;
- behavior under hostile public-network exposure;
- multi-user isolation or distributed-worker correctness;
- protection of SQLite state after host compromise;
- exactly-once effects in external systems without a connector-specific idempotency guarantee;
- end-to-end rollback of arbitrary commands;
- comprehensive prompt-injection defense for future untrusted connectors;
- calibrated forecasts or evidence correctness without live source validation;
- production voice latency, accessibility, or physical-device support;
- disaster recovery, schema rollback, or long-term compatibility.

## Dependency direction

Domain logic should continue to depend on CHIEF-owned interfaces. Vendor-specific code belongs
at the outer adapters:

```text
UI / HTTP adapters / workers
            ↓
application orchestration and policy
            ↓
domain schemas and services
            ↓
CHIEF-owned ports
            ↑
SQLite / Ollama / future vendor adapters
```

Tools, connectors, and model adapters must not bypass the guard, consent, audit, or verification
layers merely because a vendor SDK can perform an action directly.

## Security baseline for every extension

- Keep credentials out of source control, prompts, audit metadata, and plain application data.
- Start each external integration read-only with the minimum exact scope.
- Treat websites, email, tickets, documents, connector payloads, and model output as untrusted
  data rather than executable instruction.
- Make external writes idempotent, previewable, approval-aware, and verifiable.
- Preserve request, actor, evidence, plan, run, step, approval, and result correlation.
- Define bounded time, step, cost, retry, output-size, and network budgets.
- Make cancellation and abstention successful control outcomes.
- Add adversarial and recovery evaluations before expanding autonomy.
- Never report action success until a receipt or verification supports it.

Architecture decisions live in `docs/adr/`. The prioritized path from this foundation to a
production AI co-founder is maintained in `docs/BEST_IN_CLASS_CHECKLIST.md`.
