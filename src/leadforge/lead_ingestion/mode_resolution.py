"""Mode Resolver: live versus synthetic, with a stated reason (task 8.1, Requirement 4).

Precedence, highest first (design, "Mode Resolver and Credential Registry"):

1. per-source override in configuration (honoured even with a credential present, 4.3)
2. global mode override (injected value; env var ``LEADFORGE_MODE`` is read by 8.2)
3. live-access classification ``UNAVAILABLE`` -> synthetic
4. every declared credential present -> live (4.2)
5. otherwise synthetic, naming the missing variables (4.1)

Provisional decisions (see choices.md, task 8.1):

* A blank or whitespace-only value is absent, as in ``resolve_credentials``.
* Reasons and log lines carry variable *names* only; values are never read into them.
* Every call emits exactly one ``data_mode_resolved`` line (4.4).
* The configured ``live_access`` override replaces the adapter's declaration.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

import structlog

from leadforge.lead_ingestion.base_source import BaseLeadSource, LiveAccess
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import ModeResolver, SourceSettings

__all__ = ["ModeResolution", "make_mode_resolver", "resolve_data_mode"]

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ModeResolution:
    """A resolved data mode and why. ``reason`` names variables, never values."""

    mode: DataMode
    reason: str


def _coerce_global(value: DataMode | str | None) -> DataMode | None:
    if value is None or isinstance(value, DataMode):
        return value
    if not isinstance(value, str):
        raise TypeError(
            f"global mode override must be a DataMode or str, got {value!r}"
        )
    text = value.strip().casefold()
    if not text:
        return None  # an empty LEADFORGE_MODE is "not set"
    try:
        return DataMode(text)
    except ValueError:
        allowed = ", ".join(m.value for m in DataMode)
        raise ValueError(
            f"global mode override must be one of {allowed}, got {value!r}"
        ) from None


def resolve_data_mode(
    source_class: type[BaseLeadSource],
    settings: SourceSettings,
    environ: Mapping[str, str] | None = None,
    *,
    global_override: DataMode | str | None = None,
) -> ModeResolution:
    """Resolve one source's mode and log the outcome once."""
    override = _coerce_global(global_override)
    result = _decide(source_class, settings, environ, override)
    _log.info(
        "data_mode_resolved",
        source=source_class.name,
        mode=result.mode.value,
        reason=result.reason,
    )
    return result


def _decide(
    source_class: type[BaseLeadSource],
    settings: SourceSettings,
    environ: Mapping[str, str] | None,
    global_override: DataMode | None,
) -> ModeResolution:
    if settings.mode is not None:
        return ModeResolution(
            settings.mode, f"per-source override: {settings.mode.value}"
        )
    if global_override is not None:
        return ModeResolution(
            global_override, f"global override: {global_override.value}"
        )
    if (settings.live_access or source_class.live_access) is LiveAccess.UNAVAILABLE:
        return ModeResolution(DataMode.SYNTHETIC, "live access unavailable")
    env = os.environ if environ is None else environ
    missing = [n for n in source_class.required_env if not env.get(n, "").strip()]
    if missing:
        return ModeResolution(
            DataMode.SYNTHETIC, f"missing credentials: {', '.join(missing)}"
        )
    if not source_class.required_env:
        return ModeResolution(DataMode.LIVE, "no credentials required")
    return ModeResolution(DataMode.LIVE, "all declared credentials present")


def make_mode_resolver(
    environ: Mapping[str, str] | None = None,
    *,
    global_override: DataMode | str | None = None,
) -> ModeResolver:
    """A ``ModeResolver`` for ``SourceRegistry.describe(resolve_mode=...)``.

    The registry seam returns only a ``DataMode``; callers wanting the reason call
    ``resolve_data_mode`` directly. The override is validated here, not per call.
    """
    override = _coerce_global(global_override)

    def resolver(
        source_class: type[BaseLeadSource], settings: SourceSettings
    ) -> DataMode:
        return resolve_data_mode(
            source_class, settings, environ, global_override=override
        ).mode

    return resolver
