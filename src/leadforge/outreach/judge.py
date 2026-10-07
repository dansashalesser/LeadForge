"""Judge a Message against a rubric with a model (requirement 15.4).

Runs only when a model key is set: with none, ``Judge.score`` returns ``None`` and no
call is made. One score per rubric item (1 to 5) is stored on the Message row. The
Message and the Lead's facts reach the model only inside escaped blocks of the user
message. A failing call, or an answer that is not the rubric, is a named error: a
missing score is better than an invented one.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from leadforge.outreach.errors import MessageGenerationError
from leadforge.outreach.facts import LeadFacts, render_facts
from leadforge.outreach.llm import ModelInvoker
from leadforge.outreach.message_checks import Draft
from leadforge.outreach.prompts import Prompt, delimit

__all__ = ["Judge", "RubricScores"]

Score = Annotated[int, Field(ge=1, le=5, strict=True)]


class RubricScores(BaseModel):
    """The rubric: one whole-number score from 1 to 5 per item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    personalization: Score
    specificity: Score
    tone: Score
    clarity: Score


class Judge:
    def __init__(self, model: ModelInvoker | None, prompt: Prompt) -> None:
        self._model = model
        self._prompt = prompt

    @property
    def active(self) -> bool:
        return self._model is not None

    def score(self, draft: Draft, facts: LeadFacts) -> dict[str, int] | None:
        """The score per rubric item, or ``None`` when no model is set."""
        if self._model is None:
            return None
        text = f"{draft.subject}\n\n{draft.body}" if draft.subject else draft.body
        messages = (
            ("system", self._prompt.text),
            ("human", f"{render_facts(facts)}\n\n{delimit('message', text)}"),
        )
        try:
            answer = self._model.invoke(messages)
        except Exception as error:  # noqa: BLE001 - re-raised named, type only
            raise MessageGenerationError(
                f"the judge call failed ({type(error).__name__})"
            ) from None
        try:
            scores = (
                answer
                if isinstance(answer, RubricScores)
                else RubricScores.model_validate(answer)
            )
        except ValidationError:
            raise MessageGenerationError(
                "the judge answered something other than the rubric"
            ) from None
        return scores.model_dump()
