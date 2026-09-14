# CHIEF Intelligence Layer v1

The Intelligence Layer adds self-knowledge and governed specialist orchestration without
creating a second execution system. It is deliberately layered on top of CHIEF's existing
portfolio registry, model router, durable run engine, audit path, and human-authority model.

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

A routed specialist can now perform an analysis-only job through CHIEF's existing durable run
engine. The run receives normal idempotency, leases, retries, cancellation, verification, and
checkpoint semantics.

Specialist dispatch is protected by a server-owned authorization receipt stored separately from
the caller-controlled run payload. Merely submitting the action name through the generic `/runs`
API is insufficient. The worker refuses specialist execution unless a matching CHIEF-created
receipt exists.

At execution time CHIEF revalidates the specialist instead of trusting routing-time state. The
worker requires:

- the same owner and agent identity;
- the same task digest;
- an exact digest match for the routed name, mission, lifecycle state, authority, and budget;
- the agent to remain active, execution-enabled, kill-switch-open, funded, and inside its
  authority window; and
- an exact analysis-only payload.

Any authority, budget, lifecycle, identity, or mission change invalidates the dispatch and forces
fresh routing. This closes the route-now / revoke-later race.

The v1 handler is deliberately **analysis-only**. It routes only to a local, zero-cost model and
has no browser, shell, connector writes, or private-memory access. RECON, FORGE, and OPS receive
specialized analysis instructions, but none may claim that an external action occurred.

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
        +-- /intelligence/neuromap
        |         +-- ToolRegistry
        |         +-- ModelRouter
        |         +-- PortfolioStore
        |
        +-- /intelligence/route
        |         +-- ManagedAgent
        |         +-- AuthorityPolicy
        |         +-- BudgetEnvelope
        |
        +-- /intelligence/specialist-runs
        |         +-- deterministic route
        |         +-- server-owned dispatch receipt
        |         +-- durable RunStore / RunEngine
        |         +-- execution-time authority digest check
        |         +-- local-model analysis handler
        |
        +-- /intelligence/agent-proposals
                  +-- durable proposal ledger
                  +-- ToolRegistry validation
                  +-- Portfolio boundary validation
                  +-- explicit approval
                          +-- inert ManagedAgent
                              execution = false
                              kill switch = engaged
                              authority = disabled
                              budget = zero
```

## Safety invariants

The v1 layer must not violate these rules:

- Self-knowledge reports capability; it never grants capability.
- Routing selects among existing authority; it never creates authority.
- A routed run cannot execute without a server-owned dispatch receipt.
- Authority is revalidated at execution time, not just routing time.
- Specialist analysis uses local models only and executes zero tools.
- Agent creation and agent activation are separate operations.
- Factory approval cannot enable execution, external writes, delegation, or financial actions.
- Unknown requested tools are rejected before a proposal is stored.
- Business specialists cannot request personal systems or another business's systems.
- Zero-budget agents are not routable as execution-ready.
- Ambiguous routing fails closed.
- Existing CHIEF run verification and global kill-switch paths remain authoritative.

## What v1 deliberately does not do

The Intelligence Layer does not yet:

- let specialists execute browser, filesystem, shell, or connector tools;
- automatically grant specialist authority;
- run persistent proactive Scout/RECON jobs;
- provide a streaming voice backend;
- provide cloud-to-local task relay;
- dynamically install code or tools generated by an agent; or
- let agents create other agents without an explicit human proposal approval path.

Those capabilities should be added vertically, with corresponding evaluation and recovery gates,
rather than by weakening the existing control plane.

## Next build order

1. Add eval suites for specialist routing quality, prompt isolation, and action-boundary attacks.
2. Add a read-only evidence tool path for RECON, with source receipts and strict tool scoping.
3. Add scheduled RECON/Scout jobs using existing events, runs, notifications, and foresight.
4. Add read-only engineering inspection for FORGE before any code-write authority.
5. Upgrade voice to a provider-neutral streaming STT/VAD/TTS pipeline.
6. Add a credential-free cloud/local task envelope after local specialist execution is mature.
