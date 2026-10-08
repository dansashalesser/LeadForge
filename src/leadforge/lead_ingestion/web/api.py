"""The web UI's JSON API and its static page (user request 2026-10-07).

Two stores are offered: ``main`` (the operator's store, as ``leadforge ingest`` and
``leadforge leads`` resolve it) and ``demo`` (``.leadforge/demo.db``, as ``leadforge
demo`` uses). Every read names its store explicitly and opens its own engine, so a
running job's environment changes never move a read.

Contact identifiers are masked as the CLI masks them unless the request asks for
``reveal``; the UI's toggle maps to it. Nothing here logs a lead.

The server is meant for the operator's own machine: it binds to localhost (see
``web.cli``), refuses other ``Host`` headers (DNS rebinding) and takes a write only
with the ``X-LeadForge`` header, which a cross-site form cannot send without a CORS
preflight this app never answers.
"""

import json
import os
import uuid
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from leadforge.lead_ingestion.database import (
    DatabaseConfigError,
    create_store_engine,
    local_file_path,
    local_file_url,
    redact_url,
    resolve_database_url,
)
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.demo.cli import RUN_LOG
from leadforge.lead_ingestion.demo.scorecard import person_results, score
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import GLOBAL_MODE_VARIABLE
from leadforge.lead_ingestion.log_redaction import MASK
from leadforge.lead_ingestion.match_keys import normalize_linkedin_url
from leadforge.lead_ingestion.mode_resolution import make_mode_resolver
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.source_settings import load_source_settings
from leadforge.lead_ingestion.store.lead_reader import StoredLead, list_leads, load_lead
from leadforge.lead_ingestion.store.migrate import StoreNotMigratedError, require_head
from leadforge.lead_ingestion.store.models import IngestionRun, SourceRun
from leadforge.lead_ingestion.web.export import XLSX_TYPE, leads_xlsx
from leadforge.lead_ingestion.web.jobs import JobBusyError, JobKind, JobRunner

__all__ = ["create_app"]

STATIC = Path(__file__).parent / "static"
_PAGE = 500
_MAX_LEADS = 20_000
_RUNS = 50
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


class JobRequest(BaseModel):
    kind: JobKind
    fresh: bool = False
    faults: bool = False


# --------------------------------------------------------------------- masking


def _email(address: object, reveal: bool) -> str | None:
    if address is None:
        return None
    text = str(address)
    if reveal:
        return text
    local, _, domain = text.rpartition("@")
    return f"{local[:1]}{MASK}@{domain}"


def _linkedin(url: object, reveal: bool) -> str | None:
    if url is None:
        return None
    if reveal:
        return str(url)
    host = (normalize_linkedin_url(str(url)) or "").partition("/")[0]
    return f"{host}/{MASK}"


def lead_json(stored: StoredLead, reveal: bool) -> dict[str, Any]:
    """One stored lead for the UI, contact identifiers masked unless ``reveal``."""
    lead = stored.lead.model_dump(mode="json")
    lead["email"] = _email(stored.lead.email, reveal)
    lead["linkedin_url"] = _linkedin(stored.lead.linkedin_url, reveal)
    lead["role_contact_emails"] = [
        _email(e, reveal) for e in stored.lead.role_contact_emails
    ]
    return {
        "lead_id": str(stored.lead_id),
        "lead": lead,
        "retired_at": stored.retired_at and stored.retired_at.isoformat(),
        "successor_ids": [str(s) for s in stored.successor_ids],
        "contributing_sources": list(stored.contributing_sources),
        "agreement": dict(stored.agreement),
        "provenance": [p.model_dump(mode="json") for p in stored.provenance],
        "primary_domain": stored.primary_domain,
        "primary_domain_source": stored.primary_domain_source
        and stored.primary_domain_source.value,
        "primary_domain_flagged": stored.primary_domain_flagged,
        "projection_version": stored.projection_version,
        "stale": stored.stale,
        "computed_at": stored.computed_at.isoformat(),
        "web_evidence": [
            {
                "source_name": w.source_name,
                "fetched_at": w.fetched_at.isoformat(),
                "attachment": w.attachment.value,
                "domains": list(w.domains),
                "values": jsonable_encoder(dict(w.values)),
            }
            for w in stored.web_evidence
        ],
    }


def _mask_expect(expect: dict[str, Any], reveal: bool) -> dict[str, Any]:
    out = dict(expect)
    if "email" in out:
        out["email"] = _email(out["email"], reveal)
    if "linkedin_url" in out:
        out["linkedin_url"] = _linkedin(out["linkedin_url"], reveal)
    return out


def _exists(url: sa.URL) -> bool:
    """False only for a local file store whose file is not there yet."""
    path = local_file_path(url)
    return path is None or path.exists()


# --------------------------------------------------------------------- app


def create_app(
    *,
    main_url: str | None,
    demo_db: Path,
    extra_routers: Sequence[APIRouter] = (),
) -> FastAPI:
    """The UI server. ``main_url`` None means the local default store.

    ``extra_routers`` are mounted as given, before the catch-all store routes, so a
    root that composes another slice can add its own paths without this slice knowing
    it exists.
    """
    demo_db = demo_db.resolve()
    demo_url = local_file_url(demo_db)
    urls = {
        "main": resolve_database_url({"DATABASE_URL": main_url or ""}),
        "demo": resolve_database_url({"DATABASE_URL": demo_url}),
    }
    runner = JobRunner(main_url=main_url, demo_url=demo_url, demo_db=demo_db)
    app = FastAPI(title="LeadForge", docs_url=None, redoc_url=None)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=[*LOCAL_HOSTS, "testserver"]
    )

    def store_url(store: str) -> sa.URL:
        if store not in urls:
            raise HTTPException(404, f"unknown store {store!r}; use main or demo")
        return urls[store]

    @contextmanager
    def session_for(store: str) -> Iterator[Session]:
        url = store_url(store)
        no_data = HTTPException(409, "this store has no data yet: run an ingestion")
        # A read never creates the store's file (or its directory).
        if not _exists(url):
            raise no_data
        try:
            engine = create_store_engine(url)
        except (ConfigurationError, DatabaseConfigError) as error:
            raise HTTPException(500, f"configuration error: {error}") from None
        try:
            try:
                require_head(engine)
            except StoreNotMigratedError:
                raise no_data from None
            with Session(engine) as session:
                yield session
        finally:
            engine.dispose()

    def all_leads(
        session: Session, include_retired: bool, cap: int | None = _MAX_LEADS
    ) -> list[StoredLead]:
        """The store's leads, page by page; ``cap=None`` reads every one."""
        leads: list[StoredLead] = []
        after = None
        while (cap is None or len(leads) < cap) and (
            page := list_leads(
                session, include_retired=include_retired, limit=_PAGE, after=after
            )
        ):
            leads.extend(page)
            after = page[-1].lead_id
        return leads

    def require_write_header(
        x_leadforge: Annotated[str | None, Header()] = None,
    ) -> None:
        if x_leadforge != "1":
            raise HTTPException(403, "missing X-LeadForge header")

    for router in extra_routers:
        app.include_router(router)

    @app.get("/api/stores")
    def stores() -> dict[str, Any]:
        return {
            "stores": [
                {
                    "key": key,
                    "url": redact_url(url),
                    "exists": _exists(url),
                }
                for key, url in urls.items()
            ]
        }

    @app.get("/api/sources")
    def sources() -> dict[str, Any]:
        """What ``leadforge ingest`` would run each source as, right now."""
        try:
            registry = SourceRegistry.discover(config=load_source_settings())
            resolver = make_mode_resolver(
                os.environ, global_override=os.environ.get(GLOBAL_MODE_VARIABLE)
            )
            described = registry.describe(os.environ, resolve_mode=resolver)
        except (ConfigurationError, ValueError) as error:
            raise HTTPException(500, f"configuration error: {error}") from None
        return {"sources": [d.to_dict() for d in described]}

    @app.get("/api/{store}/leads")
    def leads(
        store: str,
        reveal: bool = False,
        include_retired: bool = False,
    ) -> dict[str, Any]:
        with session_for(store) as session:
            found = all_leads(session, include_retired)
        return {
            "leads": [lead_json(s, reveal) for s in found],
            "truncated": len(found) >= _MAX_LEADS,
        }

    @app.get("/api/{store}/leads.xlsx")
    def leads_workbook(
        store: str,
        reveal: bool = False,
        include_retired: bool = False,
    ) -> Response:
        # A download is the whole store, never the first page of it.
        with session_for(store) as session:
            found = all_leads(session, include_retired, cap=None)
        return Response(
            leads_xlsx(lead_json(s, reveal) for s in found),
            media_type=XLSX_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="{store}-leads.xlsx"'
            },
        )

    @app.get("/api/{store}/leads/{lead_id}")
    def lead(store: str, lead_id: uuid.UUID, reveal: bool = False) -> dict[str, Any]:
        with session_for(store) as session:
            stored = load_lead(session, lead_id)
        if stored is None:
            raise HTTPException(404, f"no lead {lead_id}")
        return lead_json(stored, reveal)

    @app.get("/api/{store}/runs")
    def runs(store: str) -> dict[str, Any]:
        with session_for(store) as session:
            rows = session.scalars(
                sa.select(IngestionRun)
                .order_by(IngestionRun.started_at.desc())
                .limit(_RUNS)
            ).all()
            per_run: dict[uuid.UUID, list[SourceRun]] = {r.id: [] for r in rows}
            for s in session.scalars(
                sa.select(SourceRun)
                .where(SourceRun.run_id.in_(list(per_run)))
                .order_by(SourceRun.source_name)
            ):
                per_run[s.run_id].append(s)
            return {
                "runs": [
                    {
                        "run_id": str(r.id),
                        "started_at": r.started_at.isoformat(),
                        "finished_at": r.finished_at and r.finished_at.isoformat(),
                        "status": r.status,
                        "exit_code": r.exit_code,
                        "failure_reason": r.failure_reason,
                        "leads_merged": r.leads_merged,
                        "leads_retired": r.leads_retired,
                        "primary_domain_ties_flagged": r.primary_domain_ties_flagged,
                        "projection_version": r.projection_version,
                        "sources": [
                            {
                                "source_name": s.source_name,
                                "mode": s.resolved_mode,
                                "mode_reason": s.mode_reason,
                                "leads_found": s.leads_found,
                                "contributions_written": s.contributions_written,
                                "failure_class": s.failure_class,
                                "attempted": s.attempted,
                                "succeeded": s.succeeded,
                                "failed": s.failed,
                                "retries": s.retries,
                                "throttle_waits": s.throttle_waits,
                                "http_429_count": s.http_429_count,
                                "records_fetched": s.records_fetched,
                                "credits_consumed": s.credits_consumed
                                and str(s.credits_consumed),
                                "quota_remaining": s.quota_remaining,
                                "warnings": s.warnings,
                            }
                            for s in per_run[r.id]
                        ],
                    }
                    for r in rows
                ]
            }

    @app.get("/api/demo/scorecard")
    def scorecard(reveal: bool = False) -> dict[str, Any]:
        key = generator.load(generator.ANSWER_KEY)
        run_log = demo_db.parent / RUN_LOG
        log = (
            json.loads(run_log.read_text(encoding="utf-8")) if run_log.exists() else {}
        )
        with session_for("demo") as session:
            found = all_leads(session, include_retired=False)
        card = score(found, key, log)
        people = {p["subject"]: p for p in key["people"]}
        persons = []
        for p in person_results(found, key, log):
            entry = people[p.subject]
            persons.append(
                {
                    "subject": p.subject,
                    "scenario": p.scenario,
                    "company": p.company,
                    "name": entry.get("name"),
                    "lead_id": p.lead and str(p.lead.lead_id),
                    "expect": _mask_expect(entry["expect"], reveal),
                    "checks": [{"check": c, "outcome": o} for c, o in p.checks],
                }
            )
        return {
            "scenarios": [
                {
                    "name": name,
                    "note": card.notes.get(name, ""),
                    "people": r.people,
                    "passed": r.passed,
                    "failed": dict(r.failed),
                    "skipped": dict(r.skipped),
                }
                for name, r in card.scenarios.items()
            ],
            "checks_passed": card.checks_passed,
            "checks_failed": card.checks_failed,
            "leads_active": card.leads_active,
            "leads_expected": card.leads_expected,
            "leads_without_subject": card.leads_without_subject,
            "shared_address_leads": card.shared_address_leads,
            "requests": card.requests,
            "companies": card.companies,
            "companies_searched": card.companies_searched,
            "check_failures": dict(
                sum(
                    (Counter(r.failed) for r in card.scenarios.values()),
                    Counter[str](),
                )
            ),
            "persons": persons,
        }

    @app.get("/api/jobs/current")
    def current_job() -> dict[str, Any]:
        job = runner.current
        return {"job": job and job.as_json()}

    @app.post("/api/jobs", dependencies=[Depends(require_write_header)])
    def start_job(request: JobRequest) -> dict[str, Any]:
        try:
            job = runner.start(request.kind, fresh=request.fresh, faults=request.faults)
        except JobBusyError as error:
            raise HTTPException(409, str(error)) from None
        return {"job": job.as_json()}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
