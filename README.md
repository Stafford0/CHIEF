# CHIEF

**Cognitive Hub for Intelligence, Execution & Foresight**

CHIEF is a local-first, provider-independent personal AI orchestration system designed to understand requests, retrieve context, use tools, execute bounded tasks, verify results, and surface what deserves attention.

## Current milestone

**CHIEF ZERO** — build a useful local foundation with approximately $0 additional recurring infrastructure cost.

## North-star acceptance test

> Chief, inspect Parcel Signals and tell me what needs my attention.

CHIEF should gather current evidence from available systems, prioritize findings, explain what supports them, recommend next actions, and clearly identify anything it could not verify.

## Core principles

- Local first
- Provider independent
- Evidence before assumption
- Least privilege
- Human authority
- Auditability
- Cost awareness
- Verify execution before claiming success

## Planned architecture

```text
User
  |
CHIEF Core
  |-- Context & Memory
  |-- Model Router
  |-- Planner / Orchestrator
  |-- Tool Registry
  |-- CHIEF Guard
  |-- Verification
  |-- Audit & Events
  |
Integrations / Tools
```

## Repository layout

```text
chief/
├── apps/             # User-facing applications
├── src/chief/        # Core Python package
│   ├── core/         # Orchestration, context, planning
│   ├── models/       # Provider-independent model adapters
│   ├── memory/       # Persistent and retrieval memory
│   ├── tools/        # Tool contracts and registry
│   ├── guard/        # Permissions and risk controls
│   ├── agents/       # Specialist agent definitions
│   ├── integrations/ # External-system adapters
│   ├── events/       # Event and CHIEF Watch foundations
│   └── audit/        # Action and decision audit trail
├── tests/            # Automated tests
├── docs/             # Technical documentation and ADRs
├── scripts/          # Development/maintenance scripts
└── infra/            # Local infrastructure configuration
```

## Development rule

Build vertically and keep each milestone usable:

**Working → Reliable → Secure → Fast → Convenient → Beautiful**

Do not add autonomous write capabilities before permission, approval, verification, and audit controls exist.

## Status

Foundation in progress. Functional AI code has intentionally not been added yet.
