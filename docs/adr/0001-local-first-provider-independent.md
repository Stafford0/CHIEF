# ADR 0001: Local-first, provider-independent architecture

**Status:** Accepted

## Context

CHIEF is intended to remain useful without mandatory paid cloud infrastructure or permanent dependence on a single AI provider.

## Decision

CHIEF will be designed local-first and provider-independent.

AI models, databases, external services, and automation platforms will be accessed through CHIEF-owned interfaces where practical. Cloud providers may enhance the system, but core architecture must not assume that any single provider is permanently available.

## Consequences

- Local execution remains a first-class path.
- Model adapters are replaceable.
- Vendor-specific dependencies stay near integration boundaries.
- Some abstractions require more work initially, but reduce lock-in later.
- Features that cannot operate locally must be clearly identified as optional cloud capabilities.
