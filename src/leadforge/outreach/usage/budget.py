"""The per-run usage budget: searches, fetches and LLM calls (Req 4.5)."""

from dataclasses import dataclass, field

__all__ = ["BUDGET_EXHAUSTED", "KINDS", "UsageBudget"]

BUDGET_EXHAUSTED = "budget_exhausted"
KINDS = ("searches", "fetches", "llm_calls")


@dataclass
class UsageBudget:
    """Separate limits per paid call kind; ``spend`` is False once one is used up.

    When ``spend`` returns False the stage stops and grades the company
    ``unverified`` with reason ``BUDGET_EXHAUSTED``.
    """

    searches: int
    fetches: int
    llm_calls: int
    _used: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(KINDS, 0), init=False, repr=False
    )

    def __post_init__(self) -> None:
        for kind in KINDS:
            if getattr(self, kind) < 0:
                raise ValueError(f"budget {kind} must be >= 0")

    def _check(self, kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown budget kind {kind!r}; expected one of {KINDS}")

    def used(self, kind: str) -> int:
        self._check(kind)
        return self._used[kind]

    def exhausted(self, kind: str) -> bool:
        return self.used(kind) >= getattr(self, kind)

    def spend(self, kind: str) -> bool:
        """Take one unit of ``kind``; False (and nothing taken) when exhausted."""
        if self.exhausted(kind):
            return False
        self._used[kind] += 1
        return True
