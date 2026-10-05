"""The adapter contract: what every lead source declares and implements (task 3.1).

Normalization returns ``LeadContribution`` records, never a ``CanonicalLead``; only the
Merge Engine builds that projection. Each adapter declares the canonical paths its API
can answer for (``answerable_surfaces``), so "could answer" versus "never able to
answer" is data, and every ``SourceAbsence`` is checked against it at the boundary.
"""

import math
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self

from pydantic import Field, model_validator

from leadforge.lead_ingestion.errors import (
    FixtureSchemaError,
    InvalidAbsenceError,
    MissingCredentialError,
    SourceError,
    SourceRateLimited,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    DataMode,
    FieldProvenance,
    NonBlank,
    SourceAbsence,
    UntrustedText,
    _Entity,
)
from leadforge.lead_ingestion.send_prohibition import assert_no_send_capable_endpoints

if TYPE_CHECKING:
    from leadforge.lead_ingestion.pacing import SourcePacing
    from leadforge.lead_ingestion.transport import Transport, TransportResponse

__all__ = [
    "TARGET_TERM_PATH_PREFIX",
    "BaseLeadSource",
    "Capability",
    "ChargeUnit",
    "CostClass",
    "Endpoint",
    "EnrichmentRequest",
    "LeadContribution",
    "LiveAccess",
    "RateBucket",
    "RateWindow",
    "RawBatch",
    "SourceRequest",
    "enrichment_order",
    "enrichment_sort_key",
    "enrichment_tiers",
    "resolve_credentials",
    "retry_after_seconds",
]


class Capability(StrEnum):
    SEARCH = "search"  # Discovery
    ENRICH = "enrich"  # Enrichment


class CostClass(StrEnum):
    FREE = "free"
    PAID = "paid"  # Credit-bearing


class LiveAccess(StrEnum):
    """Whether a provider can run live for a demo operator (3.6)."""

    AVAILABLE = "available"
    GATED = "gated"  # live is possible only behind an approval or plan we may lack
    UNAVAILABLE = "unavailable"  # decided synthetic-only


class ChargeUnit(StrEnum):
    PER_LEAD = "per_lead"
    PER_COMPANY = "per_company"
    PER_CALL = "per_call"


# Fewest billable events first: a per-company source is called once per company, a
# per-call source once per request, a per-lead source once for every lead.
_CHARGE_UNIT_RANK = {
    ChargeUnit.PER_COMPANY: 0,
    ChargeUnit.PER_CALL: 1,
    ChargeUnit.PER_LEAD: 2,
}


@dataclass(frozen=True)
class RateWindow:
    requests: int
    per_seconds: float


@dataclass(frozen=True)
class RateBucket:
    """One named limit; its windows are ANDed."""

    name: str
    windows: tuple[RateWindow, ...]
    documented: bool
    doc_url: str


def _is_provider_path(path: str) -> bool:
    """A path on the adapter's own host: '/a/b', no URL, query, '..' or '//'."""
    if not path.startswith("/") or path.startswith("//"):
        return False
    if any(c in path for c in "?#\\") or any(
        c.isspace() or not c.isprintable() for c in path
    ):
        return False
    return not any(seg in (".", "..") for seg in path.split("/")) and "//" not in path


@dataclass(frozen=True)
class Endpoint:
    """One provider path an adapter may reach; every call goes through a declared one.

    ``read_only`` is ``Literal[True]``, so a write endpoint is a type error, and it is
    also rejected at runtime for callers the type checker does not see (11.1, 11.2).
    POST is allowed because several read-only search APIs take their query in a body.
    """

    method: Literal["GET", "POST"]
    path: str
    bucket: str  # name of a RateBucket in the adapter's ``rate_limit``
    read_only: Literal[True] = True

    def __post_init__(self) -> None:
        if self.read_only is not True:
            raise ValueError("Endpoint.read_only must be True; no write endpoints")
        if self.method not in ("GET", "POST"):
            raise ValueError(
                f"Endpoint.method must be GET or POST, got {self.method!r}"
            )
        for field in ("path", "bucket"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Endpoint.{field} must be a non-blank str")
        if not _is_provider_path(self.path):
            raise ValueError(
                f"Endpoint.path must be a root-relative path on the provider host, "
                f"got {self.path!r}"
            )


class SourceRequest(_Entity):
    kind: NonBlank


class RawBatch(_Entity):
    """A provider payload kept verbatim; schema validation happens later."""

    source_name: NonBlank
    payload: Any


class LeadContribution(_Entity):
    """What one source says about a lead; merged into a ``CanonicalLead`` elsewhere."""

    source_name: NonBlank
    absences: tuple[SourceAbsence, ...] = ()
    # Canonical path -> the value this source supplied; one provenance record each.
    values: Mapping[str, Any] = Field(default_factory=dict)
    provenance: tuple[FieldProvenance, ...] = ()

    @model_validator(mode="after")
    def _provenance_matches_values(self) -> Self:
        paths = [p.canonical_path for p in self.provenance]
        if len(set(paths)) != len(paths) or set(paths) != set(self.values):
            raise ValueError(
                "values and provenance must name the same canonical paths, "
                "one provenance record each"
            )
        for record in self.provenance:
            if record.source_name != self.source_name:
                raise ValueError("provenance attributed to a different source")
            wrapped = isinstance(self.values[record.canonical_path], UntrustedText)
            if wrapped != record.untrusted:
                raise ValueError(
                    f"untrusted marking of {record.canonical_path!r} must match "
                    "whether its value is UntrustedText"
                )
        return self


class EnrichmentRequest(SourceRequest):
    """The request an Enrichment-phase source receives (task 11.5, Requirement 6.9).

    ``work_list`` is every contribution Discovery produced, in run order, unfiltered
    and unranked. It is the one extra thing an Enrichment call can be told, so it is a
    named subclass of the closed ``SourceRequest`` rather than a widened ``fetch_raw``.
    """

    work_list: tuple[LeadContribution, ...]


TARGET_TERM_PATH_PREFIX = "target_profile."


def _is_empty_vocabulary(value: object) -> bool:
    """A vocabulary is empty if it is None, a blank str, or an empty collection.

    Any other value, including 0 and False, is a real provider identifier.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Collection):
        return len(value) == 0
    return False


# Mapping declarations are copied into read-only views when a subclass is defined.
_FROZEN_MAPPINGS = (
    "rate_limit",
    "answerable_surfaces",
    "target_vocabulary",
    "endpoints",
)

_DECLARATIONS = (
    "name",
    "capabilities",
    "rate_limit",
    "answerable_surfaces",
    "cost_class",
    "charge_unit",
    "yields_suppression",
    "target_vocabulary",
    "endpoints",
    "required_env",
)


def _is_env_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and "=" not in name
        and "\0" not in name
        and not any(c.isspace() for c in name)
    )


class BaseLeadSource(ABC):
    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]  # may be empty
    rate_limit: ClassVar[Mapping[str, RateBucket]]
    # Canonical path the API can answer for -> raw field paths it can be asked on.
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]]
    # Enrichment ordering is derived from these, never hand-listed (2.7).
    cost_class: ClassVar[CostClass]
    charge_unit: ClassVar[ChargeUnit]
    yields_suppression: ClassVar[bool]
    # Whether the provider can run live for a demo operator (3.6). Defaulted, so a new
    # adapter stays one class; a configuration override may replace it per source.
    live_access: ClassVar[LiveAccess] = LiveAccess.AVAILABLE
    # Canonical Target Profile term -> this provider's opaque DEFAULT vocabulary for it.
    # The Target Profile configuration overrides it per term (effective_vocabulary).
    # An empty value (or an absent term) is Not Applicable, never "no match" (2.8).
    target_vocabulary: ClassVar[Mapping[str, object]]
    # Every provider path this adapter may reach, keyed by a local label (11.1).
    endpoints: ClassVar[Mapping[str, Endpoint]]
    # Names of the environment variables holding credentials; values are never declared
    # here, only resolved from the process environment (2.5, 10.4).
    required_env: ClassVar[tuple[str, ...]]
    # Provider documentation page named in the generated credential example file (10.2).
    # Defaulted, so a keyless adapter declares nothing; when blank, the generator falls
    # back to the first rate bucket's doc_url and fails if an adapter with credentials
    # has neither.
    docs_url: ClassVar[str] = ""
    # Provider host for live mode (``https://api.example.com``); blank for a source
    # that never goes live over REST. Read by ``build_transport`` only.
    base_url: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for declaration in _FROZEN_MAPPINGS:
            # getattr, not __dict__: an inherited mixin dict must be frozen too.
            declared = getattr(cls, declaration, None)
            if isinstance(declared, Mapping):
                # Always copy, so neither the author's dict nor a proxy over one
                # can be used to mutate the declaration.
                setattr(cls, declaration, MappingProxyType(dict(declared)))
        declared_endpoints = getattr(cls, "endpoints", None)
        if isinstance(declared_endpoints, Mapping):  # an abstract mixin declares none
            # At definition, so a send-capable adapter cannot even be imported (11.1).
            assert_no_send_capable_endpoints(
                str(getattr(cls, "name", cls.__name__)), declared_endpoints
            )

    def __init__(
        self,
        mode: DataMode,
        *,
        transport: "Transport | None" = None,
        pacing: "SourcePacing | None" = None,
    ) -> None:
        cls = type(self).__name__
        missing = [d for d in _DECLARATIONS if not hasattr(type(self), d)]
        if missing:
            raise TypeError(f"{cls} does not declare: {', '.join(missing)}")
        if not isinstance(self.name, str) or not self.name.strip():
            raise TypeError(f"{cls}.name must be a non-blank str")
        if not isinstance(self.capabilities, frozenset) or not all(
            isinstance(c, Capability) for c in self.capabilities
        ):
            raise TypeError(f"{cls}.capabilities must be a frozenset of Capability")
        if not isinstance(self.rate_limit, Mapping):
            raise TypeError(f"{cls}.rate_limit must be a Mapping")
        if not isinstance(self.answerable_surfaces, Mapping):
            raise TypeError(f"{cls}.answerable_surfaces must be a Mapping")
        if not isinstance(self.cost_class, CostClass):
            raise TypeError(f"{cls}.cost_class must be a CostClass")
        if not isinstance(self.charge_unit, ChargeUnit):
            raise TypeError(f"{cls}.charge_unit must be a ChargeUnit")
        if not isinstance(self.yields_suppression, bool):
            raise TypeError(f"{cls}.yields_suppression must be a bool")
        if not isinstance(self.live_access, LiveAccess):
            raise TypeError(f"{cls}.live_access must be a LiveAccess")
        for path, surfaces in self.answerable_surfaces.items():
            # A bare str would make `in` a substring test, so demand a real set.
            if not isinstance(surfaces, frozenset) or not all(
                isinstance(s, str) and s.strip() for s in surfaces
            ):
                raise TypeError(
                    f"{cls}.answerable_surfaces[{path!r}] must be a frozenset of "
                    "non-blank str"
                )
            if not surfaces:
                raise TypeError(
                    f"{cls}.answerable_surfaces[{path!r}] has no surface; "
                    "leave an unanswerable path out instead"
                )
        if not isinstance(self.target_vocabulary, Mapping):
            raise TypeError(f"{cls}.target_vocabulary must be a Mapping")
        for term in self.target_vocabulary:
            if not isinstance(term, str) or not term.strip():
                raise TypeError(f"{cls}.target_vocabulary keys must be non-blank str")
        answerable = {
            path.removeprefix(TARGET_TERM_PATH_PREFIX)
            for path in self.answerable_surfaces
            if path.startswith(TARGET_TERM_PATH_PREFIX)
        }
        expressible = {
            term
            for term, vocab in self.target_vocabulary.items()
            if not _is_empty_vocabulary(vocab)
        }
        if answerable != expressible:
            raise TypeError(
                f"{cls} target terms must match: answerable without vocabulary "
                f"{sorted(answerable - expressible)}, vocabulary without answerable "
                f"surface {sorted(expressible - answerable)}"
            )
        self._validate_endpoints(cls)
        self._validate_required_env(cls)
        self._mode = mode
        self._transport = transport
        self._pacing = pacing

    @classmethod
    def build_transport(
        cls, mode: DataMode, *, fixtures_root: Path | None = None
    ) -> "Transport":
        """The transport for ``mode``, built from this adapter's own ``endpoints``.

        The caller resolves the mode and hands the result to the constructor; the
        adapter never chooses its transport. Taking the endpoint map from the class
        means a transport cannot be built over a copy that drifted from it (11.1).
        """
        from leadforge.lead_ingestion.transport import FixtureTransport, RestTransport

        if mode is DataMode.SYNTHETIC:
            return FixtureTransport(
                cls.name, cls.endpoints, fixtures_root=fixtures_root
            )
        if not cls.base_url.strip():
            raise ValueError(f"{cls.name} declares no base_url for live mode")
        if not cls.base_url.startswith("https://"):
            # Credentials ride in headers: never over cleartext.
            raise ValueError(f"{cls.name} base_url must be https")
        return RestTransport(cls.name, cls.base_url, cls.endpoints)

    @classmethod
    def from_run(
        cls,
        mode: DataMode,
        *,
        transport: "Transport",
        pacing: "SourcePacing | None",
        vocabulary: Mapping[str, object] | None,
    ) -> Self:
        """Build the source for a run: the one construction a composition root uses.

        ``vocabulary`` is the Target Profile's effective vocabulary for this source
        (``effective_vocabulary``), or ``None`` when no profile was read. A source with
        no use for it ignores it, as this default does; one that reads it overrides
        this, so the root never names a concrete adapter or its constructor.
        """
        return cls(mode, transport=transport, pacing=pacing)

    @property
    def transport(self) -> "Transport":
        if self._transport is None:
            raise RuntimeError(f"{self.name} was constructed without a transport")
        return self._transport

    async def _send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> "TransportResponse":
        """Dispatch through the transport, first awaiting the endpoint's rate bucket.

        Every provider call goes through here, so each call, retries included, takes
        capacity from the bucket the endpoint declares (7.1). A source with no pacing
        (synthetic mode) awaits nothing. Retry stays with the caller's ``RetryPolicy``.

        A non-2xx answer is classified by ``classify_error`` and raised as its
        ``SourceError`` type, so the caller never sees an HTTP status. Timeouts and
        connection errors are raised by the transport before any response exists.
        """
        if self._pacing is not None:
            await self._pacing.throttle.bucket(endpoint.bucket).acquire()
        response = await self.transport.send(
            endpoint, params=params, json_body=json_body, headers=headers
        )
        self._note_response(response)
        error = self.classify_error(response, endpoint=endpoint)
        if error is not None:
            raise error
        return response

    def _note_response(  # noqa: B027 - optional hook, a no-op by default
        self, response: "TransportResponse"
    ) -> None:
        """Hook to read a response of any status before it is classified."""

    def classify_error(
        self, response: "TransportResponse", *, endpoint: Endpoint
    ) -> SourceError | None:
        """The conventional status mapping; ``None`` for a 2xx. Overridden per provider.

        401 and 403 are ``SourceUnauthorized`` naming the endpoint, 429 is
        ``SourceRateLimited`` (with ``Retry-After`` when it is a usable number), 5xx
        and 408 are ``SourceTransient``, and anything else is a plain ``SourceError``:
        permanent, one attempt (7.4). Error text names the path and status, never a
        body. A provider that inverts the convention (16.7) overrides this.
        """
        status = response.status
        if 200 <= status < 300:
            return None
        if status in (401, 403):
            return SourceUnauthorized(self.name, endpoint=endpoint.path)
        if status == 429:
            return SourceRateLimited(
                self.name,
                cause="http_429",
                retry_after_s=retry_after_seconds(response.headers),
            )
        if status >= 500 or status == 408:
            return SourceTransient(self.name, status=status)
        return SourceError(self.name, f"{endpoint.path} returned status {status}")

    def _validate_endpoints(self, cls: str) -> None:
        if not isinstance(self.endpoints, Mapping):
            raise TypeError(f"{cls}.endpoints must be a Mapping")
        for label, endpoint in self.endpoints.items():
            if not isinstance(label, str) or not label.strip():
                raise TypeError(f"{cls}.endpoints keys must be non-blank str")
            # Exact type: a subclass could override the checks in __post_init__.
            if type(endpoint) is not Endpoint or endpoint.read_only is not True:
                raise TypeError(
                    f"{cls}.endpoints[{label!r}] must be a read-only Endpoint"
                )
            if endpoint.bucket not in self.rate_limit:
                raise TypeError(
                    f"{cls}.endpoints[{label!r}] uses undeclared rate bucket "
                    f"{endpoint.bucket!r}"
                )
        assert_no_send_capable_endpoints(self.name, self.endpoints)  # 11.1

    def _validate_required_env(self, cls: str) -> None:
        names = self.required_env
        if not isinstance(names, tuple) or not all(_is_env_name(n) for n in names):
            raise TypeError(
                f"{cls}.required_env must be a tuple of environment variable names"
            )
        if len(set(names)) != len(names):
            raise TypeError(f"{cls}.required_env must not repeat a name")

    @property
    def data_mode(self) -> DataMode:
        return self._mode

    @abstractmethod
    async def fetch_raw(self, request: SourceRequest) -> RawBatch: ...

    @abstractmethod
    def normalize(self, raw: RawBatch) -> list[LeadContribution]: ...

    @classmethod
    def validate_fixture(cls, endpoint: str, body: object) -> None:
        """Check one endpoint's fixture body against this adapter's raw schema (5.3).

        An adapter that ships a fixture overrides this with its own raw models and
        raises ``NormalizationError`` naming the first offending field. The default
        refuses, so a source with fixtures and no declared schema cannot pass.
        """
        raise FixtureSchemaError(cls.name, field=f"{endpoint}: no raw schema declared")

    @classmethod
    def unmapped_fixture_paths(cls, endpoint: str, body: object) -> list[str]:
        """Leaf paths of one endpoint's fixture that no rule maps and no ignore lists.

        The fixture counterpart of ``validate_fixture`` (5.5): an adapter that ships a
        fixture overrides this, reshaping the body into the records its rules read and
        returning ``unmapped_raw_paths`` for each. The default refuses, so a source
        with fixtures and no declared coverage cannot pass.
        """
        raise FixtureSchemaError(
            cls.name, field=f"{endpoint}: no field coverage declared"
        )

    @classmethod
    def validate_reference_file(cls, file: str, text: str) -> None:
        """Check a non-JSON reference file with the adapter's own loader (5.3)."""
        raise FixtureSchemaError(cls.name, field=f"{file}: no loader declared")

    def target_term_absence(
        self, term: str, *, vocabulary: Mapping[str, object] | None = None
    ) -> SourceAbsence | None:
        """``None`` if this source can express ``term``, else a Not Applicable record.

        A source with no vocabulary for a term was never asked, so it cannot have
        found "no match" (2.8). ``vocabulary`` is the effective vocabulary (the
        Target Profile over the adapter default, ``effective_vocabulary``); without
        it only the adapter default is consulted.
        """
        if not isinstance(term, str) or not term.strip():
            raise ValueError("target term must be non-blank")
        known = self.target_vocabulary if vocabulary is None else vocabulary
        if not _is_empty_vocabulary(known.get(term)):
            return None
        return SourceAbsence(
            canonical_path=f"{TARGET_TERM_PATH_PREFIX}{term}",
            source_name=self.name,
            kind=AbsenceKind.NOT_APPLICABLE,
        )

    def validate_absence(self, absence: SourceAbsence) -> SourceAbsence:
        """Return ``absence`` if this source's declarations allow it, else raise."""

        def reject(reason: str) -> InvalidAbsenceError:
            return InvalidAbsenceError(
                self.name,
                canonical_path=absence.canonical_path,
                raw_field_path=absence.raw_field_path,
                reason=reason,
            )

        if absence.source_name != self.name:
            raise reject(f"attributed to {absence.source_name!r}, not {self.name!r}")
        surfaces = self.answerable_surfaces.get(absence.canonical_path)
        if absence.kind is AbsenceKind.NOT_APPLICABLE:
            if surfaces is not None:
                raise reject("source declares this path answerable; not applicable")
        elif surfaces is None:
            raise reject(
                "source declares no surface for this path; no negative evidence"
            )
        elif absence.raw_field_path not in surfaces:
            raise reject("raw field path is not a declared surface for this path")
        return absence

    def normalize_checked(self, raw: RawBatch) -> list[LeadContribution]:
        """``normalize`` plus absence validation; the orchestrator calls this."""
        contributions = self.normalize(raw)
        for contribution in contributions:
            for absence in contribution.absences:
                self.validate_absence(absence)
        return contributions


def enrichment_sort_key(
    source: "BaseLeadSource | type[BaseLeadSource]",
) -> tuple[bool, bool, int, str]:
    """Pure ordering key over a source's declarations; smaller runs earlier.

    Free before Credit-bearing, so Suppression and dedupe cost nothing; within a tier,
    Suppression-bearing first so suppressed leads leave the work list before later
    sources are called; then fewest billable events; name breaks ties deterministically.
    """
    return (
        source.cost_class is CostClass.PAID,
        not source.yields_suppression,
        _CHARGE_UNIT_RANK[source.charge_unit],
        source.name,
    )


def enrichment_order[S: BaseLeadSource](sources: Iterable[S]) -> list[S]:
    """Sources in Enrichment order, derived from declarations alone (2.7)."""
    return sorted(sources, key=enrichment_sort_key)


def resolve_credentials(
    source: "BaseLeadSource | type[BaseLeadSource]",
    environ: Mapping[str, str] | None = None,
) -> Mapping[str, str]:
    """Values of the source's ``required_env`` names, from the process environment.

    The environment is the only source: no config file, ``.env`` parse or default is
    consulted here (2.5). An unset or blank variable is missing; every missing name is
    reported together, and the error carries names only, never values (10.5).
    """
    env = os.environ if environ is None else environ
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for name in source.required_env:
        value = env.get(name, "")
        if value.strip():
            resolved[name] = value
        else:
            missing.append(name)
    if missing:
        raise MissingCredentialError(source.name, missing=tuple(missing))
    return MappingProxyType(resolved)


def enrichment_tiers[S: BaseLeadSource](sources: Iterable[S]) -> list[list[S]]:
    """``enrichment_order`` split into tiers of equal declarations (Requirement 6.10).

    Sources sharing ``cost_class``, ``charge_unit`` and ``yields_suppression`` form one
    tier (the name only breaks ties inside it); tiers come out in run order.
    """
    tiers: list[list[S]] = []
    last: tuple[bool, bool, int] | None = None
    for source in enrichment_order(sources):
        tier_key = enrichment_sort_key(source)[:3]
        if tier_key != last:
            tiers.append([])
            last = tier_key
        tiers[-1].append(source)
    return tiers


_DELTA_SECONDS = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    """Seconds from a ``retry-after`` header; ``None`` unless a positive finite number.

    An HTTP-date, a negative, a non-number, or an overflow is ``None``: the caller then
    falls back to its own backoff rather than inventing an interval.
    """
    raw = headers.get("retry-after", "").strip()
    if not _DELTA_SECONDS.fullmatch(raw):
        return None
    seconds = float(raw)
    return seconds if math.isfinite(seconds) and seconds > 0 else None
