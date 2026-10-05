"""Compliance flags of a contribution: what is set, and whom it names (task 19.3; 11.4).

One reading of ``suppressed`` / ``opt_out`` shared by pruning (6.10) and projection
(8.x), so the two cannot disagree. Provisional decisions (choices.md, 19.3):

* Fail CLOSED, but bounded. A flag is ABSENT only when its value says "no" or nothing:
  ``None``, ``False``, a zero number, an empty or blank string or container, or text
  (any case, any surrounding whitespace) in ``_NO_TEXT`` (``"false"``, ``"no"``,
  ``"n"``, ``"f"``, ``"0"``, ``"off"``); an ``UntrustedText`` is judged by its text.
  EVERYTHING else is SET: ``True``, ``"true"``, ``"yes"``, ``1``, any other non-empty
  text such as ``"maybe"``, any other object. A Suppression that cannot be parsed is
  still a Suppression, and the cost of a false positive is one lead not contacted, but
  a value that says "no" must not suppress. Nothing is raised and no value is
  converted with ``str()`` or ``bool()``: a raise would drop the signal along with
  the batch. This is the ONLY reading of a flag: adapters, pruning and projection
  all call ``is_flag_set``.
* Monotone. Nothing here ever clears a flag; a flag is only ever added.
* A flag names a person by the identities its contribution carries: an email or a
  LinkedIn URL, under either path spelling (``person.*`` or bare). A report with neither
  (a name-only restriction) names no one here: matching by name and domain is Match Key
  work (8.3 requires corroboration) and is not done.
* ``blocked_identities`` maps each identity to the ``(flag, source_name)`` pairs that
  were set on it, so a projection can carry a flag onto a Lead another source supplied
  and say which source set it. Identities are personal data: no ``repr`` is made of
  the mapping here and nothing is logged.
"""

from collections.abc import Iterable, Mapping

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.match_keys import normalize_email, normalize_linkedin_url
from leadforge.lead_ingestion.models import UntrustedText

__all__ = [
    "COMPLIANCE_FLAGS",
    "Blocked",
    "Identity",
    "blocked_identities",
    "flags_set",
    "identities",
    "is_flag_set",
]

# Canonical-path names match ``CanonicalLead`` fields.
COMPLIANCE_FLAGS = ("suppressed", "opt_out")
# Adapters differ on the spelling: most write ``person.*``, one suppression report
# writes the bare ``email``. Each spelling names the same identity.
_EMAIL_PATHS = ("person.email", "email")
_LINKEDIN_PATHS = ("person.linkedin_url", "linkedin_url")

_NO_TEXT = frozenset({"false", "no", "n", "f", "0", "off"})

Identity = tuple[str, str]
Blocked = Mapping[Identity, frozenset[tuple[str, str]]]


def is_flag_set(value: object) -> bool:
    """Whether ``value`` sets a compliance flag; see the module docstring."""
    if isinstance(value, UntrustedText):
        value = value.value
    if value is None or value is False:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        text = value.strip().casefold()
        return bool(text) and text not in _NO_TEXT
    if isinstance(value, int | float):
        return value != 0  # NaN is not zero: set
    if isinstance(value, bytes | list | tuple | set | frozenset | dict):
        return len(value) > 0
    return True


def flags_set(contribution: LeadContribution) -> frozenset[str]:
    """The compliance flags ``contribution`` sets."""
    return frozenset(
        flag for flag in COMPLIANCE_FLAGS if is_flag_set(contribution.values.get(flag))
    )


def identities(contribution: LeadContribution) -> frozenset[Identity]:
    """Normalised ``(kind, value)`` pairs; a blank value names no lead."""
    pairs: set[Identity] = set()
    for kind, paths, normalise in (
        ("email", _EMAIL_PATHS, normalize_email),
        ("linkedin_url", _LINKEDIN_PATHS, normalize_linkedin_url),
    ):
        for path in paths:
            value = contribution.values.get(path)
            if isinstance(value, UntrustedText):
                value = value.value
            if not isinstance(value, str):
                continue
            try:
                key = normalise(value)
            except ValueError:  # an unparseable URL names no one; it never raises
                continue
            if key:
                pairs.add((kind, key))
    return frozenset(pairs)


def blocked_identities(contributions: Iterable[LeadContribution]) -> Blocked:
    """Each identity a flagged contribution names, with the flags set and by whom."""
    blocked: dict[Identity, set[tuple[str, str]]] = {}
    for contribution in contributions:
        flags = flags_set(contribution)
        if not flags:
            continue
        for identity in identities(contribution):
            blocked.setdefault(identity, set()).update(
                (flag, contribution.source_name) for flag in flags
            )
    return {identity: frozenset(pairs) for identity, pairs in blocked.items()}
