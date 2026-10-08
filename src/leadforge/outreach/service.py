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
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
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
    load_lead,
)
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.outreach.acceptance import AcceptanceSource
from leadforge.outreach.clock import Clock
from leadforge.outreach.company_plans import users_plan, workers_plan
from leadforge.outreach.compile_llm import LlmCompiler, PlanDraft
from leadforge.outreach.compile_offline import (
    OfflineCompiler,
    _holds,
    _words,
    phrases_of,
)
from leadforge.outreach.config import OutreachConfig
from leadforge.outreach.decisions import Decision, Reason, record_decisions
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.errors import (
    MessageGenerationError,
    UnknownLeadError,
    UnknownSearchError,
    UsageClassifierUnavailableError,
)
from leadforge.outreach.facts import LeadFacts, UsageContext, build_facts
from leadforge.outreach.judge import Judge, RubricScores
from leadforge.outreach.llm import (
    LlmSettings,
    StructuredModel,
    build_chat_model,
    llm_settings,
)
from leadforge.outreach.llm_messages import LlmWriter, WrittenMessage
from leadforge.outreach.message_checks import Check, Draft, all_passed, check_message
from leadforge.outreach.messages import record_messages
from leadforge.outreach.offline_messages import OfflineWriter
from leadforge.outreach.profile import catalog_base_profile, plan_to_profile
from leadforge.outreach.prompts import load_prompt
from leadforge.outreach.qualify import decide_all
from leadforge.outreach.search_plan import SearchPlan, SearchRequest
from leadforge.outreach.searches import finish_search, link_hook, start_search
from leadforge.outreach.tables import OutreachDecision, OutreachSearch
from leadforge.outreach.tick import tick
from leadforge.outreach.usage.budget import UsageBudget
from leadforge.outreach.usage.demo_wiring import demo_usage_deps
from leadforge.outreach.usage.flow import classifier_for
from leadforge.outreach.usage.grade import CompanyGrade, CompanyUsage
from leadforge.outreach.usage.person import PersonFit, RoleVocabulary
from leadforge.outreach.usage.stage import (
    StageConfig,
    StageDeps,
    StageLead,
    cited_evidence,
    company_key_of,
    person_url_of,
    run_usage_stage,
)
from leadforge.outreach.usage.store import UsageStore, evidence_for
from leadforge.outreach.usage.verdict import UsageVerdict, VerdictStatus

__all__ = ["DraftResult", "SearchService", "SearchSummary"]

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


@dataclass(frozen=True)
class DraftResult:
    """Each written Message with its checks; nothing is stored or sent."""

    drafts: tuple[tuple[Draft, tuple[Check, ...]], ...]
    # When the model's Messages never passed: the checks its last attempt failed.
    failures: tuple[Check, ...] = ()


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
            return users_plan(
                request,
                self._catalog,
                include_ecosystem=self._cfg.usage.include_ecosystem,
            )
        compiler = self._free_text_compiler()
        return compiler.compile(request)

    def _free_text_compiler(self) -> LlmCompiler | OfflineCompiler:
        if self._settings is None:
            return OfflineCompiler(self._base)
        chat = self._chat(self._settings)
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
            leads = _gather(session, outcome.run_id, plan, labels)
        verdicts = await self._usage_verdicts(engine, search_id, plan, outcome, leads)
        with Session(engine) as session:
            crm = crm_state(session, [s.lead_id for s in leads])
        decisions = decide_all(
            leads, crm, plan, self._cfg.qualify, labels, verdicts=verdicts
        )
        # The model is called with no session open, so a slow call holds no
        # connection or transaction.
        final, drafts, model_errors = self._write_selected(
            decisions, leads, writer, judge, plan, verdicts
        )
        with Session(engine) as session, session.begin():
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
            notes=self._notes(plan, outcome, judge, model_errors),
        )

    def _write_selected(
        self,
        decisions: Collection[Decision],
        leads: list[StoredLead],
        writer: LlmWriter | OfflineWriter,
        judge: Judge,
        plan: SearchPlan,
        verdicts: Mapping[uuid.UUID, UsageVerdict] | None,
    ) -> tuple[
        list[Decision],
        dict[uuid.UUID, list[tuple[Draft, tuple[Check, ...], Any]]],
        int,
    ]:
        """Messages for each selected Lead; a Lead with none goes to manual review.

        A failed model call (timeout, rate limit, overload) costs that one Lead its
        Messages, not the whole search. The count of such Leads is returned.
        """
        by_id = {s.lead_id: s for s in leads}
        final: list[Decision] = []
        drafts: dict[uuid.UUID, list[tuple[Draft, tuple[Check, ...], Any]]] = {}
        model_errors = 0
        for decision in decisions:
            if decision.status != "selected":
                final.append(decision)
                continue
            usage = self._usage_context(plan, (verdicts or {}).get(decision.lead_id))
            try:
                written = self._write(by_id[decision.lead_id], writer, judge, usage)
            except MessageGenerationError:
                model_errors += 1
                final.append(_manual_review(decision, _MODEL_FAILED))
                continue
            if written is None:
                final.append(_manual_review(decision, _CHECKS_FAILED))
            else:
                final.append(decision)
                drafts[decision.lead_id] = written
        return final, drafts, model_errors

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
            classifier = classifier_for(
                synthetic=synthetic, usage=cfg, llm=self._cfg.llm, environ=self._environ
            )
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

    def _usage_context(
        self, plan: SearchPlan, verdict: UsageVerdict | None
    ) -> UsageContext | None:
        """A Verdict's evidence with the catalog names for the product keys it cites."""
        if verdict is None:
            return None
        return UsageContext(
            evidence=verdict.evidence_refs, product_names=self._product_names(plan)
        )

    def _product_names(self, plan: SearchPlan) -> dict[str, str]:
        vendor = self._vendor(plan)
        return {
            product.key: product.name
            for product in vendor.products + vendor.ecosystem
            if product.name
        }

    def _stored_usage(
        self, session: Session, search_id: uuid.UUID, stored: StoredLead
    ) -> UsageContext | None:
        """The usage evidence a past search's Verdict cited for this Lead, if any.

        The Verdict itself is not kept per Lead, but the Evidence Records behind it are,
        and grading them again picks the ones it cited, so a Message written later
        says what one written during the run would have said. A search that ran no
        usage stage has none, and the Lead's own facts carry it.
        """
        row = session.get(OutreachSearch, search_id)
        if row is None:
            raise UnknownSearchError(f"no search {search_id}")
        key = company_key_of(stored.lead)
        if key is None:
            return None
        records = evidence_for(session, search_id, key)
        if not records:
            return None
        plan = SearchPlan.model_validate(row.plan)
        usage = self._cfg.usage
        evidence = cited_evidence(
            records,
            self._vendor(plan),
            plan.terms,
            person_url_of(stored.lead_id, stored.lead),
            StageConfig(
                grade=usage.grade_config(), include_ecosystem=usage.include_ecosystem
            ),
            row.created_at.date(),
        )
        return UsageContext(evidence=evidence, product_names=self._product_names(plan))

    def _vendor(self, plan: SearchPlan) -> CatalogVendor:
        for vendor in self._catalog.vendors():
            if vendor.name == plan.company:
                return vendor
        raise UnknownCatalogKeyError(f"unknown vendor: {plan.company}")

    def _writer(self) -> LlmWriter | OfflineWriter:
        if self._settings is None:
            return OfflineWriter()
        chat = self._chat(self._settings)
        return LlmWriter(
            StructuredModel(chat, WrittenMessage),
            StructuredModel(chat, WrittenMessage),
            invite_prompt=load_prompt("invite_v2"),
            email_prompt=load_prompt("email_v2"),
            model_name=self._settings.model,
            cfg=self._cfg.messages,
        )

    def lead_facts(
        self,
        session: Session,
        lead_id: uuid.UUID,
        search_id: uuid.UUID | None = None,
    ) -> LeadFacts:
        """What a Message about this Lead may say, read on the caller's session.

        Given the search the Lead was found by, the facts carry the usage evidence
        that run's Verdict cited as well.
        """
        stored = load_lead(session, lead_id)
        if stored is None:
            raise UnknownLeadError(f"no lead {lead_id}")
        usage = (
            None
            if search_id is None
            else self._stored_usage(session, search_id, stored)
        )
        return build_facts(stored, self._cfg.messages, usage)

    def draft_messages(self, facts: LeadFacts) -> DraftResult:
        """Write a Lead's invite and email now, for display: nothing is stored or sent.

        Uses the model when one is configured, else the offline templates. Takes the
        facts rather than a session, so no transaction is held open while the model
        writes.
        """
        return self._drafted(self._writer(), facts)

    def _drafted(
        self, writer: LlmWriter | OfflineWriter, facts: LeadFacts
    ) -> DraftResult:
        if isinstance(writer, OfflineWriter):
            return DraftResult(
                tuple(
                    (draft, check_message(draft, facts, self._cfg.messages))
                    for draft in writer.write(facts)
                )
            )
        outcome = writer.write(facts)
        if outcome.status != "ready":
            return DraftResult((), outcome.failures)
        return DraftResult(tuple(zip(outcome.drafts, outcome.checks, strict=True)))

    def _chat(self, settings: LlmSettings) -> BaseChatModel:
        return build_chat_model(settings, self._environ, effort=self._cfg.llm.effort)

    def _judge(self) -> Judge:
        if self._settings is None:
            return Judge(None, load_prompt("judge_v1"))
        chat = self._chat(self._settings)
        return Judge(StructuredModel(chat, RubricScores), load_prompt("judge_v1"))

    def _write(
        self,
        stored: StoredLead,
        writer: LlmWriter | OfflineWriter,
        judge: Judge,
        usage: UsageContext | None,
    ) -> list[tuple[Draft, tuple[Check, ...], Any]] | None:
        facts = build_facts(stored, self._cfg.messages, usage)
        result = self._drafted(writer, facts)
        if not result.drafts or not all(all_passed(c) for _, c in result.drafts):
            return None
        return [
            (draft, checks, judge.score(draft, facts))
            for draft, checks in result.drafts
        ]

    def _notes(
        self,
        plan: SearchPlan,
        outcome: IngestionOutcome,
        judge: Judge,
        model_errors: int,
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
        if model_errors:
            notes.append(
                f"messages: {model_errors} lead(s) to manual review after a model error"
            )
        notes.append("judge: on" if judge.active else "judge: off (no model key)")
        notes.append("dispatch: dry run, nothing is sent")
        return tuple(notes)


def _gather(
    session: Session,
    run_id: uuid.UUID,
    plan: SearchPlan,
    labels: Collection[str],
) -> list[StoredLead]:
    """The run's own leads, plus every stored lead the plan matches.

    A run stores only contributions it has not seen before, so a repeat search over
    the same store writes nothing and its run alone would gather nobody. The store is
    narrowed with what it already holds (no provider call) before the usage stage:
    workers by employer domain, users by a tech signal naming a plan term.
    """
    gathered = {s.lead_id: s for s in _leads(session, run_id)}
    for stored in _leads(session, None):
        if stored.lead_id not in gathered and _matches(stored, plan, labels):
            gathered[stored.lead_id] = stored
    return sorted(gathered.values(), key=lambda s: str(s.lead_id))


def _leads(session: Session, run_id: uuid.UUID | None) -> list[StoredLead]:
    leads: list[StoredLead] = []
    after: uuid.UUID | None = None
    while True:
        page = list_leads(session, run_id=run_id, limit=_PAGE, after=after)
        leads += page
        if len(page) < _PAGE:
            return leads
        after = page[-1].lead_id


def _matches(stored: StoredLead, plan: SearchPlan, labels: Collection[str]) -> bool:
    lead = stored.lead
    if plan.mode == "workers":
        wanted = {d.casefold() for d in plan.domains}
        return any(
            d.casefold() in wanted for e in lead.employments for d in e.company.domains
        )
    if plan.mode == "users":
        phrases = [_words(p) for p in labels if _words(p)]
        return any(
            _holds(_words(signal.label), phrase)
            for signal in lead.tech_signals
            for phrase in phrases
        )
    return False  # free text: the run's own leads only


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


_CHECKS_FAILED = Reason(
    code="messages_failed_checks", note="no Message passed its checks"
)
_MODEL_FAILED = Reason(
    code="message_model_failed", note="the model call that writes Messages failed"
)


def _manual_review(decision: Decision, reason: Reason) -> Decision:
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
