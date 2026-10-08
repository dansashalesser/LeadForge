"""The outreach web API and its page (requirement 13.4).

A router under ``/api/outreach`` and a page at ``/outreach``, added to the ingestion web
app by the root (``leadforge.cli``): ingestion never imports this. Writes need the same
``X-LeadForge`` header as the ingestion API. Reads open their own engine on the
configured store, mask contact identifiers unless ``reveal`` is asked for, and never
migrate it. A search runs to completion inside the request (the demo's sources are
synthetic and quick); the UI shows a busy state until it returns.
"""

import asyncio
import hashlib
import os
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.catalog import (
    CatalogError,
    UnknownCatalogKeyError,
    load_catalog,
    load_roles,
)
from leadforge.lead_ingestion.database import (
    DatabaseConfigError,
    create_store_engine,
    local_file_url,
)
from leadforge.lead_ingestion.demo.cli import DEFAULT_DB
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import RunInProgressError
from leadforge.lead_ingestion.store.migrate import StoreNotMigratedError, require_head
from leadforge.lead_ingestion.web.api import STATIC as DASHBOARD_STATIC
from leadforge.outreach.acceptance import SeededAcceptance
from leadforge.outreach.config import OutreachConfig, load_outreach_config
from leadforge.outreach.demo_service import (
    DEMO_OUTBOX,
    ENV_LOCK,
    build_demo_service,
    demo_acceptance,
    demo_search,
)
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.errors import (
    MessageGenerationError,
    MissingDomainError,
    NoProductSelectedError,
    OutreachConfigError,
    PlanCompileError,
    UnknownLeadError,
    UnknownModeError,
    UnknownSearchError,
    UnknownTermError,
)
from leadforge.outreach.report import (
    XLSX_TYPE,
    build_report,
    render_json,
    render_markdown,
    render_xlsx,
)
from leadforge.outreach.runtime import build_service, source_mode_notes
from leadforge.outreach.search_plan import SearchPlan, parse_request
from leadforge.outreach.tables import OutreachSearch
from leadforge.outreach.tick import tick
from leadforge.outreach.usage.drafts import (
    DraftExistsError,
    DraftNotFoundError,
    approve_draft,
)
from leadforge.outreach.usage.verdict import Strictness

__all__ = ["create_router"]

STATIC = Path(__file__).resolve().parent / "static"
Store = Literal["demo", "main"]
# The dashboard's extra tab: script before the dashboard's own, so it can register.
_TAB_SCRIPT = (
    '<script src="/outreach/app.js"></script>\n  <script src="/static/app.js">'
)
_TAB_STYLE = '<link rel="stylesheet" href="/outreach/app.css">\n</head>'


def _versioned(html: str) -> str:
    """Tag each dashboard asset URL with a digest of its file.

    The server sends no cache headers, so a browser may keep an old ``app.js`` that
    ignores the Search tab; a URL that changes with the file cannot be stale."""
    assets = {
        "/static/app.js": DASHBOARD_STATIC / "app.js",
        "/static/app.css": DASHBOARD_STATIC / "app.css",
        "/outreach/app.js": STATIC / "outreach.js",
        "/outreach/app.css": STATIC / "outreach.css",
    }
    for url, path in assets.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
        html = html.replace(f'"{url}"', f'"{url}?v={digest}"')
    return html


_NAMED_ERRORS = (
    UnknownModeError,
    UnknownTermError,
    MissingDomainError,
    NoProductSelectedError,
    PlanCompileError,
    MessageGenerationError,
    UnknownCatalogKeyError,
    CatalogError,
    ValidationError,
)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    query: str
    domains: list[str] = []
    vendor: str | None = None
    products: list[str] = []
    store: Store = "main"


class SearchStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: SearchPlan
    store: Store = "main"


class MessagesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store: Store = "main"
    # The search the Lead was found by. Its usage evidence is what makes a Message
    # specific; without it the Message has only the Lead's own data to work from.
    search_id: uuid.UUID | None = None


class TickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: uuid.UUID | None = None
    advance_days: float | None = None
    now: datetime | None = None
    store: Store = "main"


def _require_write_header(
    x_leadforge: Annotated[str | None, Header()] = None,
) -> None:
    if x_leadforge != "1":
        raise HTTPException(403, "missing X-LeadForge header")


def create_router(
    environ: Mapping[str, str] | None = None, demo_db: Path | None = None
) -> APIRouter:
    """The outreach routes. ``environ`` defaults to the live process environment.

    ``demo_db`` is the demo store (default: the one the dashboard's demo uses). Every
    route takes a ``store``: ``main`` is the configured ``DATABASE_URL``, ``demo`` is
    the synthetic dataset in its own database.
    """
    router = APIRouter()

    def env() -> Mapping[str, str]:
        return os.environ if environ is None else environ

    def demo_path() -> Path:
        return (demo_db or DEFAULT_DB).resolve()

    @contextmanager
    def reading(store: Store = "main") -> Iterator[Session]:
        try:
            if store == "demo":
                path = demo_path()
                if not path.exists():
                    raise HTTPException(
                        409, "no demo searches yet: run a search on the demo dataset"
                    )
                engine = create_store_engine(local_file_url(path))
            else:
                # Waits out a demo search, which points the environment at its store.
                with ENV_LOCK:
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

    @router.post(
        "/api/catalog/drafts/{key}/approve",
        dependencies=[Depends(_require_write_header)],
    )
    def approve(key: str) -> dict[str, str]:
        """Move a drafted vendor into the catalog so searches may use it."""
        try:
            approve_draft(key)
        except DraftNotFoundError as error:
            raise HTTPException(404, str(error)) from None
        except DraftExistsError as error:
            raise HTTPException(409, str(error)) from None
        except (CatalogError, ConfigurationError) as error:
            raise cleanly(error) from None
        return {"approved": key}

    def run_notes(store: Store) -> list[str]:
        """What each source would run as, in the words of the post-run notes."""
        if store == "demo":
            return [
                "demo dataset: every source runs synthetic, nothing leaves this machine"
            ]
        with ENV_LOCK:
            modes = source_mode_notes(env())
        return [f"source {name}: {mode}" for name, mode in modes]

    @router.post("/api/outreach/plan", dependencies=[Depends(_require_write_header)])
    def plan(request: PlanRequest) -> dict[str, Any]:
        """Compile a plan. Nothing is spent and nothing is stored."""
        try:
            parsed = parse_request(
                request.mode,
                request.query,
                request.domains,
                vendor=request.vendor,
                products=request.products,
            )
            if request.store == "demo":
                with demo_search(demo_path()) as service:
                    built = service.plan(parsed)
            else:
                with ENV_LOCK:
                    built = build_service(env()).plan(parsed)
            notes = run_notes(request.store)
        except (
            *_NAMED_ERRORS,
            OutreachConfigError,
            ConfigurationError,
            ValueError,
        ) as error:
            raise cleanly(error) from None
        return {
            "plan": built.model_dump(mode="json"),
            "store": request.store,
            "notes": notes,
            "live_sources": [n for n in notes if n.endswith(": live")],
        }

    @router.post(
        "/api/outreach/searches", dependencies=[Depends(_require_write_header)]
    )
    def start(request: SearchStart) -> dict[str, Any]:
        """Run a shown plan: ingest, select, write, first tick."""
        try:
            if request.store == "demo":
                with demo_search(demo_path()) as service:
                    summary = asyncio.run(
                        service.run(request.plan, show=lambda _: None)
                    )
            else:
                with ENV_LOCK:
                    service = build_service(env())
                    summary = asyncio.run(
                        service.run(request.plan, show=lambda _: None)
                    )
        except (
            *_NAMED_ERRORS,
            OutreachConfigError,
            ConfigurationError,
            RunInProgressError,
        ) as error:
            raise cleanly(error) from None
        return {
            "search_id": str(summary.search_id),
            "store": request.store,
            "gathered": summary.gathered,
            "counts": dict(summary.counts),
            "invited": summary.invited,
            "notes": list(summary.notes),
            "outbox": str(
                demo_path().parent / DEMO_OUTBOX
                if request.store == "demo"
                else service.outbox_path
            ),
        }

    @router.get("/api/outreach/searches")
    def searches(store: Store = "main") -> dict[str, Any]:
        with reading(store) as session:
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
    def detail(
        search_id: uuid.UUID, reveal: bool = False, store: Store = "main"
    ) -> dict[str, Any]:
        with reading(store) as session:
            if session.get(OutreachSearch, search_id) is None:
                raise HTTPException(404, f"no search {search_id}")
            return {
                "report": build_report(session, search_id, reveal=reveal).model_dump(
                    mode="json"
                )
            }

    @router.post(
        "/api/outreach/leads/{lead_id}/messages",
        dependencies=[Depends(_require_write_header)],
    )
    def write_messages(lead_id: uuid.UUID, request: MessagesRequest) -> dict[str, Any]:
        """Write a Lead's invite and email now and return them; nothing is stored."""
        try:
            with reading(request.store) as session:
                if request.store == "demo":
                    demo_engine = create_store_engine(local_file_url(demo_path()))
                    try:
                        service = build_demo_service(demo_engine, demo_path())
                    finally:
                        demo_engine.dispose()
                else:
                    with ENV_LOCK:
                        service = build_service(env())
                facts = service.lead_facts(session, lead_id, request.search_id)
            # Written after the session closes: a slow model call holds no
            # connection or transaction.
            result = service.draft_messages(facts)
        except (UnknownLeadError, UnknownSearchError) as error:
            raise HTTPException(404, str(error)) from None
        except (*_NAMED_ERRORS, OutreachConfigError, ConfigurationError) as error:
            raise cleanly(error) from None
        return {
            "lead_id": str(lead_id),
            "generator": "model" if service.uses_model else "offline",
            "drafts": [
                {
                    "kind": draft.kind,
                    "subject": draft.subject,
                    "body": draft.body,
                    "checks_passed": all(c.passed for c in checks),
                    "failed_checks": [c.name for c in checks if not c.passed],
                }
                for draft, checks in result.drafts
            ],
            "failures": [{"name": c.name, "detail": c.detail} for c in result.failures],
        }

    @router.get("/api/outreach/searches/{search_id}/report")
    def report(
        search_id: uuid.UUID,
        fmt: Annotated[str, Query(alias="format", pattern="^(md|json|xlsx)$")] = "md",
        reveal: bool = False,
        store: Store = "main",
    ) -> Response:
        with reading(store) as session:
            if session.get(OutreachSearch, search_id) is None:
                raise HTTPException(404, f"no search {search_id}")
            built = build_report(session, search_id, reveal=reveal)
        if fmt == "json":
            return Response(render_json(built), media_type="application/json")
        if fmt == "xlsx":
            name = f"search-{built.mode}-{str(search_id)[:8]}.xlsx"
            return Response(
                render_xlsx(built),
                media_type=XLSX_TYPE,
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
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
        outbox = (
            demo_path().parent / DEMO_OUTBOX
            if request.store == "demo"
            else config.outbox_path
        )
        dispatcher = DryRunDispatcher(outbox, lines.append)
        with reading(request.store) as session:
            acceptance = (
                demo_acceptance(session.get_bind(), config)  # type: ignore[arg-type]
                if request.store == "demo"
                else SeededAcceptance(config.simulation)
            )
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
                        acceptance=acceptance,
                        dispatcher=dispatcher,
                    )
                )
                for search_id in ids
            )
        return {"at": when.isoformat(), "fired": fired}

    @router.get("/")
    def dashboard() -> HTMLResponse:
        """The ingestion dashboard with the Search tab added (first, the default)."""
        html = (DASHBOARD_STATIC / "index.html").read_text(encoding="utf-8")
        html = html.replace('<script src="/static/app.js">', _TAB_SCRIPT, 1)
        html = html.replace("</head>", _TAB_STYLE, 1)
        return HTMLResponse(_versioned(html), headers={"Cache-Control": "no-store"})

    @router.get("/outreach")
    def page() -> RedirectResponse:
        """The old page: search now lives in the dashboard's first tab."""
        return RedirectResponse("/?tab=search")

    @router.get("/outreach/app.js")
    def script() -> FileResponse:
        return FileResponse(STATIC / "outreach.js", media_type="text/javascript")

    @router.get("/outreach/app.css")
    def styles() -> FileResponse:
        return FileResponse(STATIC / "outreach.css", media_type="text/css")

    return router
