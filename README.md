# CHIEF

CHIEF is the primary operator in a single attributed conversation UI. Ultron is a separate
local Ollama conversational voice that may participate but has no tools or access to CHIEF's
private memory. See [the conversation architecture](docs/architecture/chief-ultron-conversation.md).

**Cognitive Hub for Intelligence, Execution & Foresight**

CHIEF is a local-first, provider-independent AI co-founder foundation. It combines durable
business context, memory, decision support, foresight, bounded execution, and explicit human
authority in one inspectable system.

CHIEF is currently **functional pre-alpha software for one owner on one trusted machine**. Its
control plane is implemented and tested; its live integration catalog and always-on operating
experience are not yet production-complete.

## North-star acceptance test

> Chief, inspect Parcel Signals and tell me what needs my attention.

CHIEF should gather current evidence from authorized systems, distinguish evidence from
assumption, rank what matters, recommend a next action, execute only within granted authority,
verify the result, and identify anything it could not confirm.

## What is implemented

| Area | Current implementation |
|---|---|
| Core API | FastAPI application with liveness, dependency-aware readiness, system, dashboard, portfolio, chat, work, events, foresight, runs, decisions, business graph, notifications, audit, tools, and plans endpoints. |
| Local interface | Responsive React command center with an explicit blank-portfolio onboarding state, installable PWA shell, mobile layout, offline-safe static cache, opt-in browser push-to-talk, and opt-in spoken replies. |
| Models | CHIEF-owned provider contract, capability/privacy/cost/specialty requirements, ordered fallback, failure tracking, cooldown circuit breaker, a configured local Ollama adapter, and optional cloud specialist adapters (Anthropic for coding, Gemini for research, Perplexity for real-time signals, OpenAI for voice/briefing) selected by a deterministic per-message task classifier. Cloud adapters activate only when their API key is configured; CHIEF runs Ollama-only otherwise. |
| Memory | SQLite memory with types, scope, sensitivity, source provenance, confidence, temporal validity, expiry, correction/supersession, forgetting, and FTS5-assisted retrieval. |
| Sessions | Owner-scoped, restart-safe SQLite conversations and atomically consumed, expiring tool-approval proposals. |
| Tools and guard | Whitelisted typed tools with JSON-like input schemas, risk, side-effect, idempotency, and timeout metadata; filesystem roots are scoped; unknown tools fail closed. |
| Execution | Deterministic chat tool planning, bounded multi-step plans, a global execution kill switch, and sensitive actions gated by exact approval rather than a caller-supplied flag. |
| Durable runs | SQLite runs, steps, attempts, events, checkpoints, leases, cancellation, retries, idempotency keys, recovery, and verification gates. |
| Work and foresight | Persistent goals, tasks, blockers, executive briefings, signals, assumptions, KPIs, and transparent attention scoring. |
| Decisions | Persistent decision records with criteria, options, evidence, assumptions, risks, provenance, deterministic weighted scoring, and score explanations. |
| Business context | Owner-scoped temporal graph of organizations, people, products, customers, competitors, projects, opportunities, risks, documents, and typed relationships with bounded traversal. |
| Portfolio control | Empty-by-default registry for businesses, managed agents, systems, account references, authority ceilings, zero-default budgets, health heartbeats, and onboarding state. Registration never activates execution or financial writes. |
| Events | Durable once, interval, and daily schedules; time-zone-aware occurrence calculation; deduplicated events, leases, retries, and dead-letter state. |
| Attention | Persistent notifications with idempotency, deduplication, quiet hours, cooldowns, finite interruption budgets, digest/interrupt/suppress decisions, attempts, and receipts. |
| Integrations | Deny-by-default connector contracts for declared scopes, explicit consent, sync cursors, health, rate limits, evidence digests, and idempotent writes. No production connectors are registered yet. |
| Voice | Provider-neutral STT/TTS contracts, privacy policy, cancellation, and an explicit voice state machine. The current UI uses browser speech services rather than a CHIEF streaming voice backend. |
| Audit and evals | Append-only SQLite tool audit with correlation IDs, redacted argument/result digests, a SHA-256 integrity chain, pagination, and offline release evaluations with critical safety gates. |
| Network boundary | Loopback-first access, explicit protected-LAN opt-in, bearer token enforcement, trusted hosts/origins, remote rate limiting, request-size limits, correlation IDs, and browser security headers. |

## How the pieces fit

```text
React PWA / local API client
            |
  HTTP trust boundary
  - loopback or protected LAN
  - bearer actor, origin/host checks
  - rate and body limits, kill switch
            |
        FastAPI core
     /         |          \
Conversation  Operating   Execution
and memory    domains     control plane
     |         |          |
 Specialty  portfolio,    tools, plans,
model router work, graph,  runs, events,
            decisions,    approvals
            foresight
     \         |          /
       SQLite state + append-only audit
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the detailed lifecycle and trust
boundaries.

## Core principles

- **Local first:** the default model and state store remain on the owner-controlled machine.
- **Provider independent:** domain logic depends on CHIEF-owned interfaces, not vendor SDKs.
- **Evidence before assumption:** source, confidence, freshness, and validity are explicit data.
- **Least privilege:** tools and connectors are registered and scoped; undeclared access fails.
- **Human authority:** consequential actions remain approval-gated and execution can be paused.
- **Auditability:** meaningful execution decisions receive correlation and integrity metadata.
- **Cost awareness:** model routes can constrain privacy, capability, and cost tier.
- **Verification before claims:** durable actions can require verified output before completion.

## Repository layout

```text
chief/
├── apps/chief-ui/         # React/Vite command center and PWA
├── src/chief/
│   ├── core/              # FastAPI composition, sessions, limits, dashboard, chat planner
│   ├── persona_files/     # Packaged, version-controlled CHIEF and Ultron prompts
│   ├── api/               # HTTP adapters for co-founder operating domains
│   ├── models/            # Provider contracts, Ollama adapter, capability-aware router
│   ├── memory/            # Scoped temporal memory and retrieval
│   ├── tools/             # Typed local capabilities and guarded registry
│   ├── guard/             # Risk and approval policy
│   ├── agents/            # Bounded execution plans and exact approval grants
│   ├── runs/              # Durable workflow state and one-step worker engine
│   ├── events/            # Durable schedules and event queue
│   ├── work/              # Goals, tasks, blockers, and executive briefing
│   ├── foresight/         # Signals, assumptions, KPIs, and transparent ranking
│   ├── decisions/         # Decision journal and deterministic option scoring
│   ├── business/          # Owner-scoped temporal business knowledge graph
│   ├── portfolio/         # Governed businesses, managed agents, systems, and accounts
│   ├── notifications/     # Attention policy, delivery state, and receipts
│   ├── integrations/      # Consent- and evidence-aware connector contracts
│   ├── voice/             # STT/TTS contracts and privacy state machine
│   ├── audit/             # In-memory and hash-chained SQLite audit implementations
│   └── evals/             # Deterministic offline release gates
├── tests/                 # Unit, integration, security, and lifecycle tests
├── docs/                  # Architecture, evaluations, ADRs, research, and roadmap
└── data/                  # Local runtime SQLite data; ignored by Git
```

## Local development

Prerequisites:

- Python 3.12 or newer
- Ollama running locally with both configured models (`qwen3:4b` for CHIEF and
  `llama3.1:8b` for Ultron by default)
- Node.js and npm for the React command center

Install the Python package and development tools:

```powershell
python -m pip install -e ".[dev]"
```

Start the API on loopback:

```powershell
uvicorn chief.core.app:app --host 127.0.0.1 --port 8000
```

The lightweight built-in interface is available at `http://127.0.0.1:8000`. Interactive API
documentation is available at `http://127.0.0.1:8000/docs`.

Start the React command center in a second terminal:

```powershell
cd apps\chief-ui
npm install
npm run dev
```

Then open `http://127.0.0.1:5173`.

Run the verification suite:

```powershell
pytest
ruff check .
cd apps\chief-ui
npm run build
```

Runtime settings are environment variables documented in `.env.example`. CHIEF creates its
local tables in `data/chief.db` by default. Do not store credentials in that database or in
source control; a production secrets vault and encrypted backup policy are not implemented.

### Enabling cloud model specialists (optional)

CHIEF runs Ollama-only until you configure a cloud provider's API key. Each configured key
adds that specialist to the model router; unconfigured providers are simply skipped, so partial
setup (e.g. Anthropic only) is safe. Local Ollama always stays available as the fallback for
every route.

To enable one specialist (Anthropic, for coding tasks, shown here — Gemini/Perplexity/OpenAI
follow the same pattern with their own `CHIEF_*_API_KEY`/`CHIEF_*_MODEL` variables):

1. **Get a key** from the provider's console (for Anthropic: [console.anthropic.com](https://console.anthropic.com) →
   Settings → API Keys).
2. **Add it to `.env`** on the machine that actually runs CHIEF (`cp .env.example .env` if you
   don't have one yet), uncommenting the matching pair:
   ```
   CHIEF_ANTHROPIC_API_KEY=sk-ant-...your real key...
   CHIEF_ANTHROPIC_MODEL=claude-opus-5
   ```
   `.env` is gitignored (`git check-ignore -v .env` should confirm) — never commit a real key.
3. **Restart CHIEF's API process.** Providers are constructed once at import time from
   `Settings.from_env()`, so a running process won't pick up a key added after it started.
4. **Smoke-test it**, either directly against the adapter:
   ```python
   from chief.models.anthropic import AnthropicProvider
   result = AnthropicProvider(api_key="sk-ant-...").generate("Say 'CHIEF-CLAUDE-OK'.")
   print(result.content, result.provider, result.model)
   ```
   or through the running API — a message matching a coding/research/signals keyword
   (`src/chief/core/task_router.py`) should come back with `"provider"` set to the specialist
   instead of `"ollama"`:
   ```bash
   curl -X POST http://127.0.0.1:8000/chat \
     -H "Content-Type: application/json" \
     -d '{"message": "There is a bug in this function, can you debug it?"}'
   ```

## Network and authority defaults

- Remote requests are denied by default. Keep the API bound to `127.0.0.1` for normal local
  use.
- Enabling `CHIEF_ALLOW_PRIVATE_LAN_UI=true` requires a random `CHIEF_API_TOKEN` of at least
  32 bytes and exact trusted host/origin configuration.
- LAN mode does not add TLS. Use a private tunnel or trusted TLS reverse proxy; do not expose
  the development server directly to the public internet.
- `CHIEF_EXECUTION_ENABLED=false` pauses tool, plan, scheduler-tick, and run-worker execution.
- `/health` is the intentionally public liveness probe. Readiness details and all other
  endpoints require the bearer token when one is configured.
- Browser microphone use is opt-in. The UI never starts listening automatically, sends a
  transcript automatically, or caches conversations in its service worker.

## Honest pre-alpha limits

CHIEF has a strong local control plane, but it is not yet a finished autonomous co-founder:

- Ollama is the only model adapter enabled by default. Anthropic/Gemini/Perplexity/OpenAI
  adapters and specialty-based routing exist and are wired into the chat path, but each cloud
  specialist stays inactive until its own API key is configured (see "Enabling cloud model
  specialists" above); task classification is a fixed keyword map, not model-driven intent
  detection.
- Integration contracts are implemented, but live GitHub, Stripe, email, calendar, CRM,
  support, analytics, and market-data connectors still require credentials and adapters.
- Schedules and runs advance through bounded tick calls; no supervised always-on worker or
  Windows service is shipped.
- The durable run engine has only a small safe handler set. It is not a general unattended
  automation fabric.
- Sensitive chat approvals are persistent and single-use. The separate plan approval ledger
  is currently process-local and has no complete user-facing issuance workflow.
- Attributed chat streams each agent contribution as it completes. The operator can cancel a
  pending turn, and the UI reports model degradation instead of silently hiding it.
- Ultron can be silenced for one turn or until explicitly rejoined. Participation state is
  session-scoped and survives a CHIEF restart.
- Voice is push-to-talk/browser-TTS, not a full-duplex, wake-word, streaming backend.
- Notification policy and delivery records exist, but no real push, email, SMS, or desktop
  dispatcher is connected.
- The business graph, decisions, and foresight domains have APIs but limited UI integration
  and no live evidence population.
- The portfolio starts deliberately blank. Registered businesses and agents remain paused with
  zero spend and no external-write authority until a future explicit governance workflow grants
  narrower permission; registration alone cannot activate them.
- SQLite is appropriate for the current single-owner/single-host milestone, not a distributed
  multi-worker or multi-tenant deployment.
- State is not encrypted at rest, the audit chain is not externally anchored, schema migration
  and backup/restore tooling are incomplete, and no external security assessment has occurred.
- Browser/computer use, vision, encrypted secret storage, multi-user RBAC, secure internet
  relay, and production mobile qualification remain future work.

For the evidence-based competitive assessment and prioritized remaining work, see
[docs/BEST_IN_CLASS_CHECKLIST.md](docs/BEST_IN_CLASS_CHECKLIST.md). The blank portfolio hierarchy,
onboarding order, and authority contract are in
[docs/PORTFOLIO_OPERATING_MODEL.md](docs/PORTFOLIO_OPERATING_MODEL.md). For deterministic release
gates, see [docs/EVALUATION.md](docs/EVALUATION.md).

## Development rule

Build vertically and keep every milestone usable:

**Working → Reliable → Secure → Fast → Convenient → Beautiful**

Do not expand autonomous write capability until the corresponding permission, approval,
idempotency, verification, recovery, audit, and evaluation controls exist.
