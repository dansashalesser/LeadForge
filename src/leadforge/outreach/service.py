"""Run one whole search: plan, ingest, select, write, first tick (1.4, 14.3).

``plan`` compiles the request and spends nothing; the caller shows it. ``run`` then
stores the search, runs ingestion on the plan's in-memory profile, gives every gathered
Lead one Decision, writes and checks Messages for the selected ones (a Lead whose
Messages never pass becomes ``manual_review`` with none), and ticks once at the clock's
time so each selected Lead's invite fires.

With no keys at all it still runs: sources are synthetic, the compiler and the writer
are offline, and the judge is off. The summary says which, so a demo is never mistaken
for live data (14.3).
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.catalog import (
    Catalog,
    CatalogVendor,
    UnknownCatalogKeyError,
    load_roles,
)
from leadforge.lead_ingestion.database import create_store_engine
from leadforge.lead_ingestion.ingest_runner import IngestionOutcome, run_ingestion
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.store.lead_reader import (
    StoredLead,
    crm_state,
    list_leads,
)
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.outreach.acceptance import AcceptanceSource
from leadforge.outreach.clock import Clock
from leadforge.outreach.company_plans import users_plan, workers_plan
from leadforge.outreach.compile_llm import LlmCompiler, PlanDraft
from leadforge.outreach.compile_offline import OfflineCompiler, phrases_of
from leadforge.outreach.config import OutreachConfig
from leadforge.outreach.decisions import Decision, Reason, record_decisions
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.errors import UsageClassifierUnavailableError
from leadforge.outreach.facts import build_facts
from leadforge.outreach.judge import Judge, RubricScores
from leadforge.outreach.llm import StructuredModel, build_chat_model, llm_settings
from leadforge.outreach.llm_messages import LlmWriter, WrittenMessage
from leadforge.outreach.message_checks import Check, Draft, all_passed, check_message
from leadforge.outreach.messages import record_messages
from leadforge.outreach.offline_messages import OfflineWriter
from leadforge.outreach.profile import catalog_base_profile, plan_to_profile
from leadforge.outreach.prompts import load_prompt
from leadforge.outreach.qualify import decide_all
from leadforge.outreach.search_plan import SearchPlan, SearchRequest
from leadforge.outreach.searches import finish_search, link_hook, start_search
from leadforge.outreach.tables import OutreachDecision
from leadforge.outreach.tick import tick
from leadforge.outreach.usage.budget import UsageBudget
from leadforge.outreach.usage.classify import OfflineClassifier
from leadforge.outreach.usage.demo_wiring import demo_usage_deps
from leadforge.outreach.usage.grade import CompanyGrade, CompanyUsage
from leadforge.outreach.usage.person import PersonFit, RoleVocabulary
from leadforge.outreach.usage.stage import (
    StageConfig,
    StageDeps,
    StageLead,
    run_usage_stage,
)
from leadforge.outreach.usage.store import UsageStore
from leadforge.outreach.usage.verdict import UsageVerdict, VerdictStatus

__all__ = ["SearchService", "SearchSummary"]

Ingest = Callable[..., Awaitable[IngestionOutcome]]
_PAGE = 200


class _UsageClassifier(Protocol):
    def classify(self, target: Any, product: Any, passages: Any) -> Any: ...
    def stamp(self, target: Any, product: Any, passages: Any) -> Any: ...


# Builds the stage's SERP client and page fetcher around the run's budget.
UsageDeps = Callable[[UsageBudget], tuple[Any, Any]]


@dataclass(frozen=True)
class SearchSummary:
    search_id: uuid.UUID
    run_id: uuid.UUID
    plan: SearchPlan
    gathered: int
    counts: Mapping[str, int]
    invited: int
    # What was synthetic or offline, in words.
    notes: tuple[str, ...]


class SearchService:
    def __init__(
        self,
        *,
        config: OutreachConfig,
        catalog: Catalog,
        environ: Mapping[str, str],
        clock: Clock,
        acceptance: AcceptanceSource,
        dispatcher: DryRunDispatcher,
        engine: Engine | None = None,
        ingest: Ingest = run_ingestion,
        usage_deps: UsageDeps | None = None,
        usage_classifier: _UsageClassifier | None = None,
    ) -> None:
        self._cfg = config
        self._catalog = catalog
        self._base = catalog_base_profile(catalog, config.sources)
        self._environ = environ
        self._clock = clock
        self._acceptance = acceptance
        self._dispatcher = dispatcher
        self._engine = engine
        self._ingest = ingest
        self._usage_deps = usage_deps
        self._usage_classifier = usage_classifier
        self._settings = llm_settings(environ, config.llm)

    @property
    def outbox_path(self) -> Path:
        return self._cfg.outbox_path

    @property
    def uses_model(self) -> bool:
        return self._settings is not None

    # ---------------------------------------------------------------- plan

    def plan(self, request: SearchRequest) -> SearchPlan:
        """Compile the request. No provider is called and nothing is stored."""
        if request.mode == "workers":
            return workers_plan(request, self._catalog)
        if request.mode == "users":
            return users_plan(request, self._catalog)
        compiler = self._free_text_compiler()
        return compiler.compile(request)

    def _free_text_compiler(self) -> LlmCompiler | OfflineCompiler:
        if self._settings is None:
            return OfflineCompiler(self._base)
        chat = build_chat_model(self._settings, self._environ)
        return LlmCompiler(
            StructuredModel(chat, PlanDraft),
            load_prompt("compile_search_v1"),
            self._base.terms(),
            retries=self._cfg.llm.compile_retries,
        )

    # ----------------------------------------------------------------- run

    async def run(
        self, plan: SearchPlan, *, show: Callable[[SearchPlan], None]
    ) -> SearchSummary:
        """Show the plan, then spend: ingest, select, write, first tick."""
        profile = plan_to_profile(plan, self._base, self._cfg.sources)
        show(plan)
        engine = self._engine or create_store_engine(environ=self._environ)
        upgrade_to_head(engine.url.render_as_string(hide_password=False))
        with Session(engine) as session, session.begin():
            search_id = start_search(session, plan, now=self._clock.now())
        try:
            outcome = await self._ingest(
                target_profile=profile, on_run_started=link_hook(engine, search_id)
            )
            summary = await self._after_ingestion(engine, search_id, plan, outcome)
        except BaseException:
            with Session(engine) as session, session.begin():
                finish_search(session, search_id, "failed")
            raise
        with Session(engine) as session, session.begin():
            finish_search(session, search_id, "done")
        return summary

    async def _after_ingestion(
        self,
        engine: Engine,
        search_id: uuid.UUID,
        plan: SearchPlan,
        outcome: IngestionOutcome,
    ) -> SearchSummary:
        now = self._clock.now()
        labels = tuple(
            phrase for t in plan.terms for phrase in phrases_of(self._base, t)
        )
        writer = self._writer()
        judge = self._judge()
        with Session(engine) as session:
            leads = _gather(session, outcome.run_id)
        verdicts = await self._usage_verdicts(engine, search_id, plan, outcome, leads)
        with Session(engine) as session, session.begin():
            crm = crm_state(session, [s.lead_id for s in leads])
            decisions = decide_all(
                leads, crm, plan, self._cfg.qualify, labels, verdicts=verdicts
            )
            by_id = {s.lead_id: s for s in leads}
            final: list[Decision] = []
            drafts: dict[uuid.UUID, list[tuple[Draft, tuple[Check, ...], Any]]] = {}
            for decision in decisions:
                if decision.status != "selected":
                    final.append(decision)
                    continue
                written = self._write(by_id[decision.lead_id], writer, judge)
                if written is None:
                    final.append(_manual_review(decision))
                else:
                    final.append(decision)
                    drafts[decision.lead_id] = written
            record_decisions(session, search_id, tuple(final), now=now)
            ids = {
                row.lead_id: row.id
                for row in session.query(OutreachDecision).filter_by(
                    search_id=search_id
                )
            }
            for lead_id, written in drafts.items():
                record_messages(session, ids[lead_id], tuple(written), now=now)
            fired = tick(
                session,
                search_id,
                now,
                cfg=self._cfg.triggers,
                acceptance=self._acceptance,
                dispatcher=self._dispatcher,
            )
            counts = _count(final)
        invited = sum(1 for f in fired if f.kind == "invite")
        return SearchSummary(
            search_id=search_id,
            run_id=outcome.run_id,
            plan=plan,
            gathered=len(leads),
            counts=counts,
            invited=invited,
            notes=self._notes(plan, outcome, judge),
        )

    # ------------------------------------------------------------- helpers

    async def _usage_verdicts(
        self,
        engine: Engine,
        search_id: uuid.UUID,
        plan: SearchPlan,
        outcome: IngestionOutcome,
        leads: list[StoredLead],
    ) -> dict[uuid.UUID, UsageVerdict] | None:
        """Usage verdicts for a users plan; None for any other mode (no gate)."""
        if plan.mode != "users":
            return None
        synthetic = all(r.resolved_mode is DataMode.SYNTHETIC for r in outcome.results)
        cfg = self._cfg.usage
        classifier = self._usage_classifier
        if classifier is None:
            if not synthetic:
                raise UsageClassifierUnavailableError
            classifier = OfflineClassifier(cfg.cues)
        build = self._usage_deps
        if build is None:
            if not synthetic:
                raise UsageClassifierUnavailableError
            build = partial(
                demo_usage_deps,
                max_bytes=cfg.fetch.max_bytes,
                passage_chars=cfg.fetch.passage_chars,
            )
        budget = UsageBudget(
            searches=cfg.budget.searches,
            fetches=cfg.budget.fetches,
            llm_calls=cfg.budget.llm_calls,
        )
        serp, fetcher = build(budget)
        roles = load_roles()
        stage_cfg = StageConfig(
            grade=cfg.grade_config(),
            strictness=cfg.strictness,
            include_ecosystem=cfg.include_ecosystem,
            roles=RoleVocabulary(roles.core, roles.adjacent, roles.irrelevant),
        )
        deps = StageDeps(
            serp=serp,
            fetcher=fetcher,
            classifier=classifier,
            store=UsageStore(engine),
            budget=budget,
            search_id=search_id,
            today=self._clock.now().date(),
        )
        got = await run_usage_stage(
            [_stage_lead(s) for s in leads],
            self._vendor(plan),
            plan.terms,
            deps,
            stage_cfg,
        )
        # The stage walks companies; a Lead with no employer has none to grade.
        return {s.lead_id: got.get(s.lead_id) or no_employer_verdict() for s in leads}

    def _vendor(self, plan: SearchPlan) -> CatalogVendor:
        for vendor in self._catalog.vendors():
            if vendor.name == plan.company:
                return vendor
        raise UnknownCatalogKeyError(f"unknown vendor: {plan.company}")

    def _writer(self) -> LlmWriter | OfflineWriter:
        if self._settings is None:
            return OfflineWriter()
        chat = build_chat_model(self._settings, self._environ)
        return LlmWriter(
            StructuredModel(chat, WrittenMessage),
            StructuredModel(chat, WrittenMessage),
            invite_prompt=load_prompt("invite_v1"),
            email_prompt=load_prompt("email_v1"),
            model_name=self._settings.model,
            cfg=self._cfg.messages,
        )

    def _judge(self) -> Judge:
        if self._settings is None:
            return Judge(None, load_prompt("judge_v1"))
        chat = build_chat_model(self._settings, self._environ)
        return Judge(StructuredModel(chat, RubricScores), load_prompt("judge_v1"))

    def _write(
        self, stored: StoredLead, writer: LlmWriter | OfflineWriter, judge: Judge
    ) -> list[tuple[Draft, tuple[Check, ...], Any]] | None:
        facts = build_facts(stored, self._cfg.messages)
        if isinstance(writer, OfflineWriter):
            drafts: tuple[Draft, ...] = writer.write(facts)
        else:
            outcome = writer.write(facts)
            if outcome.status != "ready":
                return None
            drafts = outcome.drafts
        out: list[tuple[Draft, tuple[Check, ...], Any]] = []
        for draft in drafts:
            checks = check_message(draft, facts, self._cfg.messages)
            if not all_passed(checks):
                return None
            out.append((draft, checks, judge.score(draft, facts)))
        return out

    def _notes(
        self, plan: SearchPlan, outcome: IngestionOutcome, judge: Judge
    ) -> tuple[str, ...]:
        notes = [f"compiler: {plan.compiler}"]
        notes += [
            f"source {r.source_name}: {r.resolved_mode.value}" for r in outcome.results
        ]
        notes.append(
            "messages: offline templates"
            if self._settings is None
            else f"messages: model {self._settings.model}"
        )
        notes.append("judge: on" if judge.active else "judge: off (no model key)")
        notes.append("dispatch: dry run, nothing is sent")
        return tuple(notes)


def _gather(session: Session, run_id: uuid.UUID) -> list[StoredLead]:
    leads: list[StoredLead] = []
    after: uuid.UUID | None = None
    while True:
        page = list_leads(session, run_id=run_id, limit=_PAGE, after=after)
        leads += page
        if len(page) < _PAGE:
            return leads
        after = page[-1].lead_id


def no_employer_verdict() -> UsageVerdict:
    """A Lead with no employer cannot be shown to use the product: rejected."""
    return UsageVerdict(
        status=VerdictStatus.REJECTED,
        reason="no_employer",
        company_usage=CompanyUsage(
            grade=CompanyGrade.UNVERIFIED, reason="no_employer", records=()
        ),
        person_fit=PersonFit("irrelevant"),
        evidence_refs=(),
    )


def _stage_lead(stored: StoredLead) -> StageLead:
    lead = stored.lead
    return StageLead(
        lead_id=stored.lead_id,
        lead=lead,
        role_fields=tuple(e.title for e in lead.employments if e.title),
        matched_uids=tuple(sorted({t.uid for t in lead.tech_signals if t.uid})),
    )


def _manual_review(decision: Decision) -> Decision:
    reason = Reason(code="messages_failed_checks", note="no Message passed its checks")
    return Decision(
        lead_id=decision.lead_id,
        status="manual_review",
        score=decision.score,
        reasons=(reason, *decision.reasons),
    )


def _count(decisions: list[Decision]) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(
        ("selected", "rejected", "needs_enrichment", "manual_review"), 0
    )
    for d in decisions:
        counts[d.status] += 1
    return counts
