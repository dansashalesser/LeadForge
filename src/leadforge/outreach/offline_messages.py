"""Write Messages from template files, with no model and no network (requirement 7.6).

Used while no model key is set. The wording lives in ``template_files/`` (versioned
like the prompts); this module only chooses fragments and fills them from the Lead's
own facts, recording a claim for every fact it states so the same checks that judge a
model's Message judge this one. Each Draft says ``offline``.
"""

import re
from pathlib import Path

from leadforge.outreach.facts import Fact, FactKind, LeadFacts
from leadforge.outreach.message_checks import Claim, Draft

__all__ = ["OFFLINE_LABEL", "TEMPLATES_DIR", "OfflineWriter"]

TEMPLATES_DIR = Path(__file__).resolve().parent / "template_files"
OFFLINE_LABEL = "offline"
_INVITE = "invite_offline_v2"
_EMAIL = "email_offline_v2"
_LINES = "lines_offline_v2"
# A product in use is the most specific thing a template can state, so it leads. The
# ``usage`` quote behind it is deliberately absent: see ``_hook``.
_HOOK_ORDER: tuple[FactKind, ...] = ("product", "tech", "intent", "evidence")


class OfflineWriter:
    """Writes the invite and the email for one Lead from the template files."""

    def __init__(self, directory: Path = TEMPLATES_DIR) -> None:
        self._invite = (directory / f"{_INVITE}.txt").read_text(encoding="utf-8")
        self._email = (directory / f"{_EMAIL}.txt").read_text(encoding="utf-8")
        self._lines = _read_lines(directory / f"{_LINES}.txt")

    def write(self, facts: LeadFacts) -> tuple[Draft, Draft]:
        """``(invite, email)``; too few facts yield Drafts the checks then fail."""
        name = facts.get("name")
        first = (
            name.value.split()[0] if name is not None else self._lines["greeting.none"]
        )
        company = facts.get("company")
        hook_line, hook_claims, hook_kind = self._hook(facts, company)
        # A product in use already says something specific. Reading their title back at
        # them as well is what makes a Message sound assembled, so the role gives way.
        role_line, role_claims = (
            ("", ()) if hook_kind == "product" else self._role(facts)
        )
        claims = (
            tuple([Claim(fact_id="name", text=first)] if name is not None else [])
            + role_claims
            + hook_claims
        )
        fill = {
            "first_name": first,
            "role_line": role_line,
            "hook_line": hook_line,
        }
        invite = _tidy(_fill(self._invite, fill))
        subject, _, body = _fill(self._email, fill).partition("\n\n")
        email = Draft(
            kind="email",
            subject=subject.removeprefix("Subject:").strip(),
            body=_tidy(body),
            claims=claims,
            generator="offline",
            model=OFFLINE_LABEL,
            prompt_version=_EMAIL,
        )
        return (
            Draft(
                kind="invite",
                subject=None,
                body=invite,
                claims=claims,
                generator="offline",
                model=OFFLINE_LABEL,
                prompt_version=_INVITE,
            ),
            email,
        )

    def _role(self, facts: LeadFacts) -> tuple[str, tuple[Claim, ...]]:
        title, company = facts.get("title"), facts.get("company")
        if title is not None and company is not None:
            key = "role.title_company"
        elif title is not None:
            key = "role.title"
        elif company is not None:
            key = "role.company"
        else:
            return self._lines["role.none"] + " ", ()
        used = [f for f in (title, company) if f is not None]
        text = self._lines[key]
        for fact in used:
            text = text.replace("{" + fact.kind + "}", fact.value)
        return text + " ", tuple(Claim(fact_id=f.id, text=f.value) for f in used)

    def _hook(
        self, facts: LeadFacts, company: Fact | None
    ) -> tuple[str, tuple[Claim, ...], FactKind | None]:
        """The strongest hook line, its claim, and the kind of fact it came from.

        The published sentence behind a product is never offered here. Somebody else
        wrote it, usually about the company rather than to this person, and a template
        can neither read it nor say anything true about it: quoting it and adding a
        remark produces a Message that means nothing. Only the model writer, which can
        read it, is given it. A template states the thing itself instead.
        """
        for kind in _HOOK_ORDER:
            chosen: Fact | None = next(iter(facts.of_kind(kind)), None)
            # A product line names the employer, so without one it has no sentence.
            if chosen is None or (kind == "product" and company is None):
                continue
            key = f"hook.{kind}"
            if chosen.variant is not None:
                key = f"{key}.{chosen.variant}"
            text = self._lines[key].replace("{value}", chosen.value)
            if company is not None:
                text = text.replace("{company}", company.value)
            return text + " ", (Claim(fact_id=chosen.id, text=chosen.value),), kind
        return "", (), None


def _read_lines(path: Path) -> dict[str, str]:
    lines: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip() and not raw.startswith("#"):
            key, _, text = raw.partition(":")
            lines[key.strip()] = text.strip()
    return lines


def _fill(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def _tidy(text: str) -> str:
    """Trim each line's trailing spaces and collapse doubled spaces."""
    lines = [re.sub(r" {2,}", " ", line).rstrip() for line in text.strip().splitlines()]
    return "\n".join(lines)
