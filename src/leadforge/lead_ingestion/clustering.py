"""Order-independent identity clustering (task 16.2; Requirements 8.8, 8.9).

A pure function from contributions to ``IdentityCluster`` values: no I/O, no globals,
input not mutated. Keys come from ``match_keys`` (task 16.1); clustering is union-find
over them, so the partition is the connected components of the key graph and cannot
depend on arrival order. Provisional decisions (choices.md, 16.2):

* Strength rules. LinkedIn: equal key merges. Verified email: equal key merges unless
  every member offered both carries a LinkedIn key (8.2 applies "when ``linkedin_url``
  is absent on either lead", so two leads that each name a LinkedIn profile never merge
  on email; a LinkedIn-less holder of the address joins all of them, which is what
  transitive closure over the pairwise rule gives). Name+domain (8.3): only a
  contribution with NO LinkedIn key and NO verified-email key takes part, and two merge
  only when they share a name+domain key AND ``corroborates`` (a shared title or a
  shared employer). Rejected: letting a keyed contribution join through the weak key;
  one bare record could bridge two people a stronger key keeps apart, and the
  over-merge direction has no unmerge (ADR-0003). This under-merges the one-sided case
  (a keyed record and a bare record of the same person), the cheap direction.
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
* Identity Exclusions (16.6), role-address disqualification (16.7) and the over-merge
  detector (16.8) are not built and no parameter reserves a seam for them.
* Errors name types only; cluster and key data is personal data.
"""

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.match_keys import (
    MatchKeyKind,
    MatchKeys,
    extract_match_keys,
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


class _UnionFind:
    """Union by size with path halving; elements are ``0..n-1``."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))
        self._size = [1] * size

    def find(self, item: int) -> int:
        parent = self._parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        if self._size[root_a] < self._size[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        self._size[root_a] += self._size[root_b]


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
) -> tuple[IdentityCluster, ...]:
    """Cluster ``contributions`` by Match Key; the result ignores arrival order."""
    # Canonical order first, so nothing downstream depends on arrival. Byte-identical
    # contributions are interchangeable, so their relative order is immaterial.
    items = sorted(
        ((canonical_json(c), c) for c in contributions), key=lambda pair: pair[0]
    )
    keys = [extract_match_keys(c) for _, c in items]
    forest = _UnionFind(len(items))
    _link_linkedin(keys, forest)
    _link_email(keys, forest)
    _link_name_domain(keys, forest)

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
        clusters.append(IdentityCluster(cluster_id, tuple(items[i][1] for i in group)))
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
    return any(k.kind is kind for k in keys.keys)


def _link_all(forest: _UnionFind, group: list[int]) -> None:
    for other in group[1:]:
        forest.union(group[0], other)


def _link_linkedin(keys: list[MatchKeys], forest: _UnionFind) -> None:
    by_value: dict[str, list[int]] = defaultdict(list)
    for index, value in _values(keys, MatchKeyKind.LINKEDIN_URL):
        by_value[value].append(index)
    for group in by_value.values():
        _link_all(forest, group)


def _link_email(keys: list[MatchKeys], forest: _UnionFind) -> None:
    by_value: dict[str, list[int]] = defaultdict(list)
    for index, value in _values(keys, MatchKeyKind.VERIFIED_EMAIL):
        by_value[value].append(index)
    for group in by_value.values():
        # 8.2: absent LinkedIn on either lead. With no LinkedIn-less holder, every pair
        # has two LinkedIn keys, so email links nothing.
        bare = [i for i in group if not _has(keys[i], MatchKeyKind.LINKEDIN_URL)]
        if bare:
            for index in group:
                forest.union(bare[0], index)


def _link_name_domain(keys: list[MatchKeys], forest: _UnionFind) -> None:
    by_value: dict[str, list[int]] = defaultdict(list)
    for index, value in _values(keys, MatchKeyKind.NAME_DOMAIN):
        stronger = _has(keys[index], MatchKeyKind.LINKEDIN_URL) or _has(
            keys[index], MatchKeyKind.VERIFIED_EMAIL
        )
        if not stronger:
            by_value[value].append(index)
    for group in by_value.values():
        # Pairwise ``corroborates`` (shared title or employer) as buckets: one union
        # per member per bucket instead of a loop over pairs.
        buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
        for index in group:
            for title in keys[index].titles:
                buckets["title", title].append(index)
            for employer in keys[index].employers:
                buckets["employer", employer].append(index)
        for bucket in buckets.values():
            _link_all(forest, bucket)
