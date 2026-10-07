"""Compare the leads a demo run stored with the answer key, scenario by scenario.

Every person in the key is looked up among the active leads by LinkedIn profile, then
by address. Each expectation the key states is one check; a check the run could not
reach (web evidence for a company the query cap left unsearched) is skipped, not
failed. Run-level findings sit beside the per-scenario table: requests per provider
(a source that was never asked shows 0), leads with no person behind them, and
addresses held by more than one lead.

The output names scenarios, counts and check names only, never a lead's values.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from leadforge.lead_ingestion.demo.generator import Json
from leadforge.lead_ingestion.store.lead_reader import StoredLead, list_leads

__all__ = [
    "PersonResult",
    "Scorecard",
    "load_active_leads",
    "person_results",
    "render",
    "score",
]

_TITLE_BOUND = 4000
_PAGE = 200


@dataclass
class ScenarioResult:
    people: int = 0
    passed: int = 0
    failed: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)


@dataclass
class Scorecard:
    scenarios: dict[str, ScenarioResult]
    notes: dict[str, str]
    leads_active: int
    leads_expected: int
    leads_without_subject: int
    shared_address_leads: int
    requests: dict[str, int]
    companies: int
    companies_searched: int

    @property
    def checks_passed(self) -> int:
        return sum(r.passed for r in self.scenarios.values())

    @property
    def checks_failed(self) -> int:
        return sum(sum(r.failed.values()) for r in self.scenarios.values())


def load_active_leads(session: Session) -> list[StoredLead]:
    leads: list[StoredLead] = []
    after = None
    while page := list_leads(session, limit=_PAGE, after=after):
        leads.extend(page)
        after = page[-1].lead_id
    return leads


def _slug(url: object) -> str | None:
    if url is None:
        return None
    return str(url).split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()


def _email(value: object) -> str | None:
    return None if value is None else str(value).lower()


class _Index:
    def __init__(self, leads: Iterable[StoredLead]) -> None:
        self.by_linkedin: dict[str, StoredLead] = {}
        self.by_email: defaultdict[str, list[StoredLead]] = defaultdict(list)
        self.by_name: defaultdict[str, list[StoredLead]] = defaultdict(list)
        for stored in leads:
            lead = stored.lead
            slug = _slug(lead.linkedin_url)
            if slug:
                self.by_linkedin[slug] = stored
            if lead.email:
                self.by_email[str(lead.email).lower()].append(stored)
            if lead.full_name:
                self.by_name[str(lead.full_name).lower()].append(stored)

    def find(self, expect: Json) -> StoredLead | None:
        slug = _slug(expect.get("linkedin_url"))
        if slug and slug in self.by_linkedin:
            return self.by_linkedin[slug]
        email = _email(expect.get("email"))
        candidates = self.by_email.get(email, []) if email else []
        named = [c for c in candidates if c.lead.full_name]
        chosen = named or candidates
        return chosen[0] if chosen else None


def _attachments(stored: StoredLead) -> set[str]:
    return {str(e.attachment) for e in stored.web_evidence}


def _checks(
    expect: Json,
    stored: StoredLead,
    leads_of: dict[str, StoredLead | None],
    searched: set[str],
    company: str,
) -> Iterable[tuple[str, bool | None]]:
    """``(check, outcome)``; an outcome of None means the run could not reach it."""
    lead = stored.lead
    yield "full_name", lead.full_name == expect["full_name"]
    yield "email", _email(lead.email) == _email(expect["email"])
    yield "email_status", str(lead.email_status) == expect["email_status"]
    yield (
        "role_address_flag",
        lead.email_is_role_address == expect["email_is_role_address"],
    )
    yield "opt_out", lead.opt_out == expect["opt_out"]
    yield "suppressed", lead.suppressed == expect["suppressed"]
    if "web_evidence" in expect:
        wanted = expect["web_evidence"]
        attached = _attachments(stored) - {"unattached"}
        if company not in searched:
            yield "web_evidence", None
        elif wanted == "none":
            yield "web_evidence", not attached
        else:
            yield "web_evidence", wanted in attached
    if expect.get("crm_open_deal"):
        paths = {p.canonical_path for p in stored.provenance}
        yield "crm_open_deal_on_lead", "crm.has_open_deal" in paths
    if "same_lead_as" in expect:
        twin = leads_of.get(expect["same_lead_as"])
        yield (
            "merged_with_duplicate",
            twin is not None and twin.lead_id == (stored.lead_id),
        )
    if "different_lead_from" in expect:
        twin = leads_of.get(expect["different_lead_from"])
        yield "kept_apart", twin is None or twin.lead_id != stored.lead_id
    if expect.get("title_bounded"):
        titles = [e.title or "" for e in lead.employments]
        yield "title_bounded", bool(titles) and max(map(len, titles)) <= _TITLE_BOUND
    if expect.get("primary_domain_tie"):
        source = stored.primary_domain_source
        yield (
            "primary_domain_tie_flagged",
            source is not None and (str(source) != "not_tied"),
        )


@dataclass(frozen=True)
class PersonResult:
    """One answer-key person: the lead found for them and each check's outcome."""

    subject: str
    scenario: str
    company: str
    lead: StoredLead | None
    # (check, outcome); an outcome of None means the run could not reach it.
    checks: tuple[tuple[str, bool | None], ...]


def _person_results(
    index: _Index, people: list[Json], searched: set[str]
) -> list[PersonResult]:
    leads_of: dict[str, StoredLead | None] = {
        p["subject"]: index.find(p["expect"])
        for p in people
        if p["expect"]["lead"] == "present"
    }
    out = []
    for person in people:
        expect = person["expect"]
        stored: StoredLead | None = None
        checks: tuple[tuple[str, bool | None], ...]
        if expect["lead"] == "absent":
            name = str(person.get("name", "")).lower()
            checks = (("no_lead_expected", not (name and index.by_name.get(name))),)
        else:
            stored = leads_of[person["subject"]]
            checks = (
                (("lead_found", False),)
                if stored is None
                else tuple(
                    _checks(expect, stored, leads_of, searched, person["company"])
                )
            )
        out.append(
            PersonResult(
                person["subject"], person["scenario"], person["company"], stored, checks
            )
        )
    return out


def person_results(leads: list[StoredLead], key: Json, log: Json) -> list[PersonResult]:
    """Per answer-key person, in key order: the lead found and every check."""
    return _person_results(
        _Index(leads), key["people"], set(log.get("searched_domains", []))
    )


def score(leads: list[StoredLead], key: Json, log: Json) -> Scorecard:
    index = _Index(leads)
    people: list[Json] = key["people"]
    searched = set(log.get("searched_domains", []))
    persons = _person_results(index, people, searched)
    results: dict[str, ScenarioResult] = defaultdict(ScenarioResult)
    for person in persons:
        result = results[person.scenario]
        result.people += 1
        for check, outcome in person.checks:
            if outcome is None:
                result.skipped[check] += 1
            elif outcome:
                result.passed += 1
            else:
                result.failed[check] += 1
    matched = {p.lead.lead_id for p in persons if p.lead is not None}
    # One lead per present person, a duplicate pair counted once.
    expected_leads = len(
        {
            min(p["subject"], p["expect"].get("same_lead_as") or p["subject"])
            for p in people
            if p["expect"]["lead"] == "present"
        }
    )
    shared = sum(len(group) for group in index.by_email.values() if len(group) > 1)
    return Scorecard(
        scenarios=dict(sorted(results.items())),
        notes=key["scenarios"],
        leads_active=len(leads),
        leads_expected=expected_leads,
        leads_without_subject=sum(1 for s in leads if s.lead_id not in matched),
        shared_address_leads=shared,
        requests=dict(log.get("requests", {})),
        companies=len(key["companies"]),
        companies_searched=len(searched & set(key["companies"])),
    )


def _row(cells: Iterable[Any], widths: Iterable[int]) -> str:
    return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths, strict=True))


def render(card: Scorecard) -> str:
    widths = (32, 6, 7, 6, 7)
    lines = [
        "Demo scorecard: stored leads vs answer key",
        "",
        _row(("scenario", "people", "passed", "failed", "skipped"), widths)
        + "  failing checks",
    ]
    for name, r in card.scenarios.items():
        failing = ", ".join(f"{c} x{n}" for c, n in r.failed.most_common())
        skipped = ", ".join(f"{c} x{n}" for c, n in r.skipped.most_common())
        lines.append(
            _row(
                (
                    name,
                    r.people,
                    r.passed,
                    sum(r.failed.values()),
                    sum(r.skipped.values()),
                ),
                widths,
            )
            + f"  {failing or '-'}"
            + (f"  (skipped: {skipped})" if skipped else "")
        )
    total = card.checks_passed + card.checks_failed
    lines += [
        "",
        f"checks: {card.checks_passed}/{total} passed, {card.checks_failed} failed",
        f"leads: {card.leads_active} active, {card.leads_expected} expected; "
        f"{card.leads_without_subject} match no person in the key; "
        f"{card.shared_address_leads} share an address with another lead",
        f"web evidence: {card.companies_searched} of {card.companies} companies "
        "searched"
        + (
            " (the rest were past the per-run query cap)"
            if card.companies_searched < card.companies
            else ""
        ),
        "requests served: "
        + (
            ", ".join(f"{k}={v}" for k, v in card.requests.items())
            if card.requests
            else "none recorded"
        ),
    ]
    providers = {k.split(".")[0] for k in card.requests}
    idle = [
        p
        for p in ("apollo", "hubspot", "hunter", "google_search")
        if p not in providers
    ]
    if idle:
        lines.append(f"never asked: {', '.join(idle)}")
    lines += ["", "scenarios:"]
    lines += [f"  {name}: {card.notes.get(name, '')}" for name in card.scenarios]
    return "\n".join(lines)
