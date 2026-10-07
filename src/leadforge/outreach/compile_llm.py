"""Compile free text into a Search Plan with a model (requirements 2.1, 2.3, 2.4, 14.2).

The model answers through structured output. Its answer is validated as a plan (no extra
field, only terms the base profile knows); an answer that does not validate is asked
for again up to the configured bound, then the search fails with ``PlanCompileError``.
A failing call is never answered by the offline compiler: a key being set and the call
failing is an error, not a reason to switch silently.

The query is untrusted: it sits in an escaped ``<query>`` block of the user message and
never in the system prompt, so words in it cannot become instructions or change the
output schema.
"""

from collections.abc import Collection, Sequence
from typing import Protocol

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, ValidationError

from leadforge.outreach.errors import PlanCompileError, UnknownTermError
from leadforge.outreach.prompts import Prompt, delimit
from leadforge.outreach.search_plan import SearchPlan, SearchRequest, check_terms

__all__ = ["LangChainPlanModel", "LlmCompiler", "PlanDraft", "PlanModel"]

Messages = Sequence[tuple[str, str]]


class _EmptyPlanError(ValueError):
    """The model's plan is valid but names nothing to search for."""


class PlanDraft(BaseModel):
    """What the model fills in; the rest of the plan is the request's own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    terms: tuple[str, ...] = ()
    titles: tuple[str, ...] = ()
    seniorities: tuple[str, ...] = ()


class PlanModel(Protocol):
    """A model that answers a message list with something plan-shaped."""

    def invoke(self, messages: Messages) -> object: ...


class LangChainPlanModel:
    """``PlanModel`` over a LangChain chat model's structured output."""

    def __init__(self, chat: BaseChatModel) -> None:
        self._structured = chat.with_structured_output(PlanDraft)

    def invoke(self, messages: Messages) -> object:
        return self._structured.invoke(list(messages))


class LlmCompiler:
    """``SearchCompiler`` that asks a model, validates, and retries a bounded number."""

    def __init__(
        self,
        model: PlanModel,
        prompt: Prompt,
        known_terms: Collection[str],
        *,
        retries: int,
    ) -> None:
        if retries < 0:
            raise ValueError("retries must not be negative")
        self._model = model
        self._known = tuple(known_terms)
        self._retries = retries
        self._system = prompt.fill(terms=", ".join(self._known))

    def compile(self, request: SearchRequest) -> SearchPlan:
        messages: Messages = (
            ("system", self._system),
            ("human", delimit("query", request.query)),
        )
        failure = "no answer"
        for _ in range(self._retries + 1):
            try:
                answer = self._model.invoke(messages)
            except Exception as error:  # noqa: BLE001 - re-raised named, type only
                raise PlanCompileError(
                    f"the model call failed ({type(error).__name__})"
                ) from None
            try:
                return self._plan(request, answer)
            except (ValidationError, UnknownTermError, _EmptyPlanError) as error:
                failure = type(error).__name__
        tries = self._retries + 1
        raise PlanCompileError(
            f"the model returned no valid plan after {tries} tries ({failure})"
        )

    def _plan(self, request: SearchRequest, answer: object) -> SearchPlan:
        draft = (
            answer
            if isinstance(answer, PlanDraft)
            else PlanDraft.model_validate(answer)
        )
        plan = SearchPlan(
            mode="free_text",
            query=request.query,
            terms=draft.terms,
            titles=draft.titles,
            seniorities=draft.seniorities,
            compiler="llm",
        )
        check_terms(plan, self._known)
        if not plan.terms:
            raise _EmptyPlanError("the plan names no term")
        return plan
