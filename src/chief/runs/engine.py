from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from chief.runs.schema import (
    RunStatus,
    StepLease,
    StepStatus,
    VerificationStatus,
)
from chief.runs.store import LeaseLost, SQLiteRunStore


class ActionResult(BaseModel):
    """Structured output returned by an injected action handler."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_data: dict[str, Any] = Field(default_factory=dict)
    verification_status: VerificationStatus = VerificationStatus.NOT_REQUIRED


class EngineOutcome(BaseModel):
    """One bounded engine tick and the durable state it produced."""

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    step_id: UUID
    attempt_id: UUID
    step_status: StepStatus
    run_status: RunStatus
    error_code: str | None = None


class ActionError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class RetryableActionError(ActionError):
    def __init__(self, message: str, *, code: str = "action_retryable") -> None:
        super().__init__(message, code=code, retryable=True)


class PermanentActionError(ActionError):
    def __init__(self, message: str, *, code: str = "action_permanent") -> None:
        super().__init__(message, code=code, retryable=False)


@dataclass(frozen=True)
class ActionContext:
    """Handler context with correlation, idempotency, and cooperative controls."""

    store: SQLiteRunStore
    lease: StepLease

    @property
    def correlation_id(self) -> str:
        return self.lease.run.correlation_id

    @property
    def idempotency_key(self) -> str:
        return self.lease.step.idempotency_key

    def cancellation_requested(self) -> bool:
        run = self.store.get_run(self.lease.run.id)
        return run is None or run.status == RunStatus.CANCELLED

    def renew_lease(
        self,
        *,
        lease_seconds: int = 30,
        now: datetime | None = None,
    ) -> StepLease:
        return self.store.renew_lease(
            self.lease_token,
            lease_seconds=lease_seconds,
            now=now,
        )

    @property
    def lease_token(self) -> str:
        return self.lease.lease_token


ActionHandler = Callable[[ActionContext, dict[str, Any]], ActionResult | dict[str, Any]]


class RunEngine:
    """Execute at most one claimed step using explicitly injected handlers."""

    def __init__(
        self,
        store: SQLiteRunStore,
        handlers: Mapping[str, ActionHandler] | None = None,
    ) -> None:
        self.store = store
        self.handlers = dict(handlers or {})

    def register_handler(self, action: str, handler: ActionHandler) -> None:
        if not action.strip():
            raise ValueError("Action name cannot be empty.")
        if action in self.handlers:
            raise ValueError(f"Action handler '{action}' is already registered.")
        self.handlers[action] = handler

    def execute_once(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        retry_delay_seconds: int = 0,
        now: datetime | None = None,
    ) -> EngineOutcome | None:
        """Claim and execute one step, checkpointing exactly one outcome."""

        lease = self.store.claim_next_step(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=now,
        )
        if lease is None:
            return None

        context = ActionContext(store=self.store, lease=lease)
        handler = self.handlers.get(lease.step.action)
        if handler is None:
            return self._fail(
                lease,
                code="handler_not_registered",
                message=f"No handler is registered for action '{lease.step.action}'.",
                retryable=False,
                retry_delay_seconds=retry_delay_seconds,
                now=now,
            )

        try:
            raw_result = handler(context, dict(lease.step.input_data))
            result = (
                raw_result
                if isinstance(raw_result, ActionResult)
                else ActionResult(result_data=raw_result)
            )
        except ActionError as exc:
            return self._fail(
                lease,
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                retry_delay_seconds=retry_delay_seconds,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - handler boundary must checkpoint failures
            return self._fail(
                lease,
                code="handler_exception",
                message=str(exc) or exc.__class__.__name__,
                retryable=True,
                retry_delay_seconds=retry_delay_seconds,
                now=now,
            )

        if context.cancellation_requested():
            return self._outcome(lease, error_code="run_cancelled")

        if result.verification_status in {
            VerificationStatus.PENDING,
            VerificationStatus.FAILED,
        } or (
            lease.step.verification_required
            and result.verification_status != VerificationStatus.VERIFIED
        ):
            return self._fail(
                lease,
                code="verification_incomplete",
                message="The action did not produce the required verified result.",
                retryable=True,
                retry_delay_seconds=retry_delay_seconds,
                result_data=result.result_data,
                verification_status=result.verification_status,
                now=now,
            )

        try:
            step = self.store.complete_step(
                lease.lease_token,
                result_data=result.result_data,
                verification_status=result.verification_status,
                now=now,
            )
        except LeaseLost:
            return self._outcome(lease, error_code="lease_lost")
        run = self.store.get_run(lease.run.id)
        assert run is not None
        return EngineOutcome(
            run_id=run.id,
            step_id=step.id,
            attempt_id=lease.attempt.id,
            step_status=step.status,
            run_status=run.status,
        )

    def drain(
        self,
        *,
        worker_id: str,
        max_steps: int = 100,
        lease_seconds: int = 30,
        retry_delay_seconds: int = 0,
    ) -> list[EngineOutcome]:
        """Run a caller-bounded number of immediately available steps."""

        if not 1 <= max_steps <= 10_000:
            raise ValueError("Engine drain limit must be between 1 and 10,000 steps.")
        outcomes: list[EngineOutcome] = []
        for _ in range(max_steps):
            outcome = self.execute_once(
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                retry_delay_seconds=retry_delay_seconds,
            )
            if outcome is None:
                break
            outcomes.append(outcome)
        return outcomes

    def _fail(
        self,
        lease: StepLease,
        *,
        code: str,
        message: str,
        retryable: bool,
        retry_delay_seconds: int,
        now: datetime | None,
        result_data: dict[str, Any] | None = None,
        verification_status: VerificationStatus | None = None,
    ) -> EngineOutcome:
        if verification_status is None:
            verification_status = (
                VerificationStatus.PENDING
                if lease.step.verification_required
                else VerificationStatus.NOT_REQUIRED
            )
        try:
            step = self.store.fail_step(
                lease.lease_token,
                error_code=code,
                error_message=message,
                retryable=retryable,
                retry_delay_seconds=retry_delay_seconds,
                result_data=result_data,
                verification_status=verification_status,
                now=now,
            )
        except LeaseLost:
            return self._outcome(lease, error_code="lease_lost")
        run = self.store.get_run(lease.run.id)
        assert run is not None
        return EngineOutcome(
            run_id=run.id,
            step_id=step.id,
            attempt_id=lease.attempt.id,
            step_status=step.status,
            run_status=run.status,
            error_code=code,
        )

    def _outcome(self, lease: StepLease, *, error_code: str) -> EngineOutcome:
        run = self.store.get_run(lease.run.id)
        step = self.store.get_step(lease.step.id)
        return EngineOutcome(
            run_id=lease.run.id,
            step_id=lease.step.id,
            attempt_id=lease.attempt.id,
            step_status=step.status if step is not None else StepStatus.CANCELLED,
            run_status=run.status if run is not None else RunStatus.CANCELLED,
            error_code=error_code,
        )
