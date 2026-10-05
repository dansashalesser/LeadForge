"""Named error taxonomy for every failure class in the ingestion slice.

Classification happens once, on the adapter; every layer above dispatches on the
exception type, never on an HTTP status. Every error names its provider and, where
applicable, the offending raw field path and canonical path.
"""


class SourceError(Exception):
    """Root of every failure attributable to a single provider."""

    def __init__(self, source_name: str, detail: str = "") -> None:
        self.source_name = source_name
        message = f"[{source_name}] {type(self).__name__}"
        super().__init__(f"{message}: {detail}" if detail else message)


class SourceUnauthorized(SourceError):
    def __init__(
        self, source_name: str, *, endpoint: str, scope_cause: str | None = None
    ) -> None:
        self.endpoint = endpoint
        self.scope_cause = scope_cause
        detail = f"endpoint={endpoint}"
        if scope_cause is not None:
            detail += f" scope_cause={scope_cause}"
        super().__init__(source_name, detail)


class SourceRateLimited(SourceError):
    def __init__(
        self, source_name: str, *, cause: str, retry_after_s: float | None = None
    ) -> None:
        self.cause = cause
        self.retry_after_s = retry_after_s
        detail = f"cause={cause}"
        if retry_after_s is not None:
            detail += f" retry_after_s={retry_after_s}"
        super().__init__(source_name, detail)


class SourceQuotaExhausted(SourceError):
    """Credit balance or plan quota is spent; halts the source for the run."""


class SourceTransient(SourceError):
    def __init__(self, source_name: str, *, status: int | None = None) -> None:
        self.status = status
        super().__init__(source_name, "" if status is None else f"status={status}")


class SourceTimedOut(SourceError):
    pass


class SourceComplianceRestricted(SourceError):
    def __init__(self, source_name: str, *, subject: str) -> None:
        self.subject = subject
        super().__init__(source_name, f"subject={subject}")


class NormalizationError(SourceError):
    def __init__(
        self, source_name: str, *, raw_field_path: str, canonical_path: str
    ) -> None:
        self.raw_field_path = raw_field_path
        self.canonical_path = canonical_path
        super().__init__(
            source_name, f"raw={raw_field_path} canonical={canonical_path}"
        )


class NoAccessibleAccountError(SourceError):
    """The provider has no account reachable with the configured credentials."""


class FixtureSchemaError(Exception):
    def __init__(self, provider: str, *, field: str) -> None:
        self.provider = provider
        self.field = field
        super().__init__(f"[{provider}] FixtureSchemaError: field={field}")


class DuplicateSourceNameError(Exception):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"duplicate source name: {name}")


class UndeclaredEndpointError(Exception):
    def __init__(self, provider: str, *, path: str) -> None:
        self.provider = provider
        self.path = path
        super().__init__(f"[{provider}] UndeclaredEndpointError: path={path}")
