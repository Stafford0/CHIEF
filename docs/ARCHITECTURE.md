# CHIEF Architecture

Status: foundation placeholder

This document will describe CHIEF's executable architecture as it is implemented. The Obsidian `CHIEF.md` remains the human-readable project constitution during the foundation phase.

## Architectural boundaries

CHIEF is the orchestration system, not an individual language model.

Core boundaries:

1. **Core** coordinates requests and execution.
2. **Models** provide replaceable intelligence providers.
3. **Memory** stores and retrieves durable context.
4. **Tools** expose explicit capabilities through contracts.
5. **Guard** evaluates permissions and risk before execution.
6. **Integrations** translate external systems into CHIEF tools.
7. **Audit** records meaningful actions and decisions.
8. **Events** provide the future foundation for CHIEF Watch.

## Dependency direction

Domain logic should depend on CHIEF-owned interfaces rather than vendor SDKs wherever practical. Vendor-specific code belongs at adapter boundaries.

## Security baseline

- No credentials in source control.
- Start external integrations read-only.
- Separate read and write capabilities.
- High-impact actions require explicit authorization.
- Never report an action as successful until verification supports that claim.

Detailed Architecture Decision Records will live in `docs/adr/`.
