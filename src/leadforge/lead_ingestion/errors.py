"""Named error taxonomy for every failure class in the ingestion slice.

Classification happens once, on the adapter; every layer above dispatches on the
exception type, never on an HTTP status. Every error names its provider and, where
applicable, the offending raw field path and canonical path.
"""

from typing import Any


def _rebuild(cls: "type[BaseException]", args: tuple[Any, ...]) -> BaseException:
    """Recreate an exception without calling its keyword-only ``__init__``."""
    obj = BaseException.__new__(cls)
    obj.args = args
    return obj


class _Picklable(Exception):
    """Round-trips through pickle/copy despite required keyword-only init args."""

    def __reduce__(self) -> tuple[Any, ...]:
        return _rebuild, (type(self), self.args), self.__dict__


class SourceError(_Picklable):
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


class FixtureSchemaError(_Picklable):
    def __init__(self, provider: str, *, field: str) -> None:
        self.provider = provider
        self.field = field
        super().__init__(f"[{provider}] FixtureSchemaError: field={field}")


class DuplicateSourceNameError(_Picklable):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"duplicate source name: {name}")


class UndeclaredEndpointError(_Picklable):
    def __init__(self, provider: str, *, path: str) -> None:
        self.provider = provider
        self.path = path
        super().__init__(f"[{provider}] UndeclaredEndpointError: path={path}")


class SendCapableEndpointError(_Picklable):
    """An endpoint that could send or write was declared or handed over (11.1)."""

    def __init__(self, provider: str, *, path: str, reason: str) -> None:
        self.provider = provider
        self.path = path
        self.reason = reason
        super().__init__(
            f"[{provider}] SendCapableEndpointError: path={path} reason={reason}"
        )


class ConflictingCompanySignalError(_Picklable):
    """Two Company Signals share one ``company_id`` but disagree on content."""

    def __init__(self, company_id: str) -> None:
        self.company_id = company_id
        super().__init__(
            f"company_id {company_id!r} names two Company Signals with different "
            "content; one organization must have one Company Signal"
        )


class InvalidAbsenceError(SourceError):
    """A ``SourceAbsence`` contradicts what the source declares it can answer."""

    def __init__(
        self,
        source_name: str,
        *,
        canonical_path: str,
        raw_field_path: str | None,
        reason: str,
    ) -> None:
        self.canonical_path = canonical_path
        self.raw_field_path = raw_field_path
        self.reason = reason
        super().__init__(
            source_name,
            f"canonical={canonical_path} raw={raw_field_path}: {reason}",
        )


class MissingCredentialError(SourceError):
    """Declared credential variables are unset or blank; names only, never values."""

    def __init__(self, source_name: str, *, missing: tuple[str, ...]) -> None:
        self.missing = missing
        super().__init__(
            source_name, f"missing environment variables: {', '.join(missing)}"
        )


class SourceDiscoveryError(_Picklable):
    """A module in the adapter package cannot be turned into a registered source.

    Raised at startup when a scanned module fails to import, or when a concrete (or
    name-bearing) adapter class has no usable ``name`` or is still abstract. A
    source silently dropped here would look identical to one never configured.
    """

    def __init__(self, module: str, *, detail: str) -> None:
        self.module = module
        self.detail = detail
        super().__init__(f"[{module}] SourceDiscoveryError: {detail}")


class ConfigurationError(_Picklable):
    """A configuration file under ``config/`` cannot be turned into settings.

    Names the file and the offending key path (``technologies.some_term``), and
    never the offending value: a mistyped line may hold a secret.
    """

    def __init__(self, path: str, *, key_path: str, detail: str) -> None:
        self.path = path
        self.key_path = key_path
        self.detail = detail
        super().__init__(
            f"[{path}] ConfigurationError: key={key_path or '<document>'}: {detail}"
        )
