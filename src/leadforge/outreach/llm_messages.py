"""Write Messages with a model (requirements 7.1, 7.4, 7.5, 7.7, 14.2).

One call per Message (the invite, then the email). The model answers through structured
output; the answer becomes a ``Draft`` and goes through the same checks as an offline
one. A Draft that fails is asked for again, up to ``max_regenerations`` more times with
the failed checks named; if none passes, the Lead goes to ``manual_review`` and has no
Message at all (7.4), never half a pair.

The Lead's facts reach the model only inside one escaped ``<lead_facts>`` block of the
user message (7.5); the system prompt, from a versioned file, holds only trusted
numbers from config. Every Draft records the prompt version and the model (7.7).
"""

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from leadforge.outreach.config import MessageConfig
from leadforge.outreach.errors import MessageGenerationError
from leadforge.outreach.facts import LeadFacts, render_facts
from leadforge.outreach.llm import ModelInvoker
from leadforge.outreach.message_checks import (
    Check,
    Claim,
    Draft,
    all_passed,
    check_message,
)
from leadforge.outreach.prompts import Prompt, delimit

__all__ = ["LlmWriter", "WriteOutcome", "WrittenMessage"]

Messages = Sequence[tuple[str, str]]


class WrittenMessage(BaseModel):
    """What the model fills in for one Message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str | None = None
    body: str
    claims: tuple[Claim, ...]


class WriteOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ready", "manual_review"]
    # (invite, email) when ready; empty when the Lead goes to manual review.
    drafts: tuple[Draft, ...]
    # The checks that failed on the last attempt of the Message that gave up.
    failures: tuple[Check, ...] = ()


class LlmWriter:
    def __init__(
        self,
        invite_model: ModelInvoker,
        email_model: ModelInvoker,
        *,
        invite_prompt: Prompt,
        email_prompt: Prompt,
        model_name: str,
        cfg: MessageConfig,
    ) -> None:
        self._models = {"invite": invite_model, "email": email_model}
        self._model_name = model_name
        self._cfg = cfg
        self._prompts = {
            "invite": invite_prompt,
            "email": email_prompt,
        }
        self._systems = {
            "invite": invite_prompt.fill(max_chars=str(cfg.invite_max_chars)),
            "email": email_prompt.fill(
                max_chars=str(cfg.email_max_chars),
                subject_max_chars=str(cfg.email_subject_max_chars),
            ),
        }

    def write(self, facts: LeadFacts) -> WriteOutcome:
        drafts: list[Draft] = []
        for kind in ("invite", "email"):
            draft, failures = self._one(kind, facts)
            if draft is None:
                return WriteOutcome(
                    status="manual_review", drafts=(), failures=failures
                )
            drafts.append(draft)
        return WriteOutcome(status="ready", drafts=tuple(drafts))

    def _one(
        self, kind: Literal["invite", "email"], facts: LeadFacts
    ) -> tuple[Draft | None, tuple[Check, ...]]:
        ask = render_facts(facts)
        failures: tuple[Check, ...] = ()
        for _ in range(self._cfg.max_regenerations + 1):
            messages: Messages = (
                ("system", self._systems[kind]),
                ("human", ask + _feedback(failures)),
            )
            try:
                answer = self._models[kind].invoke(messages)
            except Exception as error:  # noqa: BLE001 - re-raised named, type only
                raise MessageGenerationError(
                    f"the model call failed ({type(error).__name__})"
                ) from None
            try:
                written = (
                    answer
                    if isinstance(answer, WrittenMessage)
                    else WrittenMessage.model_validate(answer)
                )
            except ValidationError:
                failures = (
                    Check(
                        name="schema", passed=False, detail="not the requested shape"
                    ),
                )
                continue
            draft = Draft(
                kind=kind,
                subject=written.subject if kind == "email" else None,
                body=written.body,
                claims=written.claims,
                generator="llm",
                model=self._model_name,
                prompt_version=self._prompts[kind].version,
            )
            checks = check_message(draft, facts, self._cfg)
            if all_passed(checks):
                return draft, ()
            failures = tuple(c for c in checks if not c.passed)
        return None, failures


def _feedback(failures: tuple[Check, ...]) -> str:
    if not failures:
        return ""
    problems = "; ".join(f"{c.name}: {c.detail or 'failed'}" for c in failures)
    return (
        "\n\nYour previous attempt failed these checks, so write it again:\n"
        + delimit("failed_checks", problems)
    )
