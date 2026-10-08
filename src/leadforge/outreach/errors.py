"""Named outreach errors. Each is raised at the boundary that finds the fault, before
any provider call or write, and never carries a Lead's personal data."""


class OutreachConfigError(ValueError):
    """An outreach configuration file cannot be turned into settings.

    Names the file and the offending key path, never the offending value.
    """

    def __init__(self, path: str, *, key_path: str, detail: str) -> None:
        self.path = path
        self.key_path = key_path
        self.detail = detail
        super().__init__(
            f"[{path}] OutreachConfigError: key={key_path or '<document>'}: {detail}"
        )


class UnknownModeError(ValueError):
    """A search mode other than the three the system accepts."""

    def __init__(self, mode: str, *, accepted: tuple[str, ...]) -> None:
        self.mode = mode
        super().__init__(f"unknown search mode {mode!r}; use one of {accepted}")


class UnknownTermError(ValueError):
    """A plan names a technology term no provider vocabulary knows."""

    def __init__(self, term: str) -> None:
        self.term = term
        super().__init__(f"unknown technology term {term!r}: no provider knows it")


class MissingDomainError(ValueError):
    """A workers search for a company with no known domain."""

    def __init__(self, company: str) -> None:
        self.company = company
        super().__init__(
            f"no known domain for company {company!r}; give one with --domain"
        )


class NoProductSelectedError(ValueError):
    """A users search that names no catalog product. There is no default vendor."""

    def __init__(self) -> None:
        super().__init__(
            "a users search must name at least one catalog product "
            "(--product, optionally --vendor)"
        )


class PlanCompileError(RuntimeError):
    """A query could not be compiled into a valid Search Plan."""


class MessageGenerationError(RuntimeError):
    """The model call that writes or judges a Message failed (type only, no text)."""


class UnknownLeadError(LookupError):
    """Messages were asked for a Lead the store does not hold."""


class UnknownSearchError(LookupError):
    """Messages were asked for under a search the store does not hold."""


class MessageValidationError(ValueError):
    """A Message that failed its checks was offered for storage."""


class UsageClassifierUnavailableError(RuntimeError):
    """A live users search has no classifier. There is no silent offline fallback."""

    def __init__(self) -> None:
        super().__init__(
            "a live users search needs a usage classifier (an LLM key); "
            "it does not fall back to the offline classifier"
        )
