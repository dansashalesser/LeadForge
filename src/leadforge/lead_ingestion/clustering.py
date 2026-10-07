"""Order-independent identity clustering (task 16.2; Requirements 8.8, 8.9).

A pure function from contributions to ``IdentityCluster`` values: no I/O, no globals,
input not mutated. Keys come from ``match_keys`` (task 16.1); clustering is union-find
over them, so the partition is the connected components of the key graph and cannot
depend on arrival order. Provisional decisions (choices.md, 16.2):

* Strength rules. LinkedIn: equal key merges. Verified email: equal key merges unless
  every member offered both carries a LinkedIn key (8.2 applies "when ``linkedin_url``
  is absent on either lead", so two leads that each name a LinkedIn profile never merge
  on email; a LinkedIn-less holder of the address joins all of them). Follow-up (user
  decision: different LinkedIn = different person): an address seen with two distinct
  LinkedIn URLs is disqualified (``match_keys``), so that holder can no longer bridge
  two LinkedIn identities, and a cannot-link guard in the union-find refuses any union
  of two distinct LinkedIn URLs as a final invariant. Name+domain (8.3): only a
  contribution with NO LinkedIn key (present or barred) takes part, and two merge only
  when they share a name+domain key AND ``corroborates`` (a shared title or a shared
  employer). Follow-up (user decision 2026-10-06, supersedes "no verified-email key
  either"): a stated email (``stated_email``, any status) no longer excludes a record,
  so a record with an email joins an email-less one; two DIFFERENT addresses never
  join. No bridge: a candidate value stated with two distinct addresses is disqualified
  (``match_keys``), and name+domain edges are applied per component of the
  name+domain graph, a component stating two or more distinct addresses linking
  nobody. Rejected: a pairwise email cannot-link in the union-find, which is greedy
  (the bare record joins whichever side sorts first) and refuses legitimate unions of
  sets that already hold a second address through a LinkedIn merge. User-directed fix
  (2026-10-06): the addresses compared are ``personal_email`` evidence and the test is
  ``emails_conflict``: a shared (role) address is not the person's own and counts for
  nothing, and a guess (any status but verified) beside one verified address is no
  conflict; two verified addresses, or two guesses with none verified, still are.
* Linear time. Corroboration is evaluated per shared title / employer bucket inside a
  key group, never over pairs; every bucket costs one union per member.
* Contribution identity is its canonical JSON (sorted keys, sorted sets), the one
  total order used for member order, cluster order and ids. Contributions that are
  byte-identical are indistinguishable and keep separate clusters when keyless.
* ``cluster_id`` is the sha256 of the canonical JSON of the cluster's earliest-fetched
  member (canonical JSON breaks ties), with ``-2``, ``-3`` ... appended to later
  clusters whose id would repeat. A later run only adds later fetches, so it keeps the
  id; only a record bridging two clusters can change one. It is a pseudonym of personal
  data (an unsalted hash a dictionary attack could test), so treat it as personal data:
  never log it or put it in a filename. It is not a persistent store id; matching a
  run to persisted identities (8.9) is the store's job and not built here.
* Keyless contributions are singleton clusters, never dropped.
* Identity Exclusions (16.6, 8.13) are a parameter: ``match_keys.IdentityExclusions``
  values are skipped at key extraction, so clusters stay a pure function of the
  contributions and the exclusions (order-independent). A barred key still counts as
  PRESENT for the LinkedIn-absent test of 8.2 and 8.3, so an exclusion only ever
  splits clusters, never merges (barring a LinkedIn URL must not turn its holder into
  an email bridge). The one-sided rule reads the stated address raw, so barring an
  address never changes who may join by name+domain. Rejected: exclusions naming
  cluster ids (pseudonyms that change) or pairs of identities with a split rule (the
  requirement bars values, and the over-merge rule stays global and order-free).
* Role-address disqualification (16.7, 8.14) is computed here from the whole set
  (``DisqualifiedAddresses``), not passed in: it is structural, needs no config, and a
  caller-supplied set could disagree with the contributions. Disqualified addresses
  are barred like exclusions (kind stays PRESENT). The over-merge detector (16.8) is
  not built and no parameter reserves a seam for it.
* Errors name types only; cluster and key data is personal data.
"""

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.match_keys import (
    DisqualifiedAddresses,
    IdentityExclusions,
    MatchKey,
    MatchKeyKind,
    MatchKeys,
    PersonalEmail,
    emails_conflict,
    extract_match_keys,
    linkedin_identity,
    personal_email,
    stated_email,
)

__all__ = [
    "IdentityCluster",
    "canonical_json",
    "canonical_value_json",
    "cluster_contributions",
]


@dataclass(frozen=True)
class IdentityCluster:
    """Contributions judged to be one person, in canonical order."""

    cluster_id: str
    contributions: tuple[LeadContribution, ...] = field(repr=False)
    # The Match Key kinds that linked members (task 16.12), strongest first; kinds,
    # never values. Empty for a singleton and for a hand-built cluster.
    merged_by: tuple[MatchKeyKind, ...] = ()
    # The Match Keys whose unions actually joined members (task 16.12 completion),
    # sorted strongest kind first then by value. Personal data: never in ``repr``;
    # only ``match_key_digest`` digests of them reach a log.
    linked_by: tuple[MatchKey, ...] = field(default=(), repr=False)
    # The members' stated addresses that the 8.14 pass over the whole clustered set
    # found shared or role-word (``DisqualifiedAddresses.addresses``: a role address
    # such as ``info@``). Projection prefers a personal address over these for the
    # Lead's email (user decision 2026-10-06). Personal data: never in ``repr``.
    role_addresses: frozenset[str] = field(default=frozenset(), repr=False)


class _UnionFind:
    """Union by size with path halving; elements are ``0..n-1``.

    ``labels`` (the normalised LinkedIn URL per element, ``None`` when absent) make a
    cannot-link constraint: a union that would put two distinct labels in one set is
    refused, so no set ever carries two LinkedIn identities.
    """

    def __init__(self, size: int, labels: Sequence[str | None] | None = None) -> None:
        self._parent = list(range(size))
        self._size = [1] * size
        self._kinds: list[set[MatchKeyKind]] = [set() for _ in range(size)]
        self._keys: list[set[MatchKey]] = [set() for _ in range(size)]
        self._label: list[str | None] = list(labels) if labels else [None] * size

    def kinds(self, item: int) -> set[MatchKeyKind]:
        return self._kinds[self.find(item)]

    def keys(self, item: int) -> set[MatchKey]:
        return self._keys[self.find(item)]

    def find(self, item: int) -> int:
        parent = self._parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(
        self,
        a: int,
        b: int,
        kind: MatchKeyKind | None = None,
        value: str | None = None,
    ) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        label_a, label_b = self._label[root_a], self._label[root_b]
        if label_a is not None and label_b is not None and label_a != label_b:
            return  # cannot-link: two LinkedIn identities are two people
        if self._size[root_a] < self._size[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        self._size[root_a] += self._size[root_b]
        self._kinds[root_a] |= self._kinds[root_b]
        self._keys[root_a] |= self._keys[root_b]
        self._label[root_a] = label_a or label_b
        if kind is not None:
            self._kinds[root_a].add(kind)
            if value is not None:
                self._keys[root_a].add(MatchKey(kind, value))


def canonical_json(contribution: LeadContribution) -> str:
    """A serialisation of ``contribution`` that does not depend on dict or set order."""
    dumped = contribution.model_dump(mode="python")
    # One provenance record per path (the model enforces it), so path order is moot.
    dumped["provenance"] = sorted(
        dumped["provenance"], key=lambda record: record["canonical_path"]
    )
    return json.dumps(
        _canonical(dumped),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=True,
    )


def canonical_value_json(value: Any) -> str:
    """The canonical serialisation of one contribution value (same rules as above)."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    return json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=True,
    )


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return _canonical(value.value)
    if isinstance(value, date):  # datetime is a date
        return value.isoformat()
    if isinstance(value, Mapping):
        return {_key_text(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    if isinstance(value, set | frozenset):
        return sorted(
            (_canonical(v) for v in value),
            key=lambda v: json.dumps(v, sort_keys=True),
        )
    raise TypeError(
        f"cannot serialise a contribution value of type {type(value).__name__}"
    )


def _key_text(key: object) -> str:
    if isinstance(key, str):
        return key
    raise TypeError(f"mapping keys must be text, got {type(key).__name__}")


def cluster_contributions(
    contributions: Iterable[LeadContribution],
    exclusions: IdentityExclusions | None = None,
) -> tuple[IdentityCluster, ...]:
    """Cluster ``contributions`` by Match Key; the result ignores arrival order.

    ``exclusions`` bars values from acting as a key (8.13); it can only split clusters.
    So does the structural rule that bars an address reported against two names (8.14)
    or a key value reported with two distinct LinkedIn URLs; no cluster ever holds two
    distinct normalised LinkedIn URLs.
    """
    # Canonical order first, so nothing downstream depends on arrival. Byte-identical
    # contributions are interchangeable, so their relative order is immaterial.
    items = sorted(
        ((canonical_json(c), c) for c in contributions), key=lambda pair: pair[0]
    )
    # 8.14: a first pass over the whole set, so an address is disqualified for every
    # contribution that carries it, whichever arrived first.
    shared = DisqualifiedAddresses.from_contributions(c for _, c in items)
    keys = [extract_match_keys(c, exclusions, shared) for _, c in items]
    # Final guard: unions run in canonical order, so what it refuses is a function of
    # the set. With single-valued LinkedIn and email per contribution the pass above
    # already removes every bridging key, so it never fires today; it is the backstop.
    forest = _UnionFind(len(items), [linkedin_identity(c.values) for _, c in items])
    _link_linkedin(keys, forest)
    _link_email(keys, forest)
    personal = [personal_email(c.values, shared) for _, c in items]
    _link_name_domain(keys, personal, forest)

    members: dict[int, list[int]] = defaultdict(list)
    for index in range(len(items)):
        members[forest.find(index)].append(index)
    # Indexes are ascending, so each member list is already in canonical order.
    ordered = sorted(
        (tuple(items[i][0] for i in group), group) for group in members.values()
    )
    seen: dict[str, int] = {}
    clusters: list[IdentityCluster] = []
    for _, group in ordered:
        # The id member is the earliest-fetched one (canonical JSON breaks ties): a
        # later run only adds later fetches, so it cannot displace it.
        anchor = min(group, key=lambda i: (_earliest_fetch(items[i][1]), items[i][0]))
        base = sha256(items[anchor][0].encode("utf-8")).hexdigest()
        seen[base] = seen.get(base, 0) + 1
        cluster_id = base if seen[base] == 1 else f"{base}-{seen[base]}"
        clusters.append(
            IdentityCluster(
                cluster_id,
                tuple(items[i][1] for i in group),
                tuple(sorted(forest.kinds(group[0]))),
                tuple(sorted(forest.keys(group[0]))),
                frozenset(
                    address
                    for i in group
                    if (address := stated_email(items[i][1].values)) in shared.addresses
                ),
            )
        )
    return tuple(sorted(clusters, key=lambda c: c.cluster_id))


def _earliest_fetch(contribution: LeadContribution) -> datetime:
    return min(
        (p.fetched_at for p in contribution.provenance),
        default=datetime.max.replace(tzinfo=UTC),
    )


def _values(keys: list[MatchKeys], kind: MatchKeyKind) -> list[tuple[int, str]]:
    return [
        (i, k.value) for i, mk in enumerate(keys) for k in mk.keys if k.kind is kind
    ]


def _has(keys: MatchKeys, kind: MatchKeyKind) -> bool:
    """Whether the contribution offers ``kind``, a barred key (8.13) included."""
    return kind in keys.barred_kinds or any(k.kind is kind for k in keys.keys)


def _link_all(
    forest: _UnionFind, group: list[int], kind: MatchKeyKind, value: str
) -> None:
    for other in group[1:]:
        forest.union(group[0], other, kind, value)


def _link_linkedin(keys: list[MatchKeys], forest: _UnionFind) -> None:
    by_value: dict[str, list[int]] = defaultdict(list)
    for index, value in _values(keys, MatchKeyKind.LINKEDIN_URL):
        by_value[value].append(index)
    for value, group in by_value.items():
        _link_all(forest, group, MatchKeyKind.LINKEDIN_URL, value)


def _link_email(keys: list[MatchKeys], forest: _UnionFind) -> None:
    by_value: dict[str, list[int]] = defaultdict(list)
    for index, value in _values(keys, MatchKeyKind.VERIFIED_EMAIL):
        by_value[value].append(index)
    for value, group in by_value.items():
        # 8.2: absent LinkedIn on either lead. With no LinkedIn-less holder, every pair
        # has two LinkedIn keys, so email links nothing.
        bare = [i for i in group if not _has(keys[i], MatchKeyKind.LINKEDIN_URL)]
        if bare:
            for index in group:
                forest.union(bare[0], index, MatchKeyKind.VERIFIED_EMAIL, value)


def _link_name_domain(
    keys: list[MatchKeys], emails: Sequence[PersonalEmail | None], forest: _UnionFind
) -> None:
    """8.3 with the one-sided follow-up: a stated email no longer excludes a record.

    Edges are collected first and applied per component of the name+domain graph: a
    component whose personal addresses conflict (``emails_conflict``) is ambiguous
    and links nobody, so a bare record never bridges two addresses, whatever the order.
    """
    by_value: dict[str, list[int]] = defaultdict(list)
    for index, value in _values(keys, MatchKeyKind.NAME_DOMAIN):
        if not _has(keys[index], MatchKeyKind.LINKEDIN_URL):
            by_value[value].append(index)
    edges: list[tuple[int, int, str]] = []
    for value, group in by_value.items():
        # Pairwise ``corroborates`` (shared title or employer) as buckets: one edge
        # per member per bucket instead of a loop over pairs.
        buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
        for index in group:
            for title in keys[index].titles:
                buckets["title", title].append(index)
            for employer in keys[index].employers:
                buckets["employer", employer].append(index)
        for bucket in buckets.values():
            edges.extend((bucket[0], other, value) for other in bucket[1:])
    components = _UnionFind(len(keys))
    for a, b, _ in edges:
        components.union(a, b)
    stated: dict[int, set[PersonalEmail]] = defaultdict(set)
    for index, email in enumerate(emails):
        if email is not None:
            stated[components.find(index)].add(email)
    for a, b, value in edges:
        if not emails_conflict(stated[components.find(a)]):
            forest.union(a, b, MatchKeyKind.NAME_DOMAIN, value)
