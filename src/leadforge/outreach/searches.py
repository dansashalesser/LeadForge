"""Record each search and link its ingestion run (requirements 5.1, 5.2).

The search row (mode, query, compiled plan, compiler, start time) is written before the
run starts, so it exists before the first provider request. ``link_hook`` gives the run
a callback that links the run's id to the search as soon as the run record exists, still
before any source is built or called.
"""

import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.outreach.search_plan import SearchPlan
from leadforge.outreach.tables import OutreachSearch

__all__ = ["SearchStatus", "finish_search", "link_hook", "link_run", "start_search"]

SearchStatus = Literal["planned", "running", "done", "failed"]


def start_search(session: Session, plan: SearchPlan, *, now: datetime) -> uuid.UUID:
    """Store the search for ``plan``; the caller commits."""
    row = OutreachSearch(
        mode=plan.mode,
        query=plan.query,
        plan=plan.model_dump(mode="json"),
        compiler=plan.compiler,
        ingestion_run_id=None,
        status="planned",
        created_at=now,
    )
    session.add(row)
    session.flush()
    return row.id


def link_run(session: Session, search_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Point the search at its ingestion run and mark it running; the caller commits."""
    row = session.get_one(OutreachSearch, search_id)
    if row.ingestion_run_id is not None:
        raise ValueError(f"search {search_id} is already linked to a run")
    row.ingestion_run_id = run_id
    row.status = "running"


def finish_search(
    session: Session, search_id: uuid.UUID, status: Literal["done", "failed"]
) -> None:
    """Close the search; the caller commits."""
    session.get_one(OutreachSearch, search_id).status = status


def link_hook(engine: Engine, search_id: uuid.UUID) -> Callable[[uuid.UUID], None]:
    """An ``on_run_started`` callback that links the run in its own transaction."""

    def link(run_id: uuid.UUID) -> None:
        with Session(engine) as session, session.begin():
            link_run(session, search_id, run_id)

    return link
