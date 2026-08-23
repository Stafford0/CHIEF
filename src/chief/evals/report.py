from __future__ import annotations

import json
from pathlib import Path

from chief.evals.schema import EvaluationSuiteResult


def report_to_json(result: EvaluationSuiteResult, *, indent: int | None = 2) -> str:
    """Serialize a report deterministically for CI artifacts and review."""

    if indent is not None and not 0 <= indent <= 8:
        raise ValueError("Report indentation must be between 0 and 8, or None.")
    return json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        indent=indent,
        separators=(",", ":") if indent is None else None,
    )


def report_from_json(payload: str) -> EvaluationSuiteResult:
    """Validate a previously serialized evaluation report."""

    return EvaluationSuiteResult.model_validate_json(payload)


def write_json_report(
    result: EvaluationSuiteResult,
    path: str | Path,
    *,
    indent: int | None = 2,
) -> Path:
    """Write a UTF-8 JSON report, creating only its direct parent directories."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report_to_json(result, indent=indent) + "\n", encoding="utf-8")
    return destination
