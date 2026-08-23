# CHIEF release evaluations

CHIEF evaluations are deterministic release checks over recorded observations. The evaluator never calls a model, tool, network service, or user account, so it is safe and fast enough to run on every change.

Each case can assert:

- the exact selected tool;
- whether approval was required;
- actions that must never occur;
- evidence and citation markers required in the response;
- tokens that must appear in recalled memory;
- a maximum observed latency.

Suites combine case-level checks with release thresholds for case pass rate, weighted score, failed and critical case counts, and per-check pass rates. Approval and forbidden-action failures block release by default even when aggregate score thresholds are permissive.

The runner consumes `EvaluationObservation` records supplied by a test harness or captured fixture. It deliberately does not produce those observations itself. JSON reports are stable, validated, and suitable for CI artifacts. Production model or integration evaluations should record their outputs separately, then pass only the normalized observation into this offline gate.
