from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from chief.core.config import Settings
from chief.events.scheduler import Scheduler
from chief.events.store import EventStore
from chief.intelligence.evidence import build_recon_evidence_service
from chief.intelligence.execution import SpecialistRunService, SQLiteSpecialistDispatchStore
from chief.intelligence.orchestrator import SpecialistOrchestrator
from chief.intelligence.scout import ReconScoutService, SQLiteReconScoutDispatchStore
from chief.models.ollama import OllamaProvider
from chief.models.route_audit import SQLiteModelRouteStore
from chief.models.router import ModelRouter
from chief.portfolio.store import SQLitePortfolioStore
from chief.runs import RunEngine, SQLiteRunStore
from chief.security.secrets import EncryptedSecretStore, SecretResolver


@dataclass(frozen=True, slots=True)
class IntelligenceRuntimeServices:
    specialist_runs: SpecialistRunService
    recon_scouts: ReconScoutService


def _resolver(database_path: Path) -> SecretResolver:
    try:
        store = EncryptedSecretStore(database_path)
    except RuntimeError:
        store = None
    return SecretResolver(store, allow_environment_fallback=True)


def configure_runtime_intelligence(
    *,
    database_path: str | Path,
    run_store: SQLiteRunStore,
    run_engine: RunEngine,
    event_store: EventStore,
) -> IntelligenceRuntimeServices:
    """Register durable intelligence handlers on the unattended runtime worker."""

    database_path = Path(database_path)
    settings = Settings.from_env()
    model_router = ModelRouter(
        [
            OllamaProvider(
                model=settings.ollama_model,
                base_url=settings.ollama_url,
                timeout=settings.model_timeout_seconds,
                max_response_bytes=settings.max_model_response_bytes,
            )
        ]
    )
    portfolio_store = SQLitePortfolioStore(database_path)
    orchestrator = SpecialistOrchestrator(portfolio_store)
    route_store = SQLiteModelRouteStore(database_path)
    specialist_runs = SpecialistRunService(
        portfolio_store=portfolio_store,
        orchestrator=orchestrator,
        run_store=run_store,
        model_router=model_router,
        dispatch_store=SQLiteSpecialistDispatchStore(database_path),
        route_store=route_store,
    )
    specialist_runs.register_handler(run_engine)

    resolver = _resolver(database_path)
    evidence_service = build_recon_evidence_service(
        lambda: resolver.get("CHIEF_BRAVE_SEARCH_API_KEY")
    )
    recon_scouts = ReconScoutService(
        portfolio_store=portfolio_store,
        orchestrator=orchestrator,
        run_store=run_store,
        model_router=model_router,
        evidence_service=evidence_service,
        dispatch_store=SQLiteReconScoutDispatchStore(database_path),
        event_store=event_store,
        scheduler=Scheduler(event_store),
        route_store=route_store,
    )
    recon_scouts.register_handlers(run_engine)
    return IntelligenceRuntimeServices(
        specialist_runs=specialist_runs,
        recon_scouts=recon_scouts,
    )
