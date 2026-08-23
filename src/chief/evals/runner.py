from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from chief.evals.schema import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationCheckKind,
    EvaluationCheckResult,
    EvaluationObservation,
    EvaluationSuite,
    EvaluationSuiteResult,
    EvaluationSummary,
)


def _normalized(value: str | None) -> str | None:
    return value.strip().casefold() if value is not None else None


def _contains_all(text: str, markers: list[str]) -> tuple[bool, list[str]]:
    folded = text.casefold()
    missing = [marker for marker in markers if marker.casefold() not in folded]
    return not missing, missing


class EvaluationRunner:
    """Apply deterministic release checks to pre-recorded observations."""

    def run(
        self,
        suite: EvaluationSuite,
        observations: Mapping[str, EvaluationObservation],
        *,
        generated_at: datetime | None = None,
    ) -> EvaluationSuiteResult:
        case_results = [self.evaluate_case(case, observations.get(case.id)) for case in suite.cases]
        summary = self._summary(case_results)
        gate_failures = self._gate_failures(suite, case_results, summary)
        timestamp = generated_at or datetime.now(UTC)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        else:
            timestamp = timestamp.astimezone(UTC)
        return EvaluationSuiteResult(
            suite_id=suite.id,
            suite_name=suite.name,
            suite_version=suite.version,
            generated_at=timestamp,
            release_gate_passed=not gate_failures,
            gate_failures=gate_failures,
            summary=summary,
            cases=case_results,
        )

    def evaluate_case(
        self,
        case: EvaluationCase,
        observation: EvaluationObservation | None,
    ) -> EvaluationCaseResult:
        if observation is None:
            check = EvaluationCheckResult(
                kind=EvaluationCheckKind.OBSERVATION_PRESENT,
                passed=False,
                message="No observation was supplied for this case.",
                expected="recorded observation",
                observed=None,
            )
            return EvaluationCaseResult(
                case_id=case.id,
                name=case.name,
                critical=case.critical,
                weight=case.weight,
                passed=False,
                score=0,
                checks=[check],
            )

        expectation = case.expectation
        checks: list[EvaluationCheckResult] = []

        if expectation.expected_tool is not None:
            passed = _normalized(observation.selected_tool) == _normalized(
                expectation.expected_tool
            )
            checks.append(
                EvaluationCheckResult(
                    kind=EvaluationCheckKind.TOOL_CHOICE,
                    passed=passed,
                    message=(
                        "Selected the expected tool."
                        if passed
                        else "Selected tool does not match the expected tool."
                    ),
                    expected=expectation.expected_tool,
                    observed=observation.selected_tool,
                )
            )

        if expectation.approval_required is not None:
            passed = observation.approval_required is expectation.approval_required
            checks.append(
                EvaluationCheckResult(
                    kind=EvaluationCheckKind.APPROVAL_REQUIRED,
                    passed=passed,
                    message=(
                        "Approval behavior matched the expectation."
                        if passed
                        else "Approval behavior did not match the expectation."
                    ),
                    expected=expectation.approval_required,
                    observed=observation.approval_required,
                )
            )

        if expectation.forbidden_actions:
            observed_actions = {
                _normalized(action) for action in observation.actions if action.strip()
            }
            if observation.selected_tool:
                observed_actions.add(_normalized(observation.selected_tool))
            violations = [
                action
                for action in expectation.forbidden_actions
                if _normalized(action) in observed_actions
            ]
            checks.append(
                EvaluationCheckResult(
                    kind=EvaluationCheckKind.FORBIDDEN_ACTION,
                    passed=not violations,
                    message=(
                        "No forbidden action was observed."
                        if not violations
                        else "One or more forbidden actions were observed."
                    ),
                    expected={"forbidden": expectation.forbidden_actions},
                    observed={"actions": observation.actions, "violations": violations},
                )
            )

        if expectation.required_evidence_markers:
            passed, missing = _contains_all(
                observation.response_text, expectation.required_evidence_markers
            )
            checks.append(
                EvaluationCheckResult(
                    kind=EvaluationCheckKind.EVIDENCE_MARKERS,
                    passed=passed,
                    message=(
                        "All required evidence markers were present."
                        if passed
                        else "Required evidence markers were missing."
                    ),
                    expected=expectation.required_evidence_markers,
                    observed={"missing": missing},
                )
            )

        if expectation.required_citation_markers:
            passed, missing = _contains_all(
                observation.response_text, expectation.required_citation_markers
            )
            checks.append(
                EvaluationCheckResult(
                    kind=EvaluationCheckKind.CITATION_MARKERS,
                    passed=passed,
                    message=(
                        "All required citation markers were present."
                        if passed
                        else "Required citation markers were missing."
                    ),
                    expected=expectation.required_citation_markers,
                    observed={"missing": missing},
                )
            )

        if expectation.required_memory_tokens:
            memory_text = "\n".join(observation.recalled_memory)
            passed, missing = _contains_all(memory_text, expectation.required_memory_tokens)
            checks.append(
                EvaluationCheckResult(
                    kind=EvaluationCheckKind.MEMORY_RECALL,
                    passed=passed,
                    message=(
                        "All required memory tokens were recalled."
                        if passed
                        else "Required memory tokens were not recalled."
                    ),
                    expected=expectation.required_memory_tokens,
                    observed={"missing": missing},
                )
            )

        if expectation.latency_ceiling_ms is not None:
            passed = (
                observation.latency_ms is not None
                and observation.latency_ms <= expectation.latency_ceiling_ms
            )
            checks.append(
                EvaluationCheckResult(
                    kind=EvaluationCheckKind.LATENCY,
                    passed=passed,
                    message=(
                        "Latency was within the configured ceiling."
                        if passed
                        else "Latency was missing or exceeded the configured ceiling."
                    ),
                    expected={"maximum_ms": expectation.latency_ceiling_ms},
                    observed={"latency_ms": observation.latency_ms},
                )
            )

        passed_checks = sum(check.passed for check in checks)
        score = passed_checks / len(checks)
        return EvaluationCaseResult(
            case_id=case.id,
            name=case.name,
            critical=case.critical,
            weight=case.weight,
            passed=passed_checks == len(checks),
            score=score,
            checks=checks,
        )

    @staticmethod
    def _summary(case_results: list[EvaluationCaseResult]) -> EvaluationSummary:
        passed_cases = sum(result.passed for result in case_results)
        failed_cases = len(case_results) - passed_cases
        critical_failures = sum(result.critical and not result.passed for result in case_results)
        checks = [check for result in case_results for check in result.checks]
        passed_checks = sum(check.passed for check in checks)
        total_weight = sum(result.weight for result in case_results)
        weighted_score = sum(result.score * result.weight for result in case_results) / total_weight

        check_pass_rates: dict[EvaluationCheckKind, float] = {}
        for kind in EvaluationCheckKind:
            matching = [check for check in checks if check.kind == kind]
            if matching:
                check_pass_rates[kind] = sum(check.passed for check in matching) / len(matching)

        return EvaluationSummary(
            total_cases=len(case_results),
            passed_cases=passed_cases,
            failed_cases=failed_cases,
            critical_failures=critical_failures,
            total_checks=len(checks),
            passed_checks=passed_checks,
            case_pass_rate=passed_cases / len(case_results),
            weighted_score=weighted_score,
            check_pass_rates=check_pass_rates,
        )

    @staticmethod
    def _gate_failures(
        suite: EvaluationSuite,
        case_results: list[EvaluationCaseResult],
        summary: EvaluationSummary,
    ) -> list[str]:
        thresholds = suite.thresholds
        failures: list[str] = []
        if summary.case_pass_rate < thresholds.minimum_case_pass_rate:
            failures.append(
                "Case pass rate "
                f"{summary.case_pass_rate:.3f} is below {thresholds.minimum_case_pass_rate:.3f}."
            )
        if summary.weighted_score < thresholds.minimum_weighted_score:
            failures.append(
                "Weighted score "
                f"{summary.weighted_score:.3f} is below {thresholds.minimum_weighted_score:.3f}."
            )
        if summary.failed_cases > thresholds.maximum_failed_cases:
            failures.append(
                f"Failed cases {summary.failed_cases} exceed {thresholds.maximum_failed_cases}."
            )
        if summary.critical_failures > thresholds.maximum_critical_failures:
            failures.append(
                "Critical failures "
                f"{summary.critical_failures} exceed {thresholds.maximum_critical_failures}."
            )
        for kind, minimum in thresholds.minimum_check_pass_rates.items():
            actual = summary.check_pass_rates.get(kind, 0.0)
            if actual < minimum:
                failures.append(f"{kind.value} pass rate {actual:.3f} is below {minimum:.3f}.")

        blocking = set(thresholds.blocking_checks)
        blocking_failures = sorted(
            {
                check.kind.value
                for result in case_results
                for check in result.checks
                if check.kind in blocking and not check.passed
            }
        )
        if blocking_failures:
            failures.append("Blocking checks failed: " + ", ".join(blocking_failures) + ".")
        return failures
