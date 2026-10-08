"""The outreach web API and its page (requirement 13.4).

A router under ``/api/outreach`` and a page at ``/outreach``, added to the ingestion web
app by the root (``leadforge.cli``): ingestion never imports this. Writes need the same
``X-LeadForge`` header as the ingestion API. Reads open their own engine on the
configured store, mask contact identifiers unless ``reveal`` is asked for, and never
migrate it. A search runs to completion inside the request (the demo's sources are
synthetic and quick); the UI shows a busy state until it returns.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.catalog import (
    UnknownCatalogKeyError,
    load_catalog,
    load_roles,
)
from leadforge.lead_ingestion.database import DatabaseConfigError, create_store_engine
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import RunInProgressError
from leadforge.lead_ingestion.store.migrate import StoreNotMigratedError, require_head
from leadforge.outreach.acceptance import SeededAcceptance
from leadforge.outreach.config import OutreachConfig, load_outreach_config
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.errors import (
    MessageGenerationError,
    MissingDomainError,
    NoProductSelectedError,
    OutreachConfigError,
    PlanCompileError,
    UnknownModeError,
    UnknownTermError,
)
from leadforge.outreach.report import build_report, render_json, render_markdown
from leadforge.outreach.runtime import build_service
from leadforge.outreach.search_plan import SearchPlan, parse_request
from leadforge.outreach.tables import OutreachSearch
from leadforge.outreach.tick import tick
from leadforge.outreach.usage.verdict import Strictness

__all__ = ["create_router"]

STATIC = Path(__file__).resolve().parent / "static"
_NAMED_ERRORS = (
    UnknownModeError,
    UnknownTermError,
    MissingDomainError,
    NoProductSelectedError,
    PlanCompileError,
    MessageGenerationError,
    UnknownCatalogKeyError,
    ValidationError,
)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    query: str
    domains: list[str] = []
    vendor: str | None = None
    products: list[str] = []


class SearchStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: SearchPlan


class TickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: uuid.UUID | None = None
    advance_days: float | None = None
    now: datetime | None = None


def _require_write_header(
    x_leadforge: Annotated[str | None, Header()] = None,
) -> None:
    if x_leadforge != "1":
        raise HTTPException(403, "missing X-LeadForge header")


def create_router(environ: Mapping[str, str] | None = None) -> APIRouter:
    """The outreach routes. ``environ`` defaults to the live process environment."""
    router = APIRouter()

    def env() -> Mapping[str, str]:
        return os.environ if environ is None else environ

    @contextmanager
    def reading() -> Iterator[Session]:
        try:
            engine = create_store_engine(environ=env())
            require_head(engine)
        except (ConfigurationError, DatabaseConfigError) as error:
            raise HTTPException(500, f"configuration error: {error}") from None
        except StoreNotMigratedError:
            raise HTTPException(409, "no data yet: run a search first") from None
        try:
            with Session(engine) as session, session.begin():
                yield session
        finally:
            engine.dispose()

    def cleanly(error: Exception) -> HTTPException:
        if isinstance(error, RunInProgressError):
            return HTTPException(409, str(error))
        if isinstance(error, ConfigurationError | OutreachConfigError):
            return HTTPException(500, f"configuration error: {error}")
        return HTTPException(400, str(error))

    @router.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        """Every option the search form renders, read from the catalog on each call."""
        try:
            vendors = load_catalog().vendors()
            roles = load_roles()
            usage = load_outreach_config().usage
        except (ConfigurationError, OutreachConfigError) as error:
            raise cleanly(error) from None

        def entries(items: Any) -> list[dict[str, str]]:
            return [{"key": p.key, "name": p.name or p.key} for p in items]

        return {
            "vendors": [
                {
                    "key": v.key,
                    "name": v.name,
                    "products": entries(v.products),
                    "ecosystem": entries(v.ecosystem),
                }
                for v in vendors
            ],
            "include_ecosystem": usage.include_ecosystem,
            "role_families": [f.key for f in roles.families],
            "seniorities": list(roles.seniority),
            "strictness": {
                "options": [s.value for s in Strictness],
                "default": usage.strictness.value,
            },
            "max_evidence_age_days": usage.max_evidence_age_days,
            "budget": usage.budget.model_dump(),
        }

    @router.post("/api/outreach/plan", dependencies=[Depends(_require_write_header)])
    def plan(request: PlanRequest) -> dict[str, Any]:
        """Compile a plan. Nothing is spent and nothing is stored."""
        try:
            built = build_service(env()).plan(
                parse_request(
                    request.mode,
                    request.query,
                    request.domains,
                    vendor=request.vendor,
                    products=request.products,
                )
            )
        except (*_NAMED_ERRORS, OutreachConfigError, ConfigurationError) as error:
            raise cleanly(error) from None
        return {"plan": built.model_dump(mode="json")}

    @router.post(
        "/api/outreach/searches", dependencies=[Depends(_require_write_header)]
    )
    def start(request: SearchStart) -> dict[str, Any]:
        """Run a shown plan: ingest, select, write, first tick."""
        try:
            service = build_service(env())
            summary = asyncio.run(service.run(request.plan, show=lambda _: None))
        except (
            *_NAMED_ERRORS,
            OutreachConfigError,
            ConfigurationError,
            RunInProgressError,
        ) as error:
            raise cleanly(error) from None
        return {
            "search_id": str(summary.search_id),
            "gathered": summary.gathered,
            "counts": dict(summary.counts),
            "invited": summary.invited,
            "notes": list(summary.notes),
            "outbox": str(service.outbox_path),
        }

    @router.get("/api/outreach/searches")
    def searches() -> dict[str, Any]:
        with reading() as session:
            rows = session.scalars(
                select(OutreachSearch).order_by(OutreachSearch.created_at.desc())
            ).all()
            out = []
            for row in rows:
                funnel = build_report(session, row.id).funnel
                out.append(
                    {
                        "search_id": str(row.id),
                        "mode": row.mode,
                        "query": row.query,
                        "compiler": row.compiler,
                        "status": row.status,
                        "created_at": row.created_at.isoformat(),
                        "funnel": funnel.model_dump(),
                    }
                )
        return {"searches": out}

    @router.get("/api/outreach/searches/{search_id}")
    def detail(search_id: uuid.UUID, reveal: bool = False) -> dict[str, Any]:
        with reading() as session:
            if session.get(OutreachSearch, search_id) is None:
                raise HTTPException(404, f"no search {search_id}")
            return {
                "report": build_report(session, search_id, reveal=reveal).model_dump(
                    mode="json"
                )
            }

    @router.get("/api/outreach/searches/{search_id}/report")
    def report(
        search_id: uuid.UUID,
        fmt: Annotated[str, Query(alias="format", pattern="^(md|json)$")] = "md",
        reveal: bool = False,
    ) -> Response:
        with reading() as session:
            if session.get(OutreachSearch, search_id) is None:
                raise HTTPException(404, f"no search {search_id}")
            built = build_report(session, search_id, reveal=reveal)
        if fmt == "json":
            return Response(render_json(built), media_type="application/json")
        return PlainTextResponse(render_markdown(built), media_type="text/markdown")

    @router.post("/api/outreach/tick", dependencies=[Depends(_require_write_header)])
    def advance(request: TickRequest) -> dict[str, Any]:
        """Advance one search (or every search) by days or to a time."""
        if request.advance_days is not None and request.now is not None:
            raise HTTPException(400, "give advance_days or now, not both")
        if request.advance_days is not None and request.advance_days < 0:
            raise HTTPException(400, "advance_days must not be negative")
        try:
            config: OutreachConfig = load_outreach_config()
        except OutreachConfigError as error:
            raise cleanly(error) from None
        start_at = request.now or datetime.now(UTC)
        when = start_at.replace(tzinfo=start_at.tzinfo or UTC) + timedelta(
            days=request.advance_days or 0
        )
        lines: list[str] = []
        dispatcher = DryRunDispatcher(config.outbox_path, lines.append)
        with reading() as session:
            ids = (
                [request.search_id]
                if request.search_id
                else list(session.scalars(select(OutreachSearch.id)))
            )
            fired = sum(
                len(
                    tick(
                        session,
                        search_id,
                        when,
                        cfg=config.triggers,
                        acceptance=SeededAcceptance(config.simulation),
                        dispatcher=dispatcher,
                    )
                )
                for search_id in ids
            )
        return {"at": when.isoformat(), "fired": fired}

    @router.get("/outreach")
    def page() -> FileResponse:
        return FileResponse(STATIC / "outreach.html")

    @router.get("/outreach/app.js")
    def script() -> FileResponse:
        return FileResponse(STATIC / "outreach.js", media_type="text/javascript")

    @router.get("/outreach/app.css")
    def styles() -> FileResponse:
        return FileResponse(STATIC / "outreach.css", media_type="text/css")

    return router
