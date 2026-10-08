"""``DemoTransport``: answers each request from the demo tables, routed by its params.

The fixture transport serves one file per endpoint whatever was asked. This one looks
the request up: Apollo search by technology UID and page, Apollo match by id, LinkedIn
URL, email or name; HubSpot contact search by the email filter (primary address or
``hs_additional_emails``) and deal search by contact and ``hs_is_closed``; Hunter by
domain, name and domain, or address; SerpApi by the domain quoted in ``q``. Bodies
keep their documented shape, so the adapters validate and normalize them unchanged.

Like the fixture transport it holds no socket and refuses send-capable endpoints.
Each answer is a deep copy: an adapter can never alter the table. ``DemoLog`` counts
the requests per endpoint, so the scorecard can show which sources were reached.
The first Apollo match call is always answered with Apollo's documented 429
(``error_details.code`` ``USAGE.RATE_LIMIT.API_RATE_LIMIT_EXCEEDED``, context window
``minute``), every later one normally: the run must classify it as rate limiting,
retry and carry on. ``faults=True`` also serves the scripted SerpApi failure.

HubSpot assumption: an EQ filter on ``email`` matching an address held in
``hs_additional_emails`` is how the contacts API treats extra addresses (they are
unique identifiers); the search page does not say so for search.
"""

import copy
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Endpoint,
    TransportFactory,
)
from leadforge.lead_ingestion.demo.generator import DATA_DIR, Json, load
from leadforge.lead_ingestion.errors import UndeclaredEndpointError
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.send_prohibition import assert_no_send_capable_endpoints
from leadforge.lead_ingestion.transport import Transport, TransportResponse

__all__ = ["DemoLog", "DemoPages", "DemoTransport", "demo_transport_factory"]

_QUOTED = re.compile(r'"([^"]+)"')
_SITE = re.compile(r"site:([^\s)/\"]+)")
_ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com")
_CONTACT_DEFAULTS = ("createdate", "hs_object_id", "lastmodifieddate")
_DEAL_DEFAULTS = (
    "amount",
    "closedate",
    "createdate",
    "dealname",
    "dealstage",
    "hs_lastmodifieddate",
    "hs_object_id",
    "pipeline",
)
_EMPTY_SEARCH = "Google hasn't returned any results for this query."
# Apollo's 429 body: error_details per https://docs.apollo.io/reference/people-enrichment
# (read 2026-10-07); the legacy root fields are deprecated and left out.
_APOLLO_RATE_LIMITED: Json = {
    "error_details": {
        "code": "USAGE.RATE_LIMIT.API_RATE_LIMIT_EXCEEDED",
        "message": "The maximum number of api calls allowed for "
        "api/v1/people/match is 600 times per hour.",
        "suggestions": ["Wait for the window to reset, or upgrade your plan."],
        "context": {"window": "minute"},
    }
}


@dataclass
class DemoLog:
    """Requests served, per ``<provider>.<endpoint name>``, and Google's domains."""

    requests: Counter[str] = field(default_factory=Counter)
    searched_domains: set[str] = field(default_factory=set)

    def as_json(self) -> Json:
        return {
            "requests": dict(sorted(self.requests.items())),
            "searched_domains": sorted(self.searched_domains),
        }


class _Tables:
    """The four tables, indexed for lookup once per run."""

    def __init__(self, directory: Path) -> None:
        apollo = load("apollo", directory)
        self.apollo_records: list[Json] = apollo["records"]
        self.apollo_by_key: dict[str, Json] = {}
        for record in self.apollo_records:
            answer = record["enrichment"]
            person = answer.get("person") or {}
            search = record["search_result"]
            self.apollo_by_key[f"id:{search['id']}"] = answer
            if person.get("match_confidence", "none") == "none":
                continue
            for key in _person_keys(person):
                self.apollo_by_key.setdefault(key, answer)
        hubspot = load("hubspot", directory)
        self.contacts: list[Json] = hubspot["contacts"]
        self.deals: list[Json] = hubspot["deals"]
        self.hunter = load("hunter", directory)
        self.google = load("google_search", directory)
        self.pages: Json = load("pages", directory)
        self.families: dict[str, Json] = self.google.get("families", {})
        self.family_by_name = {
            entry["name"].lower(): domain for domain, entry in self.families.items()
        }


def _person_keys(person: Mapping[str, Any]) -> list[str]:
    keys = []
    if person.get("linkedin_url"):
        keys.append(f"linkedin:{_linkedin(person['linkedin_url'])}")
    if person.get("email"):
        keys.append(f"email:{str(person['email']).lower()}")
    org = person.get("organization") or {}
    name = f"{person.get('first_name', '')}|{person.get('last_name', '')}".lower()
    if org.get("primary_domain"):
        keys.append(f"name_domain:{name}|{org['primary_domain']}")
    if org.get("name"):
        keys.append(f"name_org:{name}|{str(org['name']).lower()}")
    return keys


def _record_domain(record: Mapping[str, Any]) -> str:
    """The employer domain of an Apollo table record."""
    if record.get("domain"):
        return str(record["domain"]).lower()
    person = record["enrichment"].get("person") or {}
    org = person.get("organization") or {}
    return str(org.get("primary_domain") or "").lower()


def _linkedin(url: str) -> str:
    """The profile slug, however the URL is written (scheme, www, slash, query)."""
    path = url.split("?")[0].rstrip("/")
    return path.rsplit("/", 1)[-1].lower()


class DemoTransport:
    """``Transport`` over the demo tables for one provider."""

    def __init__(
        self,
        provider: str,
        endpoints: Mapping[str, Endpoint],
        *,
        tables: _Tables,
        log: DemoLog,
        faults: bool = False,
    ) -> None:
        assert_no_send_capable_endpoints(provider, endpoints)
        self._provider = provider
        self._names = {endpoint: name for name, endpoint in endpoints.items()}
        self._tables = tables
        self._log = log
        self._faults = faults
        self._request_id = 0
        self._rate_limited = False

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        name = self._names.get(endpoint)
        if name is None:
            raise UndeclaredEndpointError(self._provider, path=endpoint.path)
        self._log.requests[f"{self._provider}.{name}"] += 1
        query = dict(params or {})
        body = dict(json_body or {})
        path = endpoint.path
        if path.endswith("/mixed_people/api_search"):
            status, answer = 200, self._apollo_search(query)
        elif path.endswith("/people/match") and not self._rate_limited:
            self._rate_limited = True
            self._log.requests["apollo.match_429"] += 1
            status, answer = 429, _APOLLO_RATE_LIMITED
        elif path.endswith("/people/match"):
            status, answer = 200, self._apollo_match(body)
        elif path.endswith("/contacts/search"):
            status, answer = 200, self._contact_search(body)
        elif path.endswith("/deals/search"):
            status, answer = 200, self._deal_search(body)
        elif path.endswith("/domain-search"):
            status, answer = 200, self._domain_search(query)
        elif path.endswith("/email-finder"):
            status, answer = 200, self._email_finder(query)
        elif path.endswith("/email-verifier"):
            status, answer = self._email_verifier(query)
        elif path == "/search":
            status, answer = 200, self._google(query)
        else:
            raise UndeclaredEndpointError(self._provider, path=path)
        return TransportResponse(status=status, headers={}, body=copy.deepcopy(answer))

    # Apollo ---------------------------------------------------------------------

    def _apollo_search(self, query: Mapping[str, object]) -> Json:
        uid = str(query.get("currently_using_any_of_technology_uids[]", ""))
        domain = str(query.get("q_organization_domains_list[]", ""))
        page = int(str(query.get("page", 1)))
        per_page = int(str(query.get("per_page", 100)))
        if domain:
            found = [
                r["search_result"]
                for r in self._tables.apollo_records
                if _record_domain(r) == domain.lower()
            ]
        else:
            found = [
                r["search_result"]
                for r in self._tables.apollo_records
                if uid in r["technology_uids"]
            ]
        start = (page - 1) * per_page
        return {"total_entries": len(found), "people": found[start : start + per_page]}

    def _apollo_match(self, body: Mapping[str, object]) -> Json:
        key = _match_key(body)
        answer = self._tables.apollo_by_key.get(key) if key else None
        if answer is None:
            return {"person": {"match_confidence": "none"}, "request_id": 1}
        return answer

    # HubSpot --------------------------------------------------------------------

    def _contact_search(self, body: Mapping[str, object]) -> Json:
        wanted = (_filter_value(body, "email") or "").lower()
        properties = [str(p) for p in _list(body.get("properties"))]
        found = [
            _project(contact, [*properties, *_CONTACT_DEFAULTS])
            for contact in self._tables.contacts
            if _holds_address(contact, wanted)
        ]
        limit = int(str(body.get("limit", 10)))
        return {"total": len(found), "results": found[:limit]}

    def _deal_search(self, body: Mapping[str, object]) -> Json:
        contact = _filter_value(body, "associations.contact")
        closed = _filter_value(body, "hs_is_closed")
        found = [
            _project(deal, _DEAL_DEFAULTS)
            for deal in self._tables.deals
            if any(
                a["id"] == contact for a in deal["associations"]["contacts"]["results"]
            )
            and (closed is None or deal["properties"]["hs_is_closed"] == closed)
        ]
        limit = int(str(body.get("limit", 10)))
        return {"total": len(found), "results": found[:limit]}

    # Hunter ---------------------------------------------------------------------

    def _domain_search(self, query: Mapping[str, object]) -> Json:
        domain = str(query.get("domain", "")).lower()
        found: Json | None = self._tables.hunter["domain_search"].get(domain)
        if found is not None:
            return found
        return {
            "data": {
                "domain": None,
                "disposable": False,
                "webmail": False,
                "accept_all": False,
                "pattern": None,
                "organization": None,
                "linked_domains": [],
                "emails": [],
            },
            "meta": {
                "results": 0,
                "results_approximate": False,
                "limit": 100,
                "offset": 0,
                "params": {"domain": domain},
            },
        }

    def _email_finder(self, query: Mapping[str, object]) -> Json:
        wanted = (
            str(query.get("first_name", "")).lower(),
            str(query.get("last_name", "")).lower(),
            str(query.get("domain", "")).lower(),
        )
        for answer in self._tables.hunter["email_finder"]:
            p = answer["meta"]["params"]
            if (p["first_name"].lower(), p["last_name"].lower(), p["domain"]) == wanted:
                return dict(answer)
        first, last, domain = wanted
        return {
            "data": {
                "first_name": first,
                "last_name": last,
                "email": None,
                "score": None,
                "domain": domain,
                "sources": [],
            },
            "meta": {"params": {"first_name": first, "last_name": last}},
        }

    def _email_verifier(self, query: Mapping[str, object]) -> tuple[int, Json]:
        email = str(query.get("email", "")).lower()
        for fault in self._tables.hunter["faults"]:
            if fault["email"] == email:
                return int(fault["status"]), fault["body"]
        found: Json | None = self._tables.hunter["email_verifier"].get(email)
        if found is not None:
            return 200, found
        return 200, {
            "data": {"status": "unknown", "score": 0, "email": email},
            "meta": {"params": {"email": email}},
        }

    # Google (SerpApi) -----------------------------------------------------------

    def _family_domain(self, q: str) -> str | None:
        """The company a family query is about: a quoted name, else a ``site:`` host."""
        for name in _QUOTED.findall(q):
            if (domain := self._tables.family_by_name.get(name.lower())) is not None:
                return domain
        for host in _SITE.findall(q.lower()):
            bare = host.removeprefix("www.")
            if bare in self._tables.families:
                return bare
        return None

    def _family_results(self, q: str) -> list[Json] | None:
        """The results of one query family (Req 4.1) for the company the query names.

        The family follows from the query: ``site:linkedin.com`` is LinkedIn,
        ``site:github.com`` code, an ATS host or a careers path job postings, the
        company's own domain its site, any other ``site:`` the vendor's pages, and no
        ``site:`` third-party pages. A family with no proof answers empty.
        """
        domain = self._family_domain(q)
        if domain is None:
            return None
        lowered = q.lower()
        hosts = [h.removeprefix("www.") for h in _SITE.findall(lowered)]
        if "site:linkedin.com" in lowered:
            family = "linkedin_public"
        elif "site:github.com" in lowered:
            family = "code"
        elif any(a in lowered for a in _ATS_HOSTS) or "/careers" in lowered:
            family = "job_posting"
        elif domain in hosts:
            family = "own_site"
        elif hosts:
            family = "vendor_customer"
        else:
            family = "third_party"
        found = self._tables.families[domain]["results"].get(family, [])
        return list(found)

    def _google(self, query: Mapping[str, object]) -> Json:
        q = str(query.get("q", ""))
        quoted = _QUOTED.search(q)
        domain = quoted.group(1).lower() if quoted else ""
        family_results: list[Json] | None = None
        if domain not in self._tables.google["by_domain"]:
            family_results = self._family_results(q)  # not an ingestion anchor query
            domain = self._family_domain(q) or domain
        self._log.searched_domains.add(domain)
        self._request_id += 1
        metadata = {
            "id": f"demo{self._request_id:020d}",
            "status": "Success",
            "json_endpoint": f"https://serpapi.com/searches/demo/{self._request_id}.json",
            "created_at": "2026-10-07 09:00:00 UTC",
            "processed_at": "2026-10-07 09:00:00 UTC",
            "google_url": "https://www.google.com/search?q=demo",
            "raw_html_file": f"https://serpapi.com/searches/demo/{self._request_id}.html",
            "total_time_taken": 1.12,
        }
        parameters = {
            "engine": "google",
            "q": q,
            "google_domain": "google.com",
            "device": "desktop",
        }
        if self._faults:
            for fault in self._tables.google["faults"]:
                if fault["domain"] == domain:
                    return {
                        "search_metadata": {**metadata, "status": fault["status"]},
                        "search_parameters": parameters,
                        "error": fault["error"],
                    }
        entry = self._tables.google["by_domain"].get(domain)
        results: list[Json] = (
            family_results
            if family_results is not None
            else ([] if entry is None else entry["organic_results"])
        )
        if not results or int(str(query.get("start", 0) or 0)) > 0:
            return {
                "search_metadata": metadata,
                "search_parameters": parameters,
                "search_information": {
                    "query_displayed": q,
                    "organic_results_state": "Fully empty",
                },
                "error": _EMPTY_SEARCH,
            }
        return {
            "search_metadata": metadata,
            "search_parameters": parameters,
            "search_information": {
                "query_displayed": q,
                "total_results": len(results) * 1370,
                "time_taken_displayed": 0.41,
                "organic_results_state": "Results for exact spelling",
            },
            "organic_results": results,
        }


class DemoPages:
    """Serves the proof pages and ``robots.txt`` of the synthetic usage flow.

    ``fetch(url)`` answers ``(status, body)``: the stored page, the shared
    ``robots_txt`` for any ``/robots.txt``, 404 for anything else. It never reaches a
    socket, and a ``linkedin.com`` URL is always 404 (the demo holds no LinkedIn page).
    Each call is counted as ``pages.fetch`` in the log.
    """

    def __init__(self, tables: _Tables, log: DemoLog) -> None:
        self._pages: Mapping[str, str] = tables.pages["pages"]
        self._robots: str = tables.pages["robots_txt"]
        self._log = log

    def fetch(self, url: str) -> tuple[int, str]:
        self._log.requests["pages.fetch"] += 1
        if url.split("?")[0].endswith("/robots.txt"):
            return 200, self._robots
        body = self._pages.get(url)
        return (404, "") if body is None else (200, body)


def _match_key(body: Mapping[str, object]) -> str | None:
    """The table key of an Apollo match request, by the rung it climbs."""
    if body.get("id"):
        return f"id:{body['id']}"
    if body.get("linkedin_url"):
        return f"linkedin:{_linkedin(str(body['linkedin_url']))}"
    if body.get("email"):
        return f"email:{str(body['email']).lower()}"
    name = f"{body.get('first_name', '')}|{body.get('last_name', '')}".lower()
    if body.get("domain"):
        return f"name_domain:{name}|{body['domain']}"
    if body.get("organization_name"):
        return f"name_org:{name}|{str(body['organization_name']).lower()}"
    return None


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _filter_value(body: Mapping[str, object], prop: str) -> str | None:
    for group in _list(body.get("filterGroups")):
        if not isinstance(group, Mapping):
            continue
        for f in _list(group.get("filters")):
            if isinstance(f, Mapping) and f.get("propertyName") == prop:
                return str(f.get("value"))
    return None


def _holds_address(contact: Mapping[str, Any], wanted: str) -> bool:
    props = contact["properties"]
    extra = str(props.get("hs_additional_emails") or "").lower().split(";")
    return bool(wanted) and (
        str(props.get("email", "")).lower() == wanted or (wanted in extra)
    )


def _project(record: Mapping[str, Any], properties: Iterable[str]) -> Json:
    """The record as search returns it: only the asked and default properties."""
    props = record["properties"]
    return {
        "id": record["id"],
        "properties": {p: props.get(p) for p in sorted(set(properties))},
        "createdAt": record["createdAt"],
        "updatedAt": record["updatedAt"],
        "archived": record["archived"],
    }


def demo_transport_factory(
    *, directory: Path = DATA_DIR, faults: bool = False, log: DemoLog | None = None
) -> tuple[TransportFactory, DemoLog]:
    """A ``TransportFactory`` over the demo tables, and the log it fills.

    Live mode is refused: the demo never reaches a provider.
    """
    tables = _Tables(directory)
    shared = DemoLog() if log is None else log

    def build(source_class: type[BaseLeadSource], mode: DataMode) -> Transport:
        if mode is not DataMode.SYNTHETIC:
            raise ValueError(
                f"the demo runs synthetic only; {source_class.name} is live"
            )
        return DemoTransport(
            source_class.name,
            source_class.endpoints,
            tables=tables,
            log=shared,
            faults=faults,
        )

    return build, shared
