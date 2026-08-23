import hashlib
import ipaddress
import logging
import re
import secrets
import time
from datetime import UTC, datetime
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request

from chief.agents import ExecutionPlan, PlanExecutor, PlanOutcome
from chief.api import create_operating_router
from chief.audit.log import AuditEvent
from chief.audit.sqlite import SQLiteAuditLog
from chief.business import SQLiteBusinessGraphStore
from chief.core.config import Settings
from chief.core.identity import SYSTEM_IDENTITY
from chief.core.rate_limit import RateLimitDecision, SlidingWindowRateLimiter
from chief.core.request_limits import RequestBodyLimitMiddleware
from chief.core.sqlite_session_store import SQLiteSessionStore
from chief.core.tool_planner import DeterministicToolPlanner, PendingAction
from chief.decisions import SQLiteDecisionStore
from chief.events.scheduler import Scheduler
from chief.events.schema import Event, Schedule, ScheduleCadence, ScheduleStatus
from chief.events.store import EventStore
from chief.foresight.schema import KPI, Assumption, ForesightSignal
from chief.foresight.scoring import rank_signals
from chief.foresight.store import ForesightStore
from chief.memory.commands import (
    CorrectMemoryCommand,
    ForgetMemoryCommand,
    MemoryCommand,
    MemoryCommandParser,
)
from chief.memory.manager import MemoryManager
from chief.memory.sqlite import SQLiteMemoryStore
from chief.models.ollama import OllamaProvider
from chief.models.router import ModelRouter
from chief.notifications import AttentionPolicy, NotificationStore
from chief.runs import (
    ActionResult,
    EngineOutcome,
    IdempotencyConflict,
    RunEngine,
    RunRecord,
    RunStatus,
    SQLiteRunStore,
    StepRecord,
    StepSpec,
    VerificationStatus,
)
from chief.tools.registry import ToolRegistry, create_standard_registry
from chief.work.briefing import build_briefing
from chief.work.schema import ExecutiveBriefing, Goal, Task, WorkPriority, WorkStatus
from chief.work.store import WorkStore

_IPV4_OCTET = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
_PRIVATE_LAN_ORIGIN_PATTERN = (
    rf"^http://(?:"
    rf"10\.{_IPV4_OCTET}\.{_IPV4_OCTET}\.{_IPV4_OCTET}|"
    rf"192\.168\.{_IPV4_OCTET}\.{_IPV4_OCTET}|"
    rf"172\.(?:1[6-9]|2\d|3[01])\.{_IPV4_OCTET}\.{_IPV4_OCTET}"
    rf"):5173$"
)

settings = Settings.from_env()
app = FastAPI(
    title="CHIEF",
    description="Cognitive Hub for Intelligence, Execution & Foresight",
    version="0.0.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_origin_regex=_PRIVATE_LAN_ORIGIN_PATTERN if settings.allow_private_lan_ui else None,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=settings.max_request_bytes)

logger = logging.getLogger("chief.api")
_PUBLIC_PATHS = frozenset({"/health"})
_LAN_ORIGIN = re.compile(_PRIVATE_LAN_ORIGIN_PATTERN)
_remote_rate_limiter = SlidingWindowRateLimiter(settings.remote_rate_limit_per_minute)


def _is_loopback_client(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host == "testclient":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.casefold() == "localhost"


def _is_private_client(request: Request) -> bool:
    host = request.client.host if request.client else ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private


def _bearer_token(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.casefold() != "bearer" or not value:
        return None
    return value


def _origin_allowed(origin: str) -> bool:
    return origin in settings.cors_origins or (
        settings.allow_private_lan_ui and _LAN_ORIGIN.fullmatch(origin) is not None
    )


@app.middleware("http")
async def operational_headers(request: Request, call_next):
    """Attach correlation, timing, and browser hardening headers."""
    request_id = request.headers.get("x-request-id", str(uuid4()))[:128]
    request.state.request_id = request_id
    started = time.perf_counter()
    is_public = request.url.path in _PUBLIC_PATHS or request.method == "OPTIONS"
    is_loopback = _is_loopback_client(request)
    response = None
    rate_limit: RateLimitDecision | None = None

    if not is_public and not is_loopback and not settings.allow_private_lan_ui:
        response = JSONResponse(
            status_code=403,
            content={
                "detail": "Remote access is disabled; use loopback or enable protected LAN mode."
            },
        )

    if response is None and not is_public and not is_loopback and not _is_private_client(request):
        response = JSONResponse(
            status_code=403,
            content={"detail": "Only private-network clients are permitted in LAN mode."},
        )

    if response is None and not is_public and not is_loopback:
        client_key = request.client.host if request.client else "unknown"
        rate_limit = _remote_rate_limiter.check(client_key)
        if not rate_limit.allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Remote request rate limit exceeded."},
                headers={"Retry-After": str(rate_limit.retry_after_seconds)},
            )

    origin = request.headers.get("origin")
    if (
        response is None
        and not is_public
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and origin is not None
        and not _origin_allowed(origin)
    ):
        response = JSONResponse(
            status_code=403,
            content={"detail": "Request origin is not trusted."},
        )

    if response is None and not is_public and settings.api_token is not None:
        supplied = _bearer_token(request)
        if supplied is None or not secrets.compare_digest(supplied, settings.api_token):
            response = JSONResponse(
                status_code=401,
                content={"detail": "A valid CHIEF bearer token is required."},
                headers={"WWW-Authenticate": "Bearer"},
            )

    if settings.api_token is None:
        request.state.actor_id = "local"
    else:
        request.state.actor_id = (
            "operator:" + hashlib.sha256(settings.api_token.encode("utf-8")).hexdigest()[:16]
        )

    if response is None:
        response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    # Voice remains opt-in in the UI; allow only this origin to request microphone access.
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    if rate_limit is not None:
        response.headers["X-RateLimit-Limit"] = str(_remote_rate_limiter.limit)
        response.headers["X-RateLimit-Remaining"] = str(rate_limit.remaining)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    logger.info(
        "request_complete",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 3),
        },
    )
    return response


model_provider = OllamaProvider(
    model=settings.ollama_model,
    base_url=settings.ollama_url,
    timeout=settings.model_timeout_seconds,
    max_response_bytes=settings.max_model_response_bytes,
)
model_router = ModelRouter([model_provider])


def generate_model(prompt: str, system_prompt: str):
    """Route providers while preserving runtime/test provider replacement."""
    router = model_router
    if model_provider not in router.providers:
        router = ModelRouter([model_provider])
    return router.generate(prompt, system_prompt)


memory_store = SQLiteMemoryStore()
memory_manager = MemoryManager(memory_store)
memory_command_parser = MemoryCommandParser()

session_store = SQLiteSessionStore()
work_store = WorkStore()
event_store = EventStore()
scheduler = Scheduler(event_store)
foresight_store = ForesightStore()
run_store = SQLiteRunStore()
decision_store = SQLiteDecisionStore()
business_store = SQLiteBusinessGraphStore()
notification_store = NotificationStore()
attention_policy = AttentionPolicy()


def _generate_briefing_step(_context, _arguments) -> ActionResult:
    briefing = build_briefing(work_store, limit=10)
    return ActionResult(
        result_data=briefing.model_dump(mode="json"),
        verification_status=VerificationStatus.VERIFIED,
    )


def _foresight_snapshot_step(_context, _arguments) -> ActionResult:
    ranked = rank_signals(foresight_store.list_signals(limit=100))[:10]
    return ActionResult(
        result_data={
            "signals": [
                {
                    "signal": signal.model_dump(mode="json"),
                    "attention": score.model_dump(mode="json"),
                }
                for signal, score in ranked
            ]
        },
        verification_status=VerificationStatus.VERIFIED,
    )


run_engine = RunEngine(
    run_store,
    {
        "briefing.generate": _generate_briefing_step,
        "foresight.snapshot": _foresight_snapshot_step,
    },
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WEB_UI_PATH = Path(__file__).resolve().parents[1] / "web" / "index.html"
audit_log = SQLiteAuditLog()
tool_registry: ToolRegistry = create_standard_registry(
    [str(PROJECT_ROOT)],
    audit_log=audit_log,
)
tool_planner = DeterministicToolPlanner()
plan_executor = PlanExecutor(tool_registry, max_steps=8)


def _record_domain_change(
    request: Request,
    domain: str,
    action: str,
    entity_id: str,
) -> None:
    """Audit state transitions without copying domain content into the log."""
    audit_log.record(
        AuditEvent(
            tool_name=f"domain.{domain}",
            approved=False,
            decision="state_change",
            success=True,
            request_id=request.state.request_id,
            actor_id=request.state.actor_id,
            metadata={
                "event_type": f"{domain}.{action}",
                "entity_id": entity_id,
            },
        )
    )


app.include_router(
    create_operating_router(
        decision_store=decision_store,
        business_store=business_store,
        notification_store=notification_store,
        attention_policy=attention_policy,
        record_change=_record_domain_change,
    )
)


def _audit_context(
    request: Request,
    *,
    session_id: UUID | None = None,
    proposal_id: str | None = None,
) -> dict[str, str | None]:
    return {
        "request_id": request.state.request_id,
        "actor_id": request.state.actor_id,
        "session_id": str(session_id) if session_id else None,
        "proposal_id": proposal_id,
    }


def _record_approval_lifecycle(
    request: Request,
    *,
    session_id: UUID,
    proposal_id: str,
    tool_name: str,
    decision: str,
    success: bool = False,
) -> None:
    tool_registry.audit_log.record(
        AuditEvent(
            tool_name=tool_name,
            approved=decision == "approved",
            decision=decision,
            success=success,
            metadata={"event_type": f"tool.{decision}"},
            **_audit_context(
                request,
                session_id=session_id,
                proposal_id=proposal_id,
            ),
        )
    )


def _require_execution_enabled() -> None:
    if not settings.execution_enabled:
        raise HTTPException(
            status_code=503,
            detail="CHIEF execution is paused by the operator kill switch.",
        )


def _run_readiness_check(name: str, check) -> bool:
    try:
        return bool(check())
    except Exception:  # pragma: no cover - exercised through injected failures
        logger.exception("readiness_check_failed", extra={"component": name})
        return False


class ChatRequest(BaseModel):
    message: str
    session_id: UUID | None = None


class ChatResponse(BaseModel):
    response: str
    provider: str
    model: str
    session_id: UUID
    status: str = "completed"
    pending_action: str | None = None
    tool_description: str | None = None


class ToolDefinitionResponse(BaseModel):
    name: str
    description: str
    risk: str
    requires_approval: bool
    input_schema: dict[str, Any]
    side_effects: bool
    idempotent: bool
    timeout_seconds: int


class ToolExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolExecuteResponse(BaseModel):
    success: bool
    content: str
    data: dict[str, Any]
    error: str | None = None


class GoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=10_000)
    target_date: str | None = None


class GoalUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=10_000)
    status: WorkStatus | None = None
    target_date: str | None = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=10_000)
    goal_id: UUID | None = None
    priority: WorkPriority = WorkPriority.MEDIUM
    due_at: str | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=10_000)
    goal_id: UUID | None = None
    status: WorkStatus | None = None
    priority: WorkPriority | None = None
    due_at: str | None = None
    blocked_reason: str | None = Field(default=None, max_length=2_000)


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    event_type: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    cadence: ScheduleCadence
    timezone: str = "UTC"
    run_at: datetime | None = None
    interval_seconds: int | None = Field(default=None, ge=1, le=31_536_000)
    daily_time: clock_time | None = None


class ScheduleUpdate(BaseModel):
    status: ScheduleStatus


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=256)
    correlation_id: str | None = Field(default=None, max_length=256)
    input_data: dict[str, Any] = Field(default_factory=dict)
    steps: list[StepSpec] = Field(min_length=1, max_length=100)


class RunCancel(BaseModel):
    reason: str = Field(default="Operator cancelled the run.", min_length=1, max_length=2_000)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "online",
        "system": "CHIEF",
        "version": "0.0.1",
    }


@app.get("/ready", response_model=None)
def readiness() -> dict[str, Any] | JSONResponse:
    """Check the core state stores instead of returning a static readiness claim."""
    checks = {
        "memory": _run_readiness_check("memory", memory_store.health),
        "work_store": _run_readiness_check("work_store", work_store.health),
        "tool_registry": _run_readiness_check("tool_registry", lambda: tool_registry.count() > 0),
        "audit_store": _run_readiness_check(
            "audit_store", lambda: tool_registry.audit_log.count() >= 0
        ),
        "event_store": _run_readiness_check(
            "event_store", lambda: event_store.counts() is not None
        ),
        "foresight_store": _run_readiness_check(
            "foresight_store", lambda: foresight_store.list_signals(limit=1) is not None
        ),
        "run_store": _run_readiness_check(
            "run_store", lambda: run_store.list_runs(limit=1) is not None
        ),
        "session_store": _run_readiness_check("session_store", lambda: session_store.count() >= 0),
        "decision_store": _run_readiness_check(
            "decision_store", lambda: decision_store.list(limit=1) is not None
        ),
        "business_store": _run_readiness_check(
            "business_store",
            lambda: business_store.list_nodes(owner_id="__readiness__", limit=1) is not None,
        ),
        "notification_store": _run_readiness_check(
            "notification_store",
            lambda: notification_store.get_by_idempotency_key("__readiness__") is None,
        ),
    }
    payload = {
        "status": "ready" if all(checks.values()) else "not_ready",
        "checks": checks,
    }
    if payload["status"] != "ready":
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def chat_ui() -> HTMLResponse:
    """Serve CHIEF's lightweight local chat interface."""
    return HTMLResponse(WEB_UI_PATH.read_text(encoding="utf-8"))


@app.get("/system")
def system_info() -> dict[str, str]:
    return {
        "name": "CHIEF",
        "full_name": "Cognitive Hub for Intelligence, Execution & Foresight",
        "version": "0.0.1",
        "milestone": "CHIEF ZERO",
        "environment": settings.environment,
    }


@app.get("/goals", response_model=list[Goal])
def list_goals(include_closed: bool = False) -> list[Goal]:
    return work_store.list_goals(include_closed=include_closed)


@app.post("/goals", response_model=Goal, status_code=201)
def create_goal(payload: GoalCreate, request: Request) -> Goal:
    try:
        goal = Goal(
            title=payload.title, description=payload.description, target_date=payload.target_date
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = work_store.save_goal(goal)
    _record_domain_change(request, "goal", "created", str(saved.id))
    return saved


@app.patch("/goals/{goal_id}", response_model=Goal)
def update_goal(goal_id: UUID, payload: GoalUpdate, request: Request) -> Goal:
    goal = work_store.get_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found.")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(goal, key, value)
    try:
        saved = work_store.save_goal(Goal.model_validate(goal.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _record_domain_change(request, "goal", "updated", str(saved.id))
    return saved


@app.get("/tasks", response_model=list[Task])
def list_tasks(include_closed: bool = False, limit: int = 200) -> list[Task]:
    try:
        return work_store.list_tasks(include_closed=include_closed, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/tasks", response_model=Task, status_code=201)
def create_task(payload: TaskCreate, request: Request) -> Task:
    if payload.goal_id is not None and work_store.get_goal(payload.goal_id) is None:
        raise HTTPException(status_code=422, detail="Referenced goal does not exist.")
    try:
        task = Task(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = work_store.save_task(task)
    _record_domain_change(request, "task", "created", str(saved.id))
    return saved


@app.patch("/tasks/{task_id}", response_model=Task)
def update_task(task_id: UUID, payload: TaskUpdate, request: Request) -> Task:
    task = work_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    values = payload.model_dump(exclude_unset=True)
    if values.get("goal_id") is not None and work_store.get_goal(values["goal_id"]) is None:
        raise HTTPException(status_code=422, detail="Referenced goal does not exist.")
    for key, value in values.items():
        setattr(task, key, value)
    try:
        saved = work_store.save_task(Task.model_validate(task.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _record_domain_change(request, "task", "updated", str(saved.id))
    return saved


@app.get("/briefing", response_model=ExecutiveBriefing)
def executive_briefing(limit: int = 10) -> ExecutiveBriefing:
    if not 1 <= limit <= 50:
        raise HTTPException(status_code=422, detail="Briefing limit must be between 1 and 50.")
    return build_briefing(work_store, limit=limit)


@app.get("/schedules", response_model=list[Schedule])
def list_schedules(include_inactive: bool = False) -> list[Schedule]:
    return event_store.list_schedules(include_inactive=include_inactive)


@app.post("/schedules", response_model=Schedule, status_code=201)
def create_schedule(payload: ScheduleCreate, request: Request) -> Schedule:
    try:
        saved = scheduler.add(Schedule(**payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _record_domain_change(request, "schedule", "created", str(saved.id))
    return saved


@app.patch("/schedules/{schedule_id}", response_model=Schedule)
def update_schedule(schedule_id: UUID, payload: ScheduleUpdate, request: Request) -> Schedule:
    schedule = event_store.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    schedule.status = payload.status
    saved = event_store.save_schedule(schedule)
    _record_domain_change(request, "schedule", "updated", str(saved.id))
    return saved


@app.post("/scheduler/tick", response_model=Event | None)
def scheduler_tick(request: Request) -> Event | None:
    """Queue at most one due event; execution remains a separate guarded concern."""
    _require_execution_enabled()
    event = scheduler.tick(f"api:{request.state.actor_id}")
    if event is not None:
        _record_domain_change(request, "event", "queued", str(event.id))
    return event


@app.get("/events", response_model=list[Event])
def list_events(limit: int = 100) -> list[Event]:
    try:
        return event_store.list_events(limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/signals", response_model=list[ForesightSignal])
def list_signals(include_closed: bool = False, limit: int = 200) -> list[ForesightSignal]:
    try:
        return foresight_store.list_signals(include_closed=include_closed, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/signals", response_model=ForesightSignal, status_code=201)
def create_signal(signal: ForesightSignal, request: Request) -> ForesightSignal:
    saved = foresight_store.save_signal(signal)
    _record_domain_change(request, "foresight_signal", "saved", str(saved.id))
    return saved


@app.get("/assumptions", response_model=list[Assumption])
def list_assumptions() -> list[Assumption]:
    return foresight_store.list_assumptions()


@app.post("/assumptions", response_model=Assumption, status_code=201)
def create_assumption(assumption: Assumption, request: Request) -> Assumption:
    saved = foresight_store.save_assumption(assumption)
    _record_domain_change(request, "assumption", "saved", str(saved.id))
    return saved


@app.get("/kpis", response_model=list[KPI])
def list_kpis() -> list[KPI]:
    return foresight_store.list_kpis()


@app.post("/kpis", response_model=KPI, status_code=201)
def create_kpi(kpi: KPI, request: Request) -> KPI:
    saved = foresight_store.save_kpi(kpi)
    _record_domain_change(request, "kpi", "saved", str(saved.id))
    return saved


@app.get("/foresight")
def foresight_snapshot(limit: int = 10) -> dict[str, Any]:
    if not 1 <= limit <= 50:
        raise HTTPException(status_code=422, detail="Foresight limit must be between 1 and 50.")
    ranked = rank_signals(foresight_store.list_signals(limit=200))[:limit]
    return {
        "signals": [
            {
                "signal": signal.model_dump(mode="json"),
                "attention": score.model_dump(mode="json"),
            }
            for signal, score in ranked
        ],
        "assumptions_due": [
            assumption.model_dump(mode="json")
            for assumption in foresight_store.list_assumptions_due()
        ],
        "kpis": [kpi.model_dump(mode="json") for kpi in foresight_store.list_kpis()],
    }


@app.get("/runs", response_model=list[RunRecord])
def list_runs(status: RunStatus | None = None, limit: int = 100) -> list[RunRecord]:
    try:
        return run_store.list_runs(status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/runs", response_model=RunRecord, status_code=201)
def create_run(payload: RunCreate, request: Request) -> RunRecord:
    try:
        saved = run_store.create_run(
            idempotency_key=payload.idempotency_key,
            correlation_id=payload.correlation_id,
            input_data=payload.input_data,
            steps=payload.steps,
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _record_domain_change(request, "run", "created", str(saved.id))
    return saved


@app.get("/runs/{run_id}", response_model=RunRecord)
def get_run(run_id: UUID) -> RunRecord:
    run = run_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@app.get("/runs/{run_id}/steps", response_model=list[StepRecord])
def list_run_steps(run_id: UUID) -> list[StepRecord]:
    if run_store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run_store.list_steps(run_id)


@app.post("/runs/{run_id}/cancel", response_model=RunRecord)
def cancel_run(run_id: UUID, payload: RunCancel, request: Request) -> RunRecord:
    try:
        cancelled = run_store.cancel_run(run_id, reason=payload.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found.") from exc
    _record_domain_change(request, "run", "cancelled", str(cancelled.id))
    return cancelled


@app.post("/runs/worker/tick", response_model=EngineOutcome | None)
def run_worker_tick(request: Request) -> EngineOutcome | None:
    """Execute at most one registered, server-owned run action."""
    _require_execution_enabled()
    outcome = run_engine.execute_once(worker_id=f"api:{request.state.actor_id}")
    if outcome is not None:
        _record_domain_change(request, "run", "worker_tick", str(outcome.run_id))
    return outcome


@app.get("/audit/events")
def list_audit_events(
    limit: int = 100,
    before_sequence: int | None = None,
    after_sequence: int | None = None,
) -> list[dict[str, Any]]:
    try:
        events = audit_log.events(
            limit=limit,
            before_sequence=before_sequence,
            after_sequence=after_sequence,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [
        {
            "sequence": event.sequence,
            "event_id": event.event_id,
            "timestamp": event.timestamp.isoformat(),
            "tool_name": event.tool_name,
            "decision": event.decision,
            "approved": event.approved,
            "success": event.success,
            "error": event.error,
            "request_id": event.request_id,
            "actor_id": event.actor_id,
            "session_id": event.session_id,
            "run_id": event.run_id,
            "step_id": event.step_id,
            "proposal_id": event.proposal_id,
            "previous_hash": event.previous_hash,
            "event_hash": event.event_hash,
            "metadata": event.metadata,
        }
        for event in events
    ]


@app.get("/audit/integrity")
def audit_integrity() -> dict[str, Any]:
    return audit_log.verify_integrity().__dict__


@app.get("/dashboard")
def dashboard_info(request: Request) -> dict[str, Any]:
    """Return live host/runtime telemetry used by the CHIEF command center UI."""
    from chief.core.dashboard import collect_dashboard_snapshot

    snapshot = collect_dashboard_snapshot(PROJECT_ROOT)
    definitions = list(tool_registry.definitions())
    audit_events = tool_registry.audit_log.events()[-8:]
    goals = work_store.list_goals()
    tasks = work_store.list_tasks(limit=20)
    approvals = session_store.pending_tool_calls(request.state.actor_id)
    briefing = build_briefing(work_store, limit=5)
    event_counts = event_store.counts()
    ranked_signals = rank_signals(foresight_store.list_signals(limit=100))[:5]
    recent_runs = run_store.list_runs(limit=10)
    decisions = decision_store.list(limit=20)
    business_nodes = business_store.list_nodes(owner_id=request.state.actor_id, limit=100)
    notifications = notification_store.active(
        recipient_id=request.state.actor_id,
        now=datetime.now(UTC),
        limit=100,
    )
    snapshot["runtime"] = {
        "api_status": "online",
        "execution_enabled": settings.execution_enabled,
        "active_model": model_provider.model,
        "model_provider": model_provider.name,
        "model_routes": model_router.provider_states(),
        "sessions": session_store.count(request.state.actor_id),
        "session_details": session_store.summaries(request.state.actor_id),
        "tools": [
            {
                "name": definition.name,
                "description": definition.description,
                "risk": definition.risk.value,
                "requires_approval": definition.requires_approval,
                "input_schema": definition.input_schema,
                "side_effects": definition.side_effects,
                "idempotent": definition.idempotent,
                "timeout_seconds": definition.timeout_seconds,
            }
            for definition in definitions
        ],
        "permissions": {
            "approval_gated": sum(1 for item in definitions if item.requires_approval),
            "automatic": sum(1 for item in definitions if not item.requires_approval),
        },
        "agents": [
            {"name": "CHIEF Core", "status": "operational", "kind": "orchestrator"},
            {"name": "Memory", "status": "operational", "kind": "service"},
            {
                "name": "Tool Planner",
                "status": "operational",
                "kind": "deterministic planner",
            },
            {
                "name": "Ollama",
                "status": "operational" if snapshot["ollama"]["online"] else "offline",
                "kind": "model service",
            },
        ],
        "queued_tasks": [
            {
                "name": task.title,
                "status": task.status.value,
                "priority": task.priority.value,
                "due_at": task.due_at.isoformat() if task.due_at else None,
                "kind": "task",
            }
            for task in tasks
        ]
        + [{**approval, "kind": "approval"} for approval in approvals],
        "briefing": briefing.model_dump(mode="json"),
        "event_queue": event_counts,
        "schedules": len(event_store.list_schedules()),
        "foresight": [
            {
                "title": signal.title,
                "kind": signal.kind.value,
                "score": score.score,
                "confidence": signal.confidence,
                "evidence_count": len(signal.evidence_refs),
            }
            for signal, score in ranked_signals
        ],
        "runs": [
            {
                "id": str(run.id),
                "status": run.status.value,
                "correlation_id": run.correlation_id,
                "updated_at": run.updated_at.isoformat(),
                "error_code": run.error_code,
            }
            for run in recent_runs
        ],
        "decisions": [
            {
                "id": str(decision.id),
                "title": decision.title,
                "status": decision.status.value,
                "confidence": decision.confidence,
                "updated_at": decision.updated_at.isoformat(),
            }
            for decision in decisions
        ],
        "business_graph": {"active_nodes": len(business_nodes)},
        "attention": {
            "active_notifications": len(notifications),
            "daily_interruption_budget": attention_policy.config.daily_interruption_budget,
            "timezone": attention_policy.config.timezone,
        },
        "recent_executions": [
            {
                "name": event.tool_name,
                "status": "success" if event.success else "failed",
                "decision": event.decision,
                "approved": event.approved,
                "timestamp": event.timestamp.isoformat(),
                "error": event.error,
            }
            for event in reversed(audit_events)
        ],
        "projects": snapshot.get("projects", []),
        "objectives": [
            {
                "name": goal.title,
                "status": goal.status.value,
                "target_date": goal.target_date.isoformat() if goal.target_date else None,
            }
            for goal in goals
        ],
    }
    return snapshot


@app.get("/tools", response_model=list[ToolDefinitionResponse])
def list_tools() -> list[ToolDefinitionResponse]:
    """List tools currently available through CHIEF's guarded registry."""
    return [
        ToolDefinitionResponse(
            name=definition.name,
            description=definition.description,
            risk=definition.risk.value,
            requires_approval=definition.requires_approval,
            input_schema=definition.input_schema,
            side_effects=definition.side_effects,
            idempotent=definition.idempotent,
            timeout_seconds=definition.timeout_seconds,
        )
        for definition in tool_registry.definitions()
    ]


@app.post("/tools/execute", response_model=ToolExecuteResponse)
def execute_tool(tool_request: ToolExecuteRequest, request: Request) -> ToolExecuteResponse:
    """Run automatic tools; sensitive tools can only be approved in their chat session."""
    _require_execution_enabled()
    result = tool_registry.execute(
        tool_request.name,
        tool_request.arguments,
        audit_context=_audit_context(request),
    )
    return ToolExecuteResponse(
        success=result.success,
        content=result.content,
        data=result.data,
        error=result.error,
    )


@app.post("/plans/validate")
def validate_plan(plan: ExecutionPlan) -> dict[str, Any]:
    try:
        plan_executor.validate(plan)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "valid": True,
        "plan_id": str(plan.id),
        "steps": len(plan.steps),
        "approval_gated_steps": sum(
            bool(tool_registry.get(step.tool_name).definition.requires_approval)
            for step in plan.steps
        ),
    }


@app.post("/plans/execute", response_model=PlanOutcome)
def execute_plan(plan: ExecutionPlan, request: Request) -> PlanOutcome:
    _require_execution_enabled()
    try:
        return plan_executor.execute(
            plan,
            actor_id=request.state.actor_id,
            audit_context=_audit_context(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/chat", response_model=ChatResponse)
def chat(chat_request: ChatRequest, request: Request) -> ChatResponse:
    try:
        session = session_store.get_or_create(
            chat_request.session_id,
            owner_id=request.state.actor_id,
        )
    except KeyError:
        session = session_store.create(request.state.actor_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    pending_action = tool_planner.pending_action(chat_request.message)
    if pending_action == PendingAction.APPROVE and not settings.execution_enabled:
        response_text = "CHIEF execution is paused by the operator kill switch."
        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-system",
            model="deterministic",
            session_id=session.id,
            status="execution_paused",
        )

    pending = (
        session_store.take_pending_tool(session.id, owner_id=request.state.actor_id)
        if pending_action is not None
        else None
    )
    if pending is not None:
        pending_call = pending.call

        if pending.expired():
            response_text = f"Approval expired: I did not {pending_call.description}."
            response_status = "expired"
            _record_approval_lifecycle(
                request,
                session_id=session.id,
                proposal_id=str(pending.id),
                tool_name=pending_call.tool_name,
                decision="expired",
            )
        elif pending_action == PendingAction.REJECT:
            response_text = f"Cancelled: I did not {pending_call.description}."
            response_status = "cancelled"
            _record_approval_lifecycle(
                request,
                session_id=session.id,
                proposal_id=str(pending.id),
                tool_name=pending_call.tool_name,
                decision="rejected",
            )
        else:
            result = tool_registry.execute(
                pending_call.tool_name,
                pending_call.arguments,
                approved=True,
                audit_context=_audit_context(
                    request,
                    session_id=session.id,
                    proposal_id=str(pending.id),
                ),
            )
            response_text = result.content
            if result.error and not result.success:
                response_text = f"{result.content} {result.error}"
            response_status = "completed" if result.success else "failed"

        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-tools",
            model="deterministic",
            session_id=session.id,
            status=response_status,
            tool_description=pending_call.description,
        )

    memory_command = memory_command_parser.parse(chat_request.message)

    if isinstance(memory_command, MemoryCommand):
        memory = memory_manager.remember(
            memory_command.content,
            source_type="user",
            source_description="Explicit remember command",
            confidence=1.0,
            importance=0.8,
        )
        _record_domain_change(request, "memory", "remembered", str(memory.id))
        response_text = f"Memory saved: {memory.content}"
        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-memory",
            model="deterministic",
            session_id=session.id,
        )

    if isinstance(memory_command, CorrectMemoryCommand):
        try:
            old_memory = memory_manager.resolve_exact(memory_command.old_content)
        except ValueError:
            response_text = (
                "I found multiple active memories matching that exact content, "
                "so I did not change anything."
            )
            session.add_message("user", chat_request.message)
            session.add_message("assistant", response_text)
            return ChatResponse(
                response=response_text,
                provider="chief-memory",
                model="deterministic",
                session_id=session.id,
            )

        if old_memory is None:
            response_text = (
                "I could not find an active memory matching that exact content, "
                "so I did not change anything."
            )
            session.add_message("user", chat_request.message)
            session.add_message("assistant", response_text)
            return ChatResponse(
                response=response_text,
                provider="chief-memory",
                model="deterministic",
                session_id=session.id,
            )

        new_memory = memory_manager.correct(
            old_memory,
            memory_command.new_content,
            source_type="user",
            source_description="Explicit user correction",
            confidence=1.0,
        )
        _record_domain_change(request, "memory", "corrected", str(new_memory.id))
        response_text = f"Memory corrected: {new_memory.content}"
        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-memory",
            model="deterministic",
            session_id=session.id,
        )

    if isinstance(memory_command, ForgetMemoryCommand):
        try:
            memory = memory_manager.resolve_exact(memory_command.content)
        except ValueError:
            response_text = (
                "I found multiple active memories matching that exact content, "
                "so I did not forget anything."
            )
            session.add_message("user", chat_request.message)
            session.add_message("assistant", response_text)
            return ChatResponse(
                response=response_text,
                provider="chief-memory",
                model="deterministic",
                session_id=session.id,
            )

        if memory is None:
            response_text = (
                "I could not find an active memory matching that exact content, "
                "so I did not forget anything."
            )
            session.add_message("user", chat_request.message)
            session.add_message("assistant", response_text)
            return ChatResponse(
                response=response_text,
                provider="chief-memory",
                model="deterministic",
                session_id=session.id,
            )

        memory_manager.forget(memory)
        _record_domain_change(request, "memory", "forgotten", str(memory.id))
        response_text = f"Memory forgotten: {memory.content}"
        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-memory",
            model="deterministic",
            session_id=session.id,
        )

    planned_call = tool_planner.plan(chat_request.message)
    if planned_call is not None:
        if not settings.execution_enabled:
            response_text = "CHIEF execution is paused by the operator kill switch."
            session.add_message("user", chat_request.message)
            session.add_message("assistant", response_text)
            return ChatResponse(
                response=response_text,
                provider="chief-system",
                model="deterministic",
                session_id=session.id,
                status="execution_paused",
                tool_description=planned_call.description,
            )
        result = tool_registry.execute(
            planned_call.tool_name,
            planned_call.arguments,
            audit_context=_audit_context(request, session_id=session.id),
        )

        if result.content == "Tool execution requires approval.":
            proposal = session.propose_tool(planned_call)
            _record_approval_lifecycle(
                request,
                session_id=session.id,
                proposal_id=str(proposal.id),
                tool_name=planned_call.tool_name,
                decision="proposed",
            )
            response_text = (
                f"Approval required to {planned_call.description}. "
                f"Review code {proposal.digest[:12]}. Reply 'approve' within five minutes "
                "to run this exact action or 'cancel' to discard it."
            )
            response_status = "pending_approval"
        else:
            response_text = result.content
            if result.error and not result.success:
                response_text = f"{result.content} {result.error}"
            response_status = "completed" if result.success else "failed"

        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-tools",
            model="deterministic",
            session_id=session.id,
            status=response_status,
            pending_action=("approve_or_cancel" if response_status == "pending_approval" else None),
            tool_description=planned_call.description,
        )

    memory_context = memory_manager.build_context(chat_request.message)
    conversation_context = session.build_context()
    context_parts = [SYSTEM_IDENTITY]
    if memory_context:
        context_parts.append(memory_context)
    if conversation_context:
        context_parts.append(conversation_context)
    system_prompt = "\n\n".join(context_parts)

    try:
        result = generate_model(
            prompt=chat_request.message,
            system_prompt=system_prompt,
        )
    except RuntimeError as exc:
        response_text = str(exc)
        session.add_message("user", chat_request.message)
        session.add_message("assistant", response_text)
        return ChatResponse(
            response=response_text,
            provider="chief-system",
            model="unavailable",
            session_id=session.id,
            status="unavailable",
        )

    session.add_message("user", chat_request.message)
    session.add_message("assistant", result.content)
    return ChatResponse(
        response=result.content,
        provider=result.provider,
        model=result.model,
        session_id=session.id,
    )
