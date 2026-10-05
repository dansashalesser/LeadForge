"""Source Registry: discovers adapters by package scan (task 7.1, Requirement 3).

Adding a source is adding one module to the adapter package. The registry imports
every module under ``ADAPTER_PACKAGE`` and registers each concrete ``BaseLeadSource``
subclass *defined* there. There is no import list, no decorator, and no global state:
each ``SourceRegistry`` is built from the classes it was given, and ``_add`` is the
single registration path, so a later runtime ``register`` (task 7.4) reuses it.

Provisional decisions (see choices.md, task 7.1):

* Configuration is injected as ``Mapping[str, SourceSettings]`` keyed by the exact
  declared source name; reading it from ``config/sources.yaml`` is task 9.1.
* A module that fails to import fails startup (``SourceDiscoveryError``) rather than
  being skipped, because a skipped adapter is indistinguishable from an unconfigured
  one.
* Discovery never instantiates an adapter; names are read from the class.

Provisional decisions (see choices.md, task 7.2):

* ``active(factory)`` is the only place an adapter is constructed, and only for
  enabled sources. The caller injects ``factory`` because construction inputs
  (resolved data mode, transport, credentials) belong to tasks 8.1 and 12.1.
* Order is Source Trust Rank descending, then name; registration order is irrelevant.
* A factory failure propagates unchanged; an empty active list is not an error.
"""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from types import ModuleType

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    LiveAccess,
)
from leadforge.lead_ingestion.errors import (
    DuplicateSourceNameError,
    SourceDiscoveryError,
)
from leadforge.lead_ingestion.models import DataMode

__all__ = [
    "ADAPTER_PACKAGE",
    "LOWEST_TRUST_RANK",
    "ModeResolver",
    "SourceDescriptor",
    "SourceFactory",
    "SourceRegistry",
    "SourceSettings",
]

ADAPTER_PACKAGE = "leadforge.lead_ingestion.adapters"

# A higher Source Trust Rank wins a field conflict (8.4), so the lowest is the floor.
LOWEST_TRUST_RANK = 0


# Builds one adapter from its class; the registry never decides how (mode, transport).
SourceFactory = Callable[[type[BaseLeadSource]], BaseLeadSource]

# Seam for task 8.1 (mode_resolution.make_mode_resolver): picks a source's data mode.
ModeResolver = Callable[[type[BaseLeadSource], "SourceSettings"], DataMode]


@dataclass(frozen=True)
class SourceSettings:
    """Per-source configuration; every field defaults to what an absent entry means."""

    enabled: bool = True
    trust_rank: int = LOWEST_TRUST_RANK
    # Per-source overrides (3.6, 4.3); None means "no override, use the default".
    mode: DataMode | None = None
    live_access: LiveAccess | None = None

    def __post_init__(self) -> None:
        # Strings (as read from YAML) are coerced; anything else must already be right.
        object.__setattr__(self, "mode", _coerce(DataMode, "mode", self.mode))
        object.__setattr__(
            self, "live_access", _coerce(LiveAccess, "live_access", self.live_access)
        )
        if not isinstance(self.enabled, bool):
            raise TypeError(f"enabled must be a bool, got {self.enabled!r}")
        # bool is an int subclass; True would silently mean rank 1.
        if not isinstance(self.trust_rank, int) or isinstance(self.trust_rank, bool):
            raise TypeError(f"trust_rank must be an int, got {self.trust_rank!r}")
        if self.trust_rank < LOWEST_TRUST_RANK:
            raise ValueError(
                f"trust_rank must be >= {LOWEST_TRUST_RANK}, got {self.trust_rank}"
            )


def _coerce[E: StrEnum](enum: type[E], field: str, value: object) -> E | None:
    if value is None or isinstance(value, enum):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a {enum.__name__} or str, got {value!r}")
    try:
        return enum(value)
    except ValueError:
        allowed = ", ".join(m.value for m in enum)
        raise ValueError(f"{field} must be one of {allowed}, got {value!r}") from None


@dataclass(frozen=True)
class SourceDescriptor:
    """What the registry publishes about one source (3.5, 3.6).

    Carries credential *names* and found/missing facts only, never a value (10.5).
    """

    name: str
    enabled: bool
    capabilities: tuple[Capability, ...]  # sorted by value, so output is stable
    resolved_mode: DataMode
    live_access: LiveAccess  # declared on the adapter, or the configured override
    credential_present: bool  # every declared variable set and non-blank
    missing_env: tuple[str, ...]  # declared order
    # True only if every declared bucket is a provider-published limit; a source with
    # no declared bucket has no published limit to report.
    rate_limit_documented: bool

    def to_dict(self) -> dict[str, object]:
        """JSON-ready form: enums as their values, tuples as lists."""
        raw = asdict(self)
        raw["capabilities"] = [c.value for c in self.capabilities]
        raw["missing_env"] = list(self.missing_env)
        raw["resolved_mode"] = self.resolved_mode.value
        raw["live_access"] = self.live_access.value
        return raw


def _collision_key(name: str) -> str:
    """Names that differ only in case or padding are one source to a human."""
    return name.strip().casefold()


class SourceRegistry:
    def __init__(
        self,
        source_classes: Iterable[type[BaseLeadSource]] = (),
        config: Mapping[str, SourceSettings] | None = None,
    ) -> None:
        self._classes: dict[str, type[BaseLeadSource]] = {}
        self._collision_keys: set[str] = set()
        self._config: dict[str, SourceSettings] = dict(config or {})
        for source_class in source_classes:
            self._add(source_class)

    @classmethod
    def discover(
        cls,
        package: str | ModuleType = ADAPTER_PACKAGE,
        *,
        config: Mapping[str, SourceSettings] | None = None,
    ) -> SourceRegistry:
        """Import every module under ``package`` and register what it defines.

        Importing an adapter module must be free of side effects. Modules are
        visited in name order, so the first duplicate reported is deterministic.
        """
        root = _import_package(package)
        modules = sorted(_scan(root), key=lambda m: m.__name__)
        found = [c for m in modules for c in _defined_sources(m)]
        return cls(found, config)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._classes))

    def source_class(self, name: str) -> type[BaseLeadSource]:
        return self._classes[name]

    def settings(self, name: str) -> SourceSettings:
        """Configured settings; absent means enabled at the lowest rank (3.3)."""
        if name not in self._classes:
            raise KeyError(name)
        return self._config.get(name, SourceSettings())

    def describe(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        resolve_mode: ModeResolver | None = None,
    ) -> tuple[SourceDescriptor, ...]:
        """One descriptor per registered source, disabled ones included, by name.

        Reads classes only, so no adapter is constructed. ``environ`` defaults to the
        process environment (3.4); only declared names are looked up and only
        found/missing is kept. The mode is the per-source override, else
        ``resolve_mode`` (the task 8.1 seam), else synthetic.
        """
        env = os.environ if environ is None else environ
        return tuple(self._describe_one(n, env, resolve_mode) for n in self.names())

    def _describe_one(
        self,
        name: str,
        env: Mapping[str, str],
        resolve_mode: ModeResolver | None,
    ) -> SourceDescriptor:
        source_class = self._classes[name]
        settings = self.settings(name)
        missing = tuple(
            n for n in source_class.required_env if not env.get(n, "").strip()
        )
        buckets = source_class.rate_limit.values()
        return SourceDescriptor(
            name=name,
            enabled=settings.enabled,
            capabilities=tuple(
                sorted(source_class.capabilities, key=lambda c: c.value)
            ),
            resolved_mode=_mode_for(source_class, settings, resolve_mode),
            live_access=settings.live_access or source_class.live_access,
            credential_present=not missing,
            missing_env=missing,
            rate_limit_documented=bool(buckets) and all(b.documented for b in buckets),
        )

    def enabled_names(self) -> tuple[str, ...]:
        """Names of sources not disabled in configuration, in active order.

        Highest Source Trust Rank first, ties by name, so the order never depends
        on registration or import order.
        """
        enabled = [n for n in self._classes if self.settings(n).enabled]
        return tuple(sorted(enabled, key=lambda n: (-self.settings(n).trust_rank, n)))

    def active(self, factory: SourceFactory) -> tuple[BaseLeadSource, ...]:
        """Construct the enabled sources, and only those (3.4).

        A disabled class is never passed to ``factory``, so no object exists for it
        and no provider call is reachable. A factory error propagates unchanged.
        """
        sources: list[BaseLeadSource] = []
        for name in self.enabled_names():
            source_class = self._classes[name]
            source = factory(source_class)
            if not isinstance(source, source_class):
                raise TypeError(
                    f"factory returned {type(source).__name__} for source {name!r}, "
                    f"expected {source_class.__name__}"
                )
            sources.append(source)
        return tuple(sources)

    @property
    def unknown_config_names(self) -> tuple[str, ...]:
        """Configured names no registered source declares (typo or deferred source)."""
        return tuple(sorted(set(self._config) - set(self._classes)))

    def _add(self, source_class: type[BaseLeadSource]) -> None:
        name = source_class.name
        key = _collision_key(name)
        if key in self._collision_keys:
            raise DuplicateSourceNameError(name)
        self._collision_keys.add(key)
        self._classes[name] = source_class


def _mode_for(
    source_class: type[BaseLeadSource],
    settings: SourceSettings,
    resolve_mode: ModeResolver | None,
) -> DataMode:
    if resolve_mode is None:
        return settings.mode or DataMode.SYNTHETIC  # the no-credentials default (4.1)
    # The resolver runs even for an overridden source so it can log (4.4); the
    # per-source override still has the last word here, whatever a resolver says.
    mode = resolve_mode(source_class, settings)
    if not isinstance(mode, DataMode):
        raise TypeError(
            f"mode resolver returned {mode!r} for source {source_class.name!r}, "
            "expected a DataMode"
        )
    return settings.mode or mode


def _import(name: str) -> ModuleType:
    try:
        return importlib.import_module(name)
    except Exception as exc:
        raise SourceDiscoveryError(
            name, detail=f"import failed: {type(exc).__name__}: {exc}"
        ) from exc


def _import_package(package: str | ModuleType) -> ModuleType:
    module = _import(package) if isinstance(package, str) else package
    if not hasattr(module, "__path__"):
        raise SourceDiscoveryError(module.__name__, detail="not a package")
    return module


def _scan(package: ModuleType) -> Iterator[ModuleType]:
    for info in pkgutil.iter_modules(package.__path__, f"{package.__name__}."):
        module = _import(info.name)
        yield module
        if info.ispkg:
            yield from _scan(module)


def _defined_sources(module: ModuleType) -> Iterator[type[BaseLeadSource]]:
    for obj in list(vars(module).values()):
        # __module__ filter: a class re-exported from another module is registered
        # once, by the module that defines it.
        if (
            not isinstance(obj, type)
            or not issubclass(obj, BaseLeadSource)
            or obj is BaseLeadSource
            or obj.__module__ != module.__name__
        ):
            continue
        if inspect.isabstract(obj):
            if "name" in vars(obj):
                missing = ", ".join(sorted(obj.__abstractmethods__))
                raise SourceDiscoveryError(
                    module.__name__,
                    detail=f"{obj.__name__} declares a name but is abstract: "
                    f"implement {missing}",
                )
            continue  # an intermediate base, not a source
        name = getattr(obj, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise SourceDiscoveryError(
                module.__name__,
                detail=f"{obj.__name__} must declare a non-blank str name, "
                f"got {name!r}",
            )
        yield obj
