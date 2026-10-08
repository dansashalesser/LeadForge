"""Polarity cue phrases for the offline classifier (`usage.cues` in config)."""

from pydantic import BaseModel, ConfigDict

__all__ = ["UsageCues"]


class UsageCues(BaseModel):
    """Polarity cue phrases (design defaults for `usage.cues`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    vendor_or_partner: tuple[str, ...] = (
        "partner of", "our partner", "reseller", "we are a vendor", "sponsored by",
    )  # fmt: skip
    used_past: tuple[str, ...] = (
        "migrated off", "moved off", "moved away from", "no longer use",
        "used to use", "switched from", "replaced", "sunset",
    )  # fmt: skip
    evaluating: tuple[str, ...] = (
        " vs ", "versus", "compared to", "comparison", "evaluating", "considering",
        "alternative", "proof of concept",
    )  # fmt: skip
    uses_now: tuple[str, ...] = (
        "we use", "we run", "we rely on", "powered by", "built on", "migrated to",
        "moved to", "switched to", "runs on", "in production", "our stack",
        "dependency",
    )  # fmt: skip
    injection: tuple[str, ...] = (
        "ignore previous", "ignore all", "ignore the above", "disregard",
        "system prompt", "mark as", "mark acme", "classify", "as customer",
    )  # fmt: skip
