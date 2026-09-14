# CHIEF Intelligence Layer v1

The Intelligence Layer adds self-knowledge and governed specialist orchestration without
creating a second execution system. It is deliberately layered on top of CHIEF's existing
portfolio registry, model router, durable run engine, scheduler, audit path, and human-authority
model.

## Implemented in v1

### Neuromap

`GET /intelligence/neuromap`

Neuromap is a machine-readable snapshot of capabilities CHIEF can actually observe at runtime:

- registered tools and their risk/approval metadata;
- configured model providers and declared capabilities;
- model circuit-breaker state;
- owner-scoped managed agents and their current authority state;
- owner-scoped registered systems;
- portfolio counts;
- global execution state; and
- mechanically derived limitations.

Neuromap does not probe credentials, infer undeclared integrations, or turn registration into
permission. Its purpose is to make capability claims evidence-based rather than prompt-based.

### Specialist routing

`POST /intelligence/route`

The specialist orchestrator is deterministic. It can select only agents that are already:

- in the requested owner and operating scope;
- active;
- execution-enabled;
- outside their kill-switch state;
- inside an unexpired authority window;
- funded with a non-zero model token budget and parallel-run budget; and
- explicitly authorized for every tool required by the request.

Routing does not activate an agent, grant a tool, change a budget, or modify authority.
Ambiguous routing fails closed instead of making an arbitrary choice.

The first routing vocabulary recognizes RECON, FORGE, and OPS naming conventions while also
scoring ordinary mission/name text. This remains deterministic and inspectable.

### Durable specialist analysis runs

`POST /intelligence/specialist-runs`

A routed specialist can perform an analysis-only job through CHIEF's existing durable run engine.
The run receives normal idempotency, leases, retries, cancellation, verification, and checkpoint
semantics.

Specialist dispatch is protected by a server-owned authorization receipt stored separately from
the caller-controlled run payload. Merely submitting the action name through the generic `/runs`
API is insufficient. The worker refuses specialist execution unless a matching CHIEF-created
receipt exists.

At execution time CHIEF revalidates the specialist instead of trusting routing-time state. The
worker requires the same owner, agent, task, lifecycle, mission, authority, and budget digest.
Any meaningful change invalidates the dispatch and forces fresh routing.

The general v1 specialist handler remains **analysis-only**. It routes only to a local, zero-cost
model and does not execute browser, shell, filesystem, or connector tools.

### RECON read-only evidence

RECON now has one explicitly registered tool: `recon.evidence`.

The capability is read-only and bounded. It can:

- inspect explicitly supplied public HTTP/HTTPS seed URLs without a search credential;
- optionally discover public pages through Brave Web Search when
  `CHIEF_BRAVE_SEARCH_API_KEY` exists in CHIEF's encrypted secret vault;
- collect bounded HTML text and source metadata;
- tolerate individual page failures without discarding the whole evidence bundle; and
- pass evidence to the local RECON model with source identifiers such as `[S1]`.

It reuses CHIEF's browser URL policy, which rejects localhost, private/local network addresses,
credential-bearing URLs, unsafe schemes, and redirects into protected address space. The
lightweight Scout reader disables active page execution by parsing fetched HTML rather than
running arbitrary page JavaScript.

External page text is always marked as untrusted evidence. RECON's system prompt explicitly
forbids following instructions contained in source material. Sources are data, not authority.

Brave search is optional. If a Scout requires discovery and the search credential is absent, the
request fails closed instead of pretending search occurred. A Scout with explicit seed URLs can
still run without that credential.

### Proactive RECON Scout jobs

One-off Scout:

`POST /intelligence/recon/scouts`

Recurring Scout schedules:

- `GET /intelligence/recon/scout-schedules`
- `POST /intelligence/recon/scout-schedules`
- `POST /intelligence/recon/scout-schedules/{id}/pause`
- `POST /intelligence/recon/scout-schedules/{id}/resume`

The default recurring schedule is 02:30 in `America/Chicago`, but callers can select another
valid IANA timezone and local time.

Recurring Scouts use CHIEF's existing Scheduler, EventStore, RunStore, RunEngine, execution kill
switch, and background runtime. The background worker now registers the same intelligence
handlers as the API process, so scheduled work can execute while the interactive UI is closed.

Scheduled Scout dispatch uses two private authorization records:

1. A private schedule registration proves the schedule was created through the governed RECON
   schedule API with a specific owner and immutable workload digest.
2. A private scheduled-run receipt proves the background scheduler created that exact durable run
   from that exact registered schedule event.

Generic `/schedules` callers therefore cannot gain RECON authority merely by copying the Scout
event name, and generic `/runs` callers cannot manufacture a valid scheduled Scout wrapper. The
worker revalidates the specialist again when the actual Scout run executes.

A Scout currently performs exactly one external capability: `recon.evidence`. It then uses a
local, zero-cost model to produce an evidence-backed result containing an executive finding,
evidence, risks/unknowns, and a recommended next action. It does not write to websites, run shell
commands, modify files, send messages, or make financial actions.

### Proposal-only Agent Factory

The Agent Factory is intentionally split into proposal and authority phases.

1. `POST /intelligence/agent-proposals` creates a durable proposal.
2. The proposal validates that requested tools really exist and referenced systems remain inside
   the specialist's business boundary.
3. `POST /intelligence/agent-proposals/{id}/approve` materializes the specialist.
4. The materialized specialist starts as `draft`, with execution disabled, its kill switch
   engaged, a zero budget, and authority disabled.
5. Requested tools and systems are recorded in the disabled authority envelope for later human
   governance. Approval of the proposal is not approval to execute.
6. `POST /intelligence/agent-proposals/{id}/reject` permanently rejects a proposal.

The proposal ID is also used as the materialized agent ID, making approval retry-safe and
preventing duplicate agent creation after an interrupted approval request.

## Architecture

```text
Authenticated CHIEF API
        |
        +-- Neuromap
        |     +-- ToolRegistry
        |     +-- ModelRouter
        |     +-- PortfolioStore
        |
        +-- Specialist route / analysis
        |     +-- deterministic route
        |     +-- private dispatch receipt
        |     +-- RunStore / RunEngine
        |     +-- execution-time authority digest check
        |
        +-- RECON evidence / Scout
        |     +-- recon.evidence
        |     |     +-- SSRF-safe public URL policy
        |     |     +-- optional Brave discovery
        |     |     +-- bounded HTML evidence
        |     |
        |     +-- one-off Scout run
        |     +-- governed recurring schedule
        |           +-- private schedule registration
        |           +-- Scheduler / EventStore
        |           +-- private scheduler-run receipt
        |           +-- background RunEngine
        |
        +-- Agent Factory
              +-- proposal ledger
              +-- explicit approval
              +-- inert ManagedAgent
```

## Safety invariants

The v1 layer must not violate these rules:

- Self-knowledge reports capability; it never grants capability.
- Routing selects among existing authority; it never creates authority.
- A routed run cannot execute without a server-owned dispatch receipt.
- Scheduled Scout names are not authorization; private schedule and scheduler-run receipts are.
- Authority is revalidated at execution time, not just routing time.
- RECON evidence is read-only and treats external content as untrusted data.
- Search discovery fails closed when its credential is unavailable.
- Specialist and Scout model synthesis is local-only and zero-cost-tier in unattended execution.
- Agent creation and agent activation are separate operations.
- Factory approval cannot enable execution, external writes, delegation, or financial actions.
- Unknown requested tools are rejected before a proposal is stored.
- Business specialists cannot request personal systems or another business's systems.
- Zero-budget agents are not routable as execution-ready.
- Ambiguous routing fails closed.
- Existing CHIEF run verification and global kill-switch paths remain authoritative.

## Required configuration for full RECON discovery

Seed-URL Scout runs require no search API key.

For web discovery, store a Brave Search API credential under:

`CHIEF_BRAVE_SEARCH_API_KEY`

The Brave adapter uses the official Web Search API endpoint and sends the credential only in the
provider's subscription-token request header. The adapter bounds result count, response size,
request duration, and accepted freshness shortcuts before evidence enters CHIEF.

Use CHIEF's existing encrypted `/secrets/{name}` API on Windows so the plaintext value is not
returned by later reads. Environment-variable resolution remains a migration fallback, not the
preferred long-term storage path.

## What v1 deliberately does not do

The Intelligence Layer still does not:

- give RECON interactive browser control or page-write authority;
- let FORGE modify repositories or execute shell commands as a specialist;
- let OPS mutate external systems;
- automatically grant specialist authority;
- provide a streaming voice backend;
- provide cloud-to-local task relay;
- dynamically install code or tools generated by an agent; or
- let agents create other agents without an explicit human proposal approval path.

Those capabilities should be added vertically with their own permission, evaluation, recovery,
and adversarial gates rather than by broadening the current read-only Scout path.

## Next build order

1. Add RECON result promotion into Foresight/notifications with deduplication and significance
   thresholds.
2. Add read-only repository and codebase inspection for FORGE before any write authority.
3. Add read-only operational evidence feeds for OPS.
4. Expand specialist isolation and prompt-injection evaluations using hostile evidence fixtures.
5. Upgrade voice to a provider-neutral streaming STT/VAD/TTS pipeline.
6. Add a credential-free cloud/local task envelope after local specialist execution is mature.
