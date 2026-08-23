# CHIEF best-in-class roadmap and external-dependency checklist

Status: landscape and repository snapshot as of 2026-08-23

CHIEF stands for **Cognitive Hub for Intelligence, Execution & Foresight**. This document
defines the engineering path from the current pre-alpha foundation to a dependable AI
co-founder. It compares what is verifiable in this repository with Trillion's public claims and
with representative primary sources from the broader assistant and agent ecosystem.

## Bottom line

CHIEF already has an unusually strong **verifiable control plane** for a pre-alpha project:
typed tools, exact approval binding, durable runs and checkpoints, scoped memory, event
scheduling, evidence-aware foresight records, a hash-chained audit log, authenticated LAN
gates, and deterministic release evaluations. Those capabilities are present in source and
tests rather than only described on a product page.

CHIEF does **not** yet equal Trillion's advertised end-to-end experience. Trillion says it is
voice-first, continuously watches six business-system categories, coordinates specialist
sub-agents, uses live Stripe/GitHub/calendar/customer/competitive data, and performs real
browser automation. CHIEF currently has contracts and durable foundations for much of that
work, but few live business connectors, no production computer-use harness, no full-duplex
streaming voice service, and no proven always-on deployment.

There is no defensible way to claim that CHIEF has surpassed Trillion overall today.
[Trillion's own site](https://www.hellotrillion.ai/) says its repository is “coming soon” and
will be released when ready. Its product and stack descriptions therefore remain advertised
claims that cannot be independently inspected, tested, benchmarked, or security-reviewed.
Conversely, absence of public Trillion code is not evidence that a capability is absent.

The most credible winning strategy is not to copy a personality demo. It is to make CHIEF the
system that can continuously observe a business, preserve provenance, make explicit
decisions, execute through least-privilege tools, prove what happened, recover from failure,
and give the founder control over every material risk.

## Evidence method and limits

- **CHIEF claims** below come from repository source, schemas, endpoints, documentation, and
  automated tests. A module or contract is not counted as a live integration unless it can
  actually communicate with the outside system.
- **Trillion claims** come only from Trillion's public site and prompt pages. The comparison
  deliberately labels those as public claims, not verified implementation facts.
- **Landscape conclusions** use official vendor documentation, official project
  documentation, specifications, or original research papers. This is a representative
  engineering survey, not a claim to enumerate every assistant project in existence.
- Product availability, pricing, model quality, and vendor terms can change. Recheck every
  external dependency immediately before production adoption or purchase.

## What the 2026 landscape makes table stakes

| Direction | Primary evidence | Implication for CHIEF |
|---|---|---|
| Long-running agents produce finished work across apps and files, not just answers. | [OpenAI describes ChatGPT Work](https://openai.com/index/chatgpt-for-your-most-ambitious-work/) as taking action across apps/files, decomposing goals, and staying with projects for hours. | Treat a conversation as an interface to durable, inspectable work; never make chat history the only source of run state. |
| A personal assistant is becoming system-wide, context-aware, multimodal, and able to act across apps. | [Apple's Siri AI announcement](https://www.apple.com/newsroom/2026/06/apple-introduces-siri-ai-a-profoundly-more-capable-and-personal-assistant/) describes personal context, onscreen awareness, app actions, on-device models, and Private Cloud Compute. | Add screen/vision and app actions behind explicit capture indicators, data minimization, and per-app permission scopes. |
| Computer use is moving into general multimodal models. | [Google's Gemini 3.5 Flash computer-use announcement](https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-computer-use-gemini-3-5-flash/) describes agents that see, reason, and act across browser, mobile, and desktop environments. | Build an isolated computer-use harness with screenshot/action receipts, domain allowlists, and confirmation at consequential boundaries. |
| Voice quality now means low-latency, interruption-friendly, tool-using conversation. | [OpenAI's 2026 realtime voice models](https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/) cover live transcription, reasoning, translation, and action; [GPT-Live](https://openai.com/index/introducing-gpt-live/) describes full-duplex interaction and delegation to frontier reasoning. | Evolve browser push-to-talk into a replaceable streaming pipeline with VAD, barge-in, cancellation, local/cloud routing, and visible privacy state. |
| Voice pipelines remain easier to secure and replace when wake word, STT, intent, and TTS are separate stages. | [Home Assistant's Assist pipeline](https://developers.home-assistant.io/docs/voice/pipelines/) specifies wake-word, STT, intent, and TTS stages over evented WebSockets. | Keep provider boundaries already present in `chief.voice`; add a transport and runtime without coupling audio capture to one vendor. |
| Agent durability requires checkpoints, idempotency, pause/resume, and recovery. | [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) connects checkpoints to human-in-the-loop control, memory, replay, and fault tolerance; its [functional API guidance](https://docs.langchain.com/oss/python/langgraph/functional-api) emphasizes deterministic replay and idempotent side effects. | Preserve CHIEF's durable run engine and make every external write an idempotent step with a verification receipt. |
| Long-term memory is a lifecycle and evaluation problem, not merely vector search. | [Letta's production-memory evaluation](https://www.letta.com/blog/evaluating-memory-in-production-agents/) separates memory use from memory generation; the [Mem0 paper](https://arxiv.org/abs/2504.19413) evaluates extraction, consolidation, retrieval, temporal questions, graph memory, latency, and cost. | Add memory-generation tests, consolidation, conflict resolution, forgetting, and graph-assisted retrieval while retaining scope, sensitivity, temporal validity, and provenance. |
| Typed tools, standardized context exchange, and narrowly bound authorization are converging. | The [OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses) exposes typed function, shell, computer, file, web, and MCP tool events. The [MCP specification](https://modelcontextprotocol.io/specification/2025-06-18/index) defines resources/prompts/tools and explicit consent, while [MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization) requires OAuth resource indicators and audience-bound tokens. | Implement MCP as an adapter to CHIEF's guard—not a bypass—and preserve exact tool/argument/user approval binding. |
| Local models can support typed tool use, structured outputs, and multimodal processing. | [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling) and [structured outputs](https://docs.ollama.com/capabilities/structured-outputs) document schema-driven local inference, including vision examples. | Keep local inference as a privacy and continuity tier; benchmark rather than assuming local or cloud is always best. |
| Multi-agent systems need explicit topology, termination, and observability rather than role-play alone. | [Microsoft AutoGen](https://microsoft.github.io/autogen/stable/index.html) separates conversational agents from an event-driven core, tools, MCP, executors, and distributed runtimes; its [team guidance](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html) warns that teams require more scaffolding and should be reserved for genuinely complex work. | Start with a bounded single-agent plan; fan out specialists only when an eval shows a benefit, isolate their context, and record the synthesis. |
| More autonomy raises the importance of human control, transparency, privacy, and layered prompt-injection defenses. | [Anthropic's trustworthy-agents guidance](https://www.anthropic.com/research/trustworthy-agents) identifies model, harness, tools, and environment as separate safety layers and describes permission choices for actions. | Keep sensitive-action gates, add taint/provenance tracking for untrusted content, and make “pause or abstain” a successful outcome when intent or evidence is insufficient. |

## Current CHIEF capabilities verified in this repository

| Area | Implemented now | Important boundary |
|---|---|---|
| API and UI | FastAPI core, React command center, mobile-responsive installable PWA, health/readiness/system/dashboard endpoints. | Still pre-alpha; not approved for public internet exposure or a large multi-user deployment. |
| Model routing | Provider-independent contracts; capability, privacy, and cost requirements; ordered fallback; latency records; failure threshold and cooldown circuit breaker. Ollama is the implemented local adapter. | A routing contract is not a competitive model portfolio. Cloud, vision, audio, and specialized reasoning adapters are not yet wired into production. |
| Memory | SQLite persistence; semantic, episodic, decision, and procedural types; personal/organization/project/session scopes; sensitivity, temporal validity, expiry, provenance, corrections, forgetting, and FTS-based hybrid candidate retrieval. | No production-grade embedding reranker, learned consolidation, automatic contradiction resolution, or memory-quality benchmark fed by real use. |
| Tool system | Machine-readable input schemas, risk/side-effect/idempotency/timeout metadata, deny-by-default registry, bounded plans, safe filesystem/system/process tools, and a tightly constrained PowerShell boundary. | The catalog is mostly local. It lacks live business APIs, browser/computer use, and hardened sandboxed code execution. |
| Permissions | Safe/controlled/sensitive policy; exact tool-and-argument digest; actor-bound, expiring, single-use plan grants; persistent chat proposals; no caller-controlled approval flag; global execution kill switch. | Plan approval issuance is not yet a complete user-facing persistent workflow, and one API token is not full identity/RBAC/delegation. |
| Durable execution | SQLite runs, steps, attempts, checkpoints, digests, idempotency keys, leases, retries, cancellation, restart recovery, verification gates, and event history. | Only a small safe action-handler set is registered; no continuously supervised worker deployment has been proven. |
| Work management | Persistent goals and tasks with priority, due dates, blockers, status, and deterministic executive briefing. | Not yet synchronized with external project, calendar, or issue-tracking systems. |
| Events and schedules | Durable once/interval/daily schedules, time zones, deduplicated events, leases, retries, failure tracking, and dead letters. | Scheduling currently needs an operator/service to keep ticking; live webhook and change-feed ingestion is not complete. |
| Foresight | Typed risks, opportunities, anomalies, trends, assumptions, and KPIs; evidence requirement for high-confidence signals; transparent impact/urgency/confidence/freshness/reversibility scoring. | These are decision-quality primitives, not a validated forecasting engine. Live evidence ingestion, calibrated forecasts, and outcome backtesting are missing. |
| Decisions | Persistent decision records and APIs with options, weighted criteria, evidence, assumptions, risks, provenance, deterministic scoring, and weight-override sensitivity inspection. | It needs a full UI workbench, recommendation calibration, scheduled decision reviews, and outcome learning. |
| Business context | Typed graph entities/relationships with owner, sensitivity, confidence, temporal validity, provenance, and bounded traversal. | It is not yet populated from real company systems or merged with retrieval and planning in the main agent loop. |
| Integrations | Connector, scope, consent, evidence, cursor, health, rate-limit, and idempotency contracts plus a guarded registry. | These are contracts and tests, not live Stripe, GitHub, email, calendar, CRM, support, analytics, or market-data connectors. |
| Audit and observability | Append-only SQLite tool audit, SHA-256 chain, integrity verification, pagination, request/actor/session/run/step/proposal correlation, and redacted argument/result digests. | A local hash chain detects corruption but is not an externally anchored tamper-proof ledger; model traces and service-level metrics are limited. |
| Networking and access | Loopback-first defaults, explicit private-LAN opt-in, public-client refusal, minimum-length bearer token, trusted-host/origin checks, security headers, bounded request sizes, remote rate limiting, and session-only PWA token pairing that refuses plain remote HTTP. | No TLS termination, device enrollment, token rotation, multi-user identity, secure internet relay, or proven hostile-network deployment. |
| Voice | Backend provider protocols, privacy policy/state machine/cancellation, and opt-in browser push-to-talk plus browser text-to-speech. The UI does not auto-listen or retain audio. | No wake word, backend streaming audio, full-duplex conversation, robust barge-in, production STT/TTS adapters, or physical-device latency/accessibility qualification. |
| Attention | Durable notifications, deduplication, cooldowns, quiet hours, finite interruption budgets, digest-versus-interrupt policy, delivery attempts, and receipts. | No real push/email/SMS/desktop dispatcher or credentialed delivery channel is connected. |
| Evaluations | Deterministic offline cases for tool choice, approvals, forbidden actions, evidence/citation markers, memory recall, latency, and release thresholds. Critical approval/forbidden-action failures can block release. | The framework still needs a representative founder workload, adversarial prompt-injection suites, model/provider regression data, and CI trend reporting. |

## Competitive gap matrix: CHIEF versus Trillion's public claims

“Ahead” means CHIEF has inspectable implementation in this repository where Trillion has not
published inspectable evidence. It does **not** assert that a private Trillion implementation
lacks that feature.

| Capability | CHIEF now | Trillion public claim | Evidence-based assessment |
|---|---|---|---|
| Inspectability | Private CHIEF repository is available to its owner with source and tests. | Site says open-source repository is still “coming soon.” | **CHIEF ahead in verifiability today.** No full product-quality comparison is possible until Trillion code or a testable deployment is available. |
| Safety gates | Typed risk classes, exact approvals, kill switch, audit context, bounded execution. | The [starter prompt](https://hellotrillion.ai/p/start-here) mentions safety rails; the public site does not expose their implementation. | **CHIEF ahead in publicly verifiable control design.** Trillion's actual safety cannot be scored. |
| Durable reliability | Runs, attempts, checkpoints, leases, retries, cancellation, idempotency, verification status. | Public site says “never sleeps” but gives no inspectable recovery semantics. | **CHIEF ahead in verifiable runtime foundations;** CHIEF still needs an always-on supervisor and crash/load qualification. |
| Audit and evals | Hash-chained local audit and deterministic release-gate framework. | No public audit/evaluation implementation is available. | **CHIEF ahead in public evidence,** but external audit anchoring and production eval coverage remain gaps. |
| Live business awareness | Connector/evidence contracts, goals, graph, KPIs, signals; few live data adapters. | Claims 24/7 live access to revenue, code, customers, data, communications, and competitive intelligence. | **Trillion ahead by advertised end-to-end capability.** This is CHIEF's highest product-value gap. |
| Voice | Opt-in browser push-to-talk/TTS and backend contracts/state machine. | Claims streaming Deepgram STT, ElevenLabs TTS, local wake word, and voice-first interaction. | **Trillion ahead by public claim.** CHIEF has the safer component boundary but not the comparable experience. |
| Proactivity | Durable schedules/events, foresight and attention-policy primitives. | Claims continuous monitoring and proactive alerts. | **Foundation close; product gap large.** CHIEF needs continuous ingestion, supervised workers, calibrated notification delivery, and real business data. |
| Browser/desktop action | Guarded local read/system tools and gated commands; no visual computer-use loop. | Claims “real browser automation for research.” | **Trillion ahead by public claim.** CHIEF should add isolated browser use before general desktop control. |
| Specialist agents | Bounded plan executor and durable action runtime; no polished specialist team product. | Claims engineering, support, testing, design, research, social, retention, and agent-creation specialists. | **Trillion ahead in advertised breadth.** CHIEF should add specialists only with role-specific tools, data boundaries, evals, and termination rules. |
| Memory and business model | Scoped temporal memory plus typed business graph, decisions, assumptions, KPIs, and provenance. | Claims persistent state in Postgres and live business knowledge; internal model is not inspectable. | **CHIEF has a stronger verifiable semantic foundation;** Trillion may have a stronger populated live context. |
| Mobile/networking | Installable responsive PWA and authenticated private-LAN path. | Trillion publishes a [mobile-PWA build prompt](https://www.hellotrillion.ai/p/mobile-pwa); its site says a Tauri/Next desktop shell is planned. | **Roughly comparable at public-shell level;** neither can be declared production-secure from public evidence. |
| Model independence | CHIEF-owned provider boundary and privacy-aware router, presently with Ollama. | Site names Claude Sonnet 4.6 as the reasoning model. | **CHIEF ahead architecturally, behind operationally.** It needs multiple real adapters and routing evals. |
| Data scale | SQLite local-first single-host design. | Site claims Postgres state and async Python/FastAPI/WebSockets. | **Trillion ahead by advertised scale architecture.** SQLite is appropriate for CHIEF's current single-owner milestone, not a distributed fleet. |

## Prioritized engineering roadmap

### P0 — make CHIEF a dependable co-founder for one owner

These items close the largest gap between a strong foundation and a system that creates daily
business value. Sensitive writes remain approval-gated throughout.

- [ ] **Ship the evidence plane.** Build read-only GitHub, Stripe, calendar, email, support,
  analytics/database, and web/competitive-intelligence adapters using the existing connector,
  consent, cursor, rate-limit, health, and evidence contracts. Normalize timestamps, source
  IDs, freshness, sensitivity, and content digests. Never let prose detached from a source
  become a high-confidence business fact.
- [ ] **Run continuously and recover cleanly.** Package scheduler, event, notification, and run
  workers under a supervised Windows service or tray application. Add graceful shutdown,
  lease recovery, bounded backoff, disk-space checks, clock-skew tests, dead-letter review,
  and operator-visible degraded modes.
- [ ] **Unify the co-founder loop.** Connect model tool selection to schema validation, guarded
  planning, durable runs, verification, audit, and user-facing approvals. A chat response must
  never be the sole record that an action happened.
- [ ] **Complete the persistent approval experience.** Present exact action, target, data to be
  shared, expected side effects, expiry, and rollback/verification plan. Support approve,
  reject, narrow scope, revoke, and emergency stop. Persist grants atomically and make all
  grants single-use unless an owner explicitly creates a bounded standing policy.
- [ ] **Turn business primitives into the daily briefing.** Populate and reconcile the business
  graph, goals, tasks, decisions, assumptions, KPIs, signals, evidence, and run outcomes. Every
  priority must state “why now,” evidence freshness, confidence, owner, next action, and what
  CHIEF could not verify.
- [ ] **Add production model adapters and measured routing.** Implement at least one strong
  cloud reasoner, one fast/cheap cloud model, and the local Ollama tier. Route by capability,
  sensitivity, latency target, cost ceiling, task scorecard, circuit state, and owner policy;
  record route decisions without storing secret content.
- [ ] **Build streaming voice without weakening consent.** Add a WebSocket audio transport,
  local VAD, optional local wake word, streaming STT, streaming/cancellable TTS, barge-in,
  transcript review for sensitive actions, device audio-state indicators, and a text fallback.
- [ ] **Harden secrets, identity, and data at rest.** Use the OS credential vault or a dedicated
  encrypted secret store; add token rotation and revocation, per-user/device identity, scoped
  sessions, encrypted backups, and a documented recovery procedure. Do not put OAuth refresh
  tokens or vendor keys in SQLite plaintext or source control.
- [ ] **Threat-model untrusted evidence.** Mark email, tickets, websites, documents, tool
  descriptions, and model output as untrusted; separate data from instructions; constrain tool
  reach; prevent token passthrough; add egress/domain policies; and test direct/indirect prompt
  injection, confused-deputy, approval-replay, and cross-session attacks.
- [ ] **Create a founder-workload release gate.** Record representative, privacy-scrubbed
  read-only and write workflows; measure task success, evidence correctness, approval
  correctness, prohibited-action rate, memory precision, recovery, latency, and cost. Require
  zero unauthorized consequential actions before expanding autonomy.
- [ ] **Prove backup, restore, and upgrade paths.** Version every schema migration; take atomic
  backups; verify restore on a clean machine; test rollback/forward compatibility; document
  retention and secure deletion; and surface integrity failures in readiness.

### P1 — add multimodal execution, decision leverage, and secure mobility

- [ ] Add an isolated Playwright/browser-computer worker with ephemeral profiles, screenshot
  receipts, download quarantine, domain allowlists, network egress controls, action budgets,
  and mandatory confirmation before login, purchase, publish, submit, delete, or data sharing.
- [ ] Add screen/image/document understanding with an unmistakable capture indicator, explicit
  per-session consent, redaction, local preprocessing where practical, and automatic expiry of
  raw screenshots.
- [ ] Integrate decisions into the UI: option comparison, evidence/assumption/risk review,
  sensitivity analysis, dissenting analysis, decision journal, scheduled review date, expected
  outcome, and later outcome-versus-forecast scoring.
- [ ] Add scenario planning and calibrated foresight: base/upside/downside cases, probability
  ranges, leading indicators, trigger thresholds, reversible experiments, Brier/calibration
  tracking, and automatic retirement of stale forecasts.
- [ ] Implement specialist agents only where isolated context and tools improve an eval:
  finance/revenue, engineering/release, customer health, research/competitive, and operations.
  Give each a typed output, source boundary, finite budget, termination condition, and central
  synthesis that preserves disagreement and abstention.
- [ ] Connect real attention channels (web push, desktop, email, optional SMS) to the existing
  quiet-hours, deduplication, cooldown, interruption-budget, digest, and receipt model. Make all
  outbound content previewable and channel-revocable.
- [ ] Provide secure mobile access through HTTPS, device enrollment, short-lived tokens,
  remote revoke, biometric/device-lock handoff where supported, and a private tunnel or relay.
  Never expose the development server directly to the public internet.
- [ ] Improve memory with embedding and graph retrieval, evidence-linked consolidation,
  contradiction sets, memory-generation review, source deletion propagation, configurable
  retention, portable export, and production memory evaluations.
- [ ] Add unified traces and service objectives across request, model, connector, plan, run,
  step, evidence, approval, and notification IDs; export privacy-safe metrics and provide a
  founder-readable incident timeline.
- [ ] Add connector write paths one at a time, beginning with reversible drafts. Each write must
  have idempotency, least-privilege OAuth scope, preview, approval rule, verification receipt,
  compensation/rollback where possible, and a dedicated adversarial test suite.

### P2 — scale, personalize, and build a defensible intelligence advantage

- [ ] Add Postgres/object storage and multi-worker coordination only after single-host limits
  are measured; preserve the local single-owner mode as a first-class deployment.
- [ ] Build a policy-controlled skill/plugin system with signed versions, provenance,
  capability manifests, sandboxed installation, update review, revocation, and per-skill evals.
- [ ] Add an owner-approved advisory board whose seats cite a bounded doctrine corpus, abstain
  outside that corpus, reason independently, expose blind spots, and cannot manufacture
  unanimity.
- [ ] Add privacy-preserving personalization for tone, cadence, attention thresholds, voice,
  recurring workflows, and preferred decision style. Keep identity/preferences editable,
  exportable, and forgettable.
- [ ] Add learned workflow suggestions only after opt-in observation, counterfactual evaluation,
  rollback, and conservative confidence thresholds; never let self-modification silently
  increase permissions.
- [ ] Evaluate on-device and private-cloud inference tiers for voice, vision, embeddings, and
  sensitive summarization; route with measured quality, privacy, latency, energy, and cost.
- [ ] Commission repeatable competitor benchmarks against a testable Trillion release,
  ChatGPT Work, Siri AI, Gemini computer use, and relevant local assistants using the same
  founder workflows, evidence set, hardware class, and scoring rubric.

## Definition of “best” — measurable release gates

These targets should be finalized against the owner's real workflow and risk tolerance. They
are more meaningful than a subjective “JARVIS-like” label.

- [ ] **Authority:** zero unauthorized consequential actions in release, adversarial, and
  recovery tests; 100% of sensitive actions have a valid exact approval or standing policy.
- [ ] **Truthfulness:** every material business claim is linked to retrievable evidence,
  freshness, and confidence; CHIEF visibly abstains when evidence is insufficient or conflicts.
- [ ] **Execution:** at least 95% end-to-end success on the accepted representative workflow
  suite, with all external writes idempotent and independently verified.
- [ ] **Recovery:** crash/restart, duplicate-delivery, expired-lease, network-partition, provider
  outage, and disk-pressure exercises finish without lost approvals, duplicate side effects, or
  false success claims.
- [ ] **Foresight:** forecasts are scored after resolution; calibration and useful-signal rate
  improve over time without increasing alert volume beyond the owner's attention budget.
- [ ] **Memory:** evaluated retrieval precision/recall, temporal correctness, contradiction
  handling, deletion propagation, and no cross-scope restricted-memory leakage.
- [ ] **Voice:** physical-device targets for first-audio latency, interruption response,
  transcription accuracy, noisy-room recovery, accessibility, and a 100% reliable visible
  listening state.
- [ ] **Security:** threat model, dependency review, secret scan, prompt-injection suite,
  authorization tests, backup/restore drill, and independent penetration test pass before
  exposing sensitive business accounts or remote internet access.
- [ ] **Operations:** service-level objectives, cost ceilings, model/provider failover, capacity
  limits, data retention, incident response, and kill-switch drills are documented and tested.

## Checklist that requires human authority or external resources

The repository cannot safely decide or complete the following items on its own. Nothing here
should be guessed, purchased, consented to, or enabled by an agent without the responsible
human.

### Owner decisions and policy

- [ ] Define the company mission, current strategy, goals, KPIs, planning horizon, and what
  “best co-founder” means for the actual business.
- [ ] Set risk appetite, autonomy tiers, spending limits, approval thresholds, permitted
  recipients/domains, emergency contacts, and who may approve which actions.
- [ ] Choose the authoritative systems of record and conflict policy for revenue, customers,
  projects, calendar, communications, analytics, and competitive evidence.
- [ ] Approve privacy, retention, backup, export, deletion, employee/customer consent, and
  legal-hold policies for messages, audio, screenshots, documents, telemetry, and memories.
- [ ] Choose quiet hours, interruption budget, escalation rules, wake phrase, voice, tone,
  personality, briefing cadence, and preferred mobile/desktop experience.
- [ ] Decide local-versus-cloud routing rules by sensitivity and approve vendor data-processing
  terms, geographic restrictions, and acceptable recurring cost.
- [ ] Provide representative workflows, expected outcomes, prohibited actions, adversarial
  cases, and subjective acceptance criteria for release evaluations.

### Credentials and external accounts

- [ ] Provision narrowly scoped OAuth apps/tokens for GitHub, Stripe, calendar, email, CRM,
  support, analytics/database, project management, cloud storage, and any other selected system.
- [ ] Provision model-provider API keys and organization/project limits for each approved cloud
  reasoner, embedding, vision, transcription, synthesis, or realtime voice service.
- [ ] Configure competitive/news/search/financial data feeds and approve their licenses,
  redistribution limits, and source-quality policy.
- [ ] Create push-notification credentials (for example VAPID) and any approved email, SMS,
  mobile-push, or incident-management channel accounts.
- [ ] Provide domain, DNS, TLS certificate, private-tunnel/relay, and identity-provider access if
  CHIEF will be used outside localhost/private LAN.
- [ ] Store all secrets in the selected credential vault; never paste production credentials
  into source, issues, logs, test fixtures, prompts, or this checklist.

### OS, device, and network permissions

- [ ] Explicitly grant microphone, speech-recognition, notification, camera, screen-capture,
  accessibility/UI-automation, filesystem, and background-start permissions only for features
  the owner accepts.
- [ ] Approve installation of a Windows service/tray app and any administrator action needed
  for service startup, firewall rules, certificate trust, audio devices, or local-network access.
- [ ] Enroll and revoke each phone/tablet/desktop device; enable device lock, supported biometric
  confirmation, remote wipe/revoke, and secure key storage.
- [ ] Approve any network egress allowlist and browser-automation targets; never grant general
  authenticated browsing or unrestricted shell access merely for convenience.
- [ ] Complete physical iOS, Android, Windows, browser, microphone, speaker, sleep/wake, roaming,
  and unreliable-network tests on the devices that will actually be used.

### Purchases and infrastructure

- [ ] Approve model, voice, messaging, market-data, observability, tunnel/relay, domain, and
  storage subscriptions with hard spending alerts and cancellation owners.
- [ ] Provide an always-on, patched host with adequate encrypted storage, backup media, UPS if
  required, and supported microphone/speakers; approve a GPU or dedicated inference hardware
  only after benchmarked benefit.
- [ ] Approve a production database/managed service only when capacity, resilience, compliance,
  or multi-worker evidence justifies moving beyond SQLite/local-first operation.
- [ ] Fund independent security, accessibility, privacy, and reliability testing before high-
  impact autonomy or sensitive multi-system access.

### Legal, security, and external validation

- [ ] Obtain legal/privacy review for recording and transcribing conversations, monitoring
  employees/customers, storing personal data, automated communications, financial actions,
  terms of service, and jurisdiction-specific obligations.
- [ ] Complete an independent threat-model review, penetration test, dependency/supply-chain
  review, prompt-injection assessment, and secret-handling audit; remediate findings before
  public exposure.
- [ ] Validate financial, tax, employment, medical, legal, safety-critical, and regulated-domain
  workflows with qualified humans; CHIEF must not be treated as the final authority.
- [ ] Arrange a real Trillion build, repository, or owner-authorized test account before claiming
  benchmark superiority; score both systems on the same data, permissions, hardware, cost,
  tasks, and failure conditions.
- [ ] Run a staged pilot with reversible/read-only workflows, review false positives and misses,
  and obtain explicit owner approval before enabling each new class of external write.

## Recommended immediate sequence after the repository work

1. The owner sets systems of record, autonomy/approval policy, retention, provider budget, and
   the first ten representative founder workflows.
2. Connect GitHub and Stripe read-only, then calendar/email read-only, and prove evidence
   freshness and safe token storage.
3. Run scheduler/event/run workers as a supervised local service and qualify restart, backup,
   and degraded-mode behavior.
4. Populate the briefing from real evidence and measure whether its priorities are correct and
   useful for two weeks before adding more proactive interruptions.
5. Add one cloud reasoning route and one streaming voice route behind explicit privacy/cost
   policy, keeping the local and text paths fully functional.
6. Add browser research in an isolated, read-only profile; introduce consequential browser or
   connector writes only after the approval, verification, rollback, and adversarial gates pass.

That sequence turns CHIEF's strongest differentiator—its explicit, durable, auditable control
plane—into real co-founder value without trading away human authority.
