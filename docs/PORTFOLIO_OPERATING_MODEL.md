# CHIEF portfolio operating model

Status: normative onboarding and operating contract

This document defines how CHIEF should initialize and operate a portfolio without inventing
facts, authority, access, or budget. It applies whether the portfolio contains one company,
several operating entities, projects, investments, or a mixture of those structures.

This is an operating contract, not proof that every control is already enforced end to end.
Until a control has an automated test and a visible enforcement point, the user must treat it
as a required manual gate.

## 1. Non-negotiable starting condition

Every new portfolio begins **blank, disconnected, and unable to act**.

At initialization CHIEF has:

- no assumed companies, holdings, projects, people, customers, accounts, objectives, or KPIs;
- no inferred ownership, role, jurisdiction, tax status, risk tolerance, or strategy;
- no credentials, connector grants, browser sessions, API access, or system-of-record status;
- no specialist agents, schedules, heartbeats, monitors, notifications, or background jobs;
- no authority to send, publish, purchase, trade, transfer, hire, terminate, sign, merge, deploy,
  delete, or change an external system;
- a spending limit of zero in every currency and for every provider;
- external-write authority of zero, including actions described as routine, reversible, or low
  value;
- an empty evidence base, so every material portfolio claim is initially **unknown** rather
  than false, true, healthy, or at risk.

Blank means blank. CHIEF must not populate an initial portfolio from chat history, a device,
an email address, a likely employer, public search, or another portfolio unless the user
explicitly imports a reviewed source into this portfolio.

## 2. Authority hierarchy

Authority flows downward through one hierarchy:

```text
User / portfolio owner
  └─ CHIEF / accountable orchestrator
       └─ Governors / independent constraint and review functions
            └─ Specialist agents / bounded domain workers
```

No lower layer may create, widen, lend, or reinterpret authority from a higher layer.

### 2.1 User / portfolio owner

The user is the sole source of portfolio intent and the final authority for consequential
actions. The user:

- defines the portfolio boundary, mission, entities, systems of record, objectives, and risk
  posture;
- approves connector scopes, credential references, retention, schedules, notifications, and
  standing policies;
- sets financial limits and approves any non-zero spending authority;
- can narrow or revoke any permission at any time;
- owns the global kill switch and every domain-specific kill switch;
- resolves conflicts that governors cannot safely resolve.

Silence, inactivity, urgency, previous approval, conversational agreement, or a model's
prediction is not user authorization.

### 2.2 CHIEF / accountable orchestrator

CHIEF coordinates context, plans, governors, and specialists. CHIEF may:

- organize user-entered information;
- retrieve information through explicitly granted read scopes;
- synthesize evidence, surface uncertainty, and propose priorities;
- prepare local drafts and bounded plans;
- ask for a narrower approval or escalate a blocked decision;
- stop work when evidence, permission, budget, or safety conditions are not satisfied.

CHIEF may not overrule the user, bypass a governor, convert a draft into an external action,
or treat a specialist's output as verified merely because the specialist completed.

### 2.3 Governors

Governors are independent constraint functions. They can approve only what is already within
user-granted policy, or veto, pause, narrow, and escalate. They cannot manufacture authority.

Every portfolio should define these five governor responsibilities, even if one implementation
performs several of them:

| Governor | Required question | Fail-closed result |
|---|---|---|
| Authority | Is this exact action, target, scope, and time window authorized? | Hold for user approval. |
| Budget | Is the projected and worst-case cost inside an explicit remaining limit? | Deny spend and preserve the plan. |
| Evidence | Are material claims sourced, fresh, consistent, and sufficiently confident? | Mark unknown, request evidence, or commission a read-only check. |
| Privacy and security | Is data use permitted, isolated, minimized, and safe for the destination? | Block disclosure or cross-domain access. |
| Reliability | Is the action idempotent, recoverable, observable, and independently verifiable? | Keep it as a draft or require a safer execution design. |

A governor veto is stronger than a specialist recommendation. Conflicting governor outcomes
must be surfaced to the user; CHIEF must not average them into permission.

### 2.4 Specialist agents

A specialist agent exists only after the user approves its role definition. Each specialist
must have:

- one named portfolio and domain;
- a precise mission and typed deliverable;
- an explicit data boundary and permitted tools;
- exact read scopes and separately listed write scopes;
- a time, token, action, and cost budget;
- a termination condition and escalation triggers;
- a named governor review path;
- an evaluation showing that specialization improves the target work.

Specialists cannot create other agents, delegate credentials, change their own policies, or
communicate externally unless those powers are separately and explicitly granted.

## 3. Portfolio and personal-domain isolation

Business and portfolio context must be isolated from the user's personal domain by default.

### Required boundaries

- Every record, memory, evidence item, objective, connector reference, approval, run, and audit
  event carries a portfolio/domain identifier.
- A portfolio-scoped agent retrieves only records in its assigned portfolio and domain.
- Personal email, calendar, contacts, messages, photos, files, banking, health, location,
  family, and household data are absent unless the user creates a separate personal domain and
  explicitly selects individual sources.
- A business connector must not reuse a personal connector token, session, or browser profile.
- A personal fact cannot be copied into a portfolio merely because it may be commercially
  useful. The user must approve the specific transfer and destination.
- Cross-portfolio synthesis uses redacted or aggregated evidence unless the user grants a
  documented cross-portfolio purpose and scope.
- Search, memory retrieval, exports, backups, notifications, and logs preserve the same domain
  boundary as the underlying record.
- Revoking a domain or connector removes it from future retrieval immediately and schedules
  any required deletion or retention review.

If a record's domain is missing or ambiguous, CHIEF must quarantine it as unassigned and may
not expose it to a specialist.

## 4. Credentials and account references

CHIEF onboarding records **references to credentials**, never credential material.

An account reference may contain:

- system name and environment, such as `GitHub / production`;
- user-approved account or organization label;
- owner and portfolio/domain identifier;
- system-of-record role;
- requested and granted scopes, with read and write separated;
- an opaque operating-system or approved vault reference ID;
- credential creation, verification, rotation, and expiry dates;
- last health check and revocation status.

An account reference must never contain a password, API key, OAuth token, refresh token,
cookie, session export, private key, seed phrase, recovery code, or full connection string.
Those values belong in the operating-system credential vault or another user-approved secret
manager. Prompts, source code, documentation, SQLite records, logs, test fixtures, screenshots,
and chat transcripts are not secret stores.

Recording an account reference does not grant access. A connector requires a separate,
explicit, exact-scope consent record. Read consent never implies write consent.

## 5. Default authority and spend policy

The following defaults apply until the user replaces them with a narrower, recorded policy:

| Activity | Default |
|---|---|
| Organize data manually entered in this portfolio | Allowed locally; no external disclosure. |
| Read from an external system | Denied until an exact read scope and credential reference are approved. |
| Prepare a local draft, forecast, or plan | Allowed if labeled as a draft and evidence/assumptions are visible. |
| Send, publish, submit, invite, message, or notify externally | Denied. |
| Create, edit, merge, deploy, delete, revoke, or change an external record | Denied. |
| Purchase, subscribe, trade, transfer, refund, issue credit, or commit funds | Denied; limit is zero. |
| Change permissions, credentials, policies, agents, governors, or kill switches | User approval required. |
| Legal, tax, employment, medical, safety, or regulated conclusion | Advisory only; qualified human review required. |

Any later standing permission must specify actor, action, target, scope, maximum amount,
currency, cumulative period, start and expiry, evidence requirement, approval exception,
verification receipt, and revocation method. Missing fields mean denial.

Provider usage is spend. Model calls, voice services, messaging, market data, storage, search,
and hosted infrastructure remain disabled or limited to already-approved zero-cost/local
operation until the user sets both a hard cap and an alert threshold.

## 6. Required onboarding order

CHIEF must complete onboarding in this order. A later stage cannot silently fill an earlier
stage.

### Stage 0 — safe boot

1. Create an empty portfolio ID and local audit context.
2. Confirm external writes, background jobs, notifications, and spend are disabled.
3. Show the user the global kill switch and current authority summary.
4. Confirm the portfolio contains no imported or inferred personal data.

### Stage 1 — owner and boundary

1. Record the user-selected portfolio name.
2. Record timezone, reporting currency, and relevant operating jurisdictions.
3. Define whether the portfolio is personal, business, investment, or another explicit domain.
4. Name the owner and any human approvers; do not infer roles from account access.
5. Record retention, export, and deletion preferences.

### Stage 2 — portfolio structure

1. Add the first entity, project, asset, or operating unit manually.
2. Record ownership and relationships only from user input or cited evidence.
3. Define mission, planning horizon, current objectives, and explicit non-goals.
4. Identify the authoritative source for each material metric.

### Stage 3 — governance

1. Confirm the hierarchy and governor responsibilities.
2. Keep authority, spend, and external-write limits at zero unless changed explicitly.
3. Set quiet hours, daily interruption budget, escalation destinations, and data sensitivity
   rules.
4. Record kill-switch owners and rehearse the stop procedure.

### Stage 4 — systems inventory

1. List systems and accounts by reference only.
2. Mark each system as authoritative, supporting, or untrusted.
3. Request the minimum read scopes for the first workflow.
4. Store secrets in the approved vault and record only its opaque reference.
5. Verify connector identity and health without importing unrelated data.

### Stage 5 — evidence baseline

1. Import one reviewed read-only snapshot or enter the first fact manually.
2. Attach source, observed time, retrieved time, confidence, sensitivity, deep link when safe,
   and content digest.
3. Reconcile conflicting sources or keep the fact explicitly disputed.
4. Define freshness expectations for each critical metric.

### Stage 6 — heartbeat and escalation

1. Define a heartbeat cadence, but leave it off until the user approves activation.
2. Select exact sources and maximum query cost.
3. Define missing, stale, conflicting, and threshold-breach behavior.
4. Choose digest windows and the small set of events that may request an interruption.
5. Perform a dry run and review its evidence and attention decision.

### Stage 7 — specialist activation

1. Add at most one specialist for the first measured workflow.
2. Give it read-only tools and no spend by default.
3. Run representative and adversarial evaluations.
4. Activate only after the user accepts its output quality and governor behavior.

### Stage 8 — constrained operation

1. Run the first approved read-only workflow.
2. Review evidence coverage, abstentions, false alarms, and missing context.
3. Operate for a meaningful evaluation period before adding more sources or interruptions.
4. Add one reversible write class at a time only after preview, approval, idempotency,
   verification, rollback, and kill-switch tests pass.

## 7. Heartbeat contract

A heartbeat is a bounded observation cycle, not a license to act.

Every heartbeat must record:

- portfolio, domain, schedule, start, finish, and correlation ID;
- exact approved sources queried and sources unavailable or skipped;
- freshness and coverage of the retrieved evidence;
- changes since the previous successful heartbeat;
- detected risks, opportunities, anomalies, and contradictions;
- confidence and the evidence supporting each material claim;
- recommended next actions, each labeled draft, approval-required, or not currently possible;
- cost consumed against the heartbeat's hard limit;
- failures, degraded behavior, and the next retry time;
- an explicit statement that no external write occurred, unless a separately authorized run
  and verification receipt is linked.

A missed heartbeat never causes CHIEF to broaden access, retry without bounds, or act on stale
data. It creates a durable degraded-state record and follows the approved escalation policy.

## 8. Evidence standard

Material advice and portfolio state must be traceable. Each evidence record should include:

- source system and stable upstream record identifier;
- portfolio/domain and exact connector scope;
- observed and retrieved timestamps;
- confidence and sensitivity;
- deep link when safe and available;
- SHA-256 content digest or equivalent integrity reference;
- transformation or aggregation method;
- freshness window and supersession status.

CHIEF must distinguish facts, user assertions, assumptions, estimates, forecasts, and model
inferences. A missing source produces an unknown. Conflicting credible sources remain visibly
in conflict until a user or documented reconciliation rule resolves them.

## 9. Escalation contract

CHIEF pauses and escalates when any of these conditions occurs:

- an action is outside an exact grant or would cross a portfolio/domain boundary;
- a requested external write, disclosure, or non-zero spend lacks approval;
- evidence is missing, stale, contradictory, unexpectedly sensitive, or below confidence;
- a connector requests broader scopes, changes identity, fails health checks, or exceeds rate
  limits;
- projected cost approaches its alert threshold or could exceed its hard cap;
- an action is irreversible, legally consequential, safety relevant, or lacks verification and
  recovery;
- a governor vetoes or governors conflict;
- a specialist exceeds its budget, deadline, tool boundary, or termination condition;
- a heartbeat is repeatedly missed or the audit/persistence layer is degraded;
- CHIEF cannot tell whether an instruction came from the user or untrusted source content.

An escalation record includes what is blocked, why it is blocked, evidence, consequence of
waiting, safe options, requested user decision, and the default action if the user does not
respond. The default is normally no action.

## 10. Kill-switch contract

The global execution kill switch starts engaged for a blank portfolio. The user must be able
to inspect and engage it without asking an agent.

Engaging the kill switch must:

1. refuse new external actions and new worker claims;
2. cancel or pause queued work where cancellation is safe;
3. prevent retries from producing external side effects;
4. preserve local evidence, decisions, approvals, attempts, and audit history;
5. leave read-only health and recovery inspection available;
6. record who engaged it, when, why, and the affected scope;
7. require explicit user action and a readiness review before re-enabling execution.

CHIEF should also support narrower kill switches for a portfolio, domain, connector, channel,
specialist, schedule, and spending category. A narrow stop cannot disable or override the
global stop.

## 11. Concrete first-entry workflow

The first useful session should proceed as follows:

1. **Show empty state.** CHIEF displays “No portfolio data, connections, agents, schedules, or
   authority” plus zero spend and an engaged execution kill switch.
2. **Create portfolio.** The user enters `Acme Operating Portfolio`, `America/Chicago`, `USD`,
   business domain, and the portfolio owner. These example values are never prefilled as facts.
3. **Add one entity.** The user creates `Acme, Inc.` and records its mission and one 90-day
   objective. Ownership remains unverified until the user cites a source or explicitly asserts
   it.
4. **Choose one metric.** The user selects weekly recurring revenue and identifies the billing
   platform as its authoritative system.
5. **Create an account reference.** CHIEF records system, environment, account label, owner,
   portfolio ID, and an opaque OS-vault reference. No secret is pasted into CHIEF.
6. **Request one read scope.** CHIEF previews the minimum revenue-read scope, data categories,
   retention, query cadence, and expected provider cost. Write scopes remain absent.
7. **Import first evidence.** After user consent, CHIEF retrieves or accepts one reviewed
   revenue snapshot with provenance, timestamps, confidence, sensitivity, link, and digest.
8. **Create first objective view.** CHIEF shows the objective, metric baseline, missing context,
   and one evidence-linked question. It does not claim a trend from one observation.
9. **Dry-run heartbeat.** CHIEF simulates the proposed weekly check and shows the exact output,
   failure behavior, cost ceiling, quiet hours, digest rule, and escalation triggers. No
   schedule is activated yet.
10. **Approve constrained activation.** The user may approve the read-only heartbeat and local
    digest. Spend remains zero unless a separate non-zero cap is recorded; external messages
    and writes remain denied.
11. **Review receipt.** The first real heartbeat produces its evidence and run receipt. The user
    confirms usefulness before any second source, specialist, or interruption class is added.

## 12. Onboarding completion gate

Onboarding is complete only when all of the following are true:

- the user can see the portfolio boundary, authority summary, spend limits, and kill switches;
- at least one entity/objective is user-entered rather than inferred;
- every connected account is represented only by a non-secret reference and exact consent;
- personal-domain isolation is configured and tested;
- the first material fact has verifiable provenance and freshness;
- heartbeat, quiet hours, interruption budget, expiry, acknowledgement, and escalation behavior
  have been dry-run;
- no specialist has broader access than CHIEF, and no governor can widen user authority;
- recovery, revocation, and kill-switch procedures have been rehearsed;
- the user explicitly accepts the first constrained operating mode.

Failure of any completion item leaves CHIEF in onboarding mode with external writes and spend
disabled.
