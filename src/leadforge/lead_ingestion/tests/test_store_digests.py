"""No plain sha256 over contribution content reaches the store (follow-up 2026-10-06).

Requirements 8.12 and 10.5. ``clustering.cluster_id`` is a plain sha256 of a whole
contribution, and a Lead whose company had no domain used to persist a ``company_id``
derived from it; ``content_sha`` and the raw payload fingerprint were plain sha256s of
contribution and payload content. A plain hash of personal data is reversible by a
dictionary attack, so now:

* a Lead's domainless company is ``co-<lead identity uuid hex>``, its persisted id;
* ``content_sha`` and ``request_fingerprint`` are HMAC-SHA256 under the store's own
  random key (``store_secret``, written once by migration 0007): useless without the
  store, and with the store an attacker already has the contribution rows;
* migration 0007 rewrites every existing id consistently, so a store written before
  it re-ingests the same record as the same contribution and the same Lead.

Proved by a dump of every stored value (no plain digest anywhere) and an AST check
of the store code (every persisted digest goes through the keyed function).
"""

# ruff: noqa: F811 - fixtures imported from test_persistence_both_engines

import ast
import hashlib
import hmac
import json
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.clustering import (
    canonical_value_json,
    cluster_contributions,
)
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.contributions import (
    contribution_sha,
    load_lead_contributions,
)
from leadforge.lead_ingestion.store.store_key import KEY_NAME
from leadforge.lead_ingestion.tests.store_run_support import (
    Script,
    active_leads,
    count,
    person,
    registry_of,
    scripted_source,
)
from leadforge.lead_ingestion.tests.test_lead_remerge import contribution, remerge
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    _alembic,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)

STORE_DIR = Path(__file__).resolve().parents[1] / "store"
DOMAINLESS = person(
    "ada@example.com", "Ada Lovelace", company__name="Analytical Engines"
)
KEYLESS = {"person.full_name": "Nameless Person", "company.name": "Acme Ltd"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _dump(engine: sa.Engine) -> str:
    """Every value of every row of every table, as one text."""
    out: list[str] = []
    with engine.connect() as conn:
        for table in m.Base.metadata.sorted_tables:
            for row in conn.execute(sa.select(table)):
                out.append(json.dumps([str(v) for v in row], default=str))
    return "\n".join(out)


# Verifies: specs/lead-source-adapters/requirements.md#10.5
async def test_no_plain_sha256_of_contribution_or_payload_content_is_stored(
    composed: Backend,
) -> None:
    await run_ingestion(
        registry=registry_of(scripted_source("alpha", Script([DOMAINLESS, KEYLESS])))
    )

    with Session(composed.engine) as s:
        log = list(load_lead_contributions(s).values())
        payloads = [
            p for p in s.scalars(sa.select(m.RawResponse.payload)) if p is not None
        ]
    forbidden: set[str] = set()
    for c in log:
        forbidden |= {contribution_sha(c), contribution_sha(c)[:16]}
    for cluster in cluster_contributions(log):
        lead_company = _sha("no-domain\x1f" + cluster.cluster_id)[:16]
        forbidden |= {cluster.cluster_id, cluster.cluster_id[:16], lead_company}
    for payload in payloads:
        forbidden.add(_sha(canonical_value_json(payload)))
    assert len(forbidden) > 4
    dump = _dump(composed.engine)
    assert [f for f in forbidden if f in dump] == []

    # The domainless companies are the leads' own persisted ids.
    for lead in active_leads(composed.engine):
        [employment] = lead.employments or []
        assert employment["company"]["domains"] == []
        assert employment["company"]["company_id"] == f"co-{lead.lead_identity_id.hex}"


def _calls_keyed(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "id", "") == "store_digest"


def _random_hex(node: ast.AST) -> bool:
    """``uuid.uuid4().hex``: an opaque value, no digest of any content."""
    return ast.unparse(node) == "uuid.uuid4().hex"


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_store_code_persists_digests_only_through_the_keyed_function() -> None:
    persisted: list[tuple[str, str]] = []
    plain: list[str] = []
    for path in sorted(STORE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.keyword)
                and node.arg in {"content_sha", "request_fingerprint"}
                # A bare name is a parameter passed through (``RawResponseRepository.
                # add``); its callers are checked here.
                and not isinstance(node.value, ast.Name)
            ):
                persisted.append((path.name, node.arg))
                opaque = node.arg == "request_fingerprint"
                assert (_random_hex if opaque else _calls_keyed)(node.value), (
                    path.name,
                    node.arg,
                )
            if isinstance(node, ast.FunctionDef):
                for call in ast.walk(node):
                    name = getattr(getattr(call, "func", None), "id", None) or getattr(
                        getattr(call, "func", None), "attr", None
                    )
                    if isinstance(call, ast.Call) and name == "sha256":
                        plain.append(f"{path.name}:{node.name}")
    assert ("contributions.py", "content_sha") in persisted
    assert ("merged_leads.py", "request_fingerprint") in persisted
    # The in-memory identity is the only plain digest.
    assert sorted(set(plain)) == ["contributions.py:contribution_sha"]


# Verifies: specs/lead-source-adapters/requirements.md#10.5
async def test_the_raw_payload_fingerprint_is_random_never_a_digest_of_it(
    composed: Backend,
) -> None:
    """Nothing looks a payload up by its fingerprint, so it needs no determinism: an
    opaque random value leaks nothing once the payload is purged, even to whoever
    holds the store and its key (a keyed digest would let them confirm a guess)."""
    for _ in range(2):
        await run_ingestion(
            registry=registry_of(scripted_source("alpha", Script([DOMAINLESS])))
        )
    key = _store_key(composed.engine)
    with Session(composed.engine) as s:
        rows = s.execute(
            sa.select(m.RawResponse.request_fingerprint, m.RawResponse.payload)
        ).all()
    assert len(rows) == 2
    assert len({fingerprint for fingerprint, _ in rows}) == 2  # same payload
    for fingerprint, payload in rows:
        plain = _sha(canonical_value_json(payload))
        assert fingerprint not in {plain, _keyed(key, plain)}
        assert len(fingerprint) == 32
        int(fingerprint, 16)


def _keyed(key: bytes, text: str) -> str:
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()


def _store_key(engine: sa.Engine) -> bytes:
    with engine.connect() as conn:
        value = conn.execute(
            sa.select(m.StoreSecret.value).where(m.StoreSecret.name == KEY_NAME)
        ).scalar_one()
    return bytes.fromhex(value)


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_0007_rewrites_existing_ids_consistently_and_walks_down(blank: Backend) -> None:
    _alembic(blank, "upgrade", "head")
    record = contribution(
        "alpha", person__full_name="Nameless Person", company__name="Acme Ltd"
    )
    remerge(blank.engine, [record])
    [lead] = active_leads(blank.engine)
    plain = contribution_sha(record)
    old_company = "co-" + "0123456789abcdef"

    # Walk down to 0006 and make the rows look as 0006 wrote them.
    _alembic(blank, "downgrade", "0006")
    contribution_table = sa.table(
        "source_contribution", sa.column("id"), sa.column("content_sha")
    )
    canonical = sa.table(
        "canonical_lead", sa.column("id"), sa.column("employments", sa.JSON())
    )
    raw = sa.table("raw_response", sa.column("id"), sa.column("request_fingerprint"))
    with blank.engine.begin() as conn:
        assert (
            conn.execute(sa.select(contribution_table.c.content_sha)).scalar_one()
            is None
        )
        conn.execute(sa.update(contribution_table).values(content_sha=plain))
        employments = conn.execute(sa.select(canonical.c.employments)).scalar_one()
        employments[0]["company"]["company_id"] = old_company
        conn.execute(sa.update(canonical).values(employments=employments))
        conn.execute(sa.update(raw).values(request_fingerprint="ab" * 32))
    assert not {"store_secret", "run_lock"} & set(
        sa.inspect(blank.engine).get_table_names()
    )

    _alembic(blank, "upgrade", "head")
    key = _store_key(blank.engine)
    with Session(blank.engine) as s:
        assert s.scalars(sa.select(m.SourceContribution.content_sha)).one() == (
            hmac.new(key, plain.encode(), hashlib.sha256).hexdigest()
        )
        fingerprint = s.scalars(sa.select(m.RawResponse.request_fingerprint)).one()
    # The plain payload digest is replaced by an opaque random value (never keyed).
    assert fingerprint not in {"ab" * 32, _keyed(key, "ab" * 32)}
    assert len(fingerprint) == 32
    # The first-seen lead column is gone: ``contribution_lead`` is the one mapping.
    assert "lead_identity_id" not in _columns(blank.engine, "source_contribution")
    [rewritten] = active_leads(blank.engine)
    company: dict[str, Any] = (rewritten.employments or [{}])[0]["company"]
    assert company["company_id"] == f"co-{lead.lead_identity_id.hex}"

    # The same record ingested again is the same contribution and the same lead.
    remerge(blank.engine, [record])
    assert count(blank.engine, m.SourceContribution) == 1
    assert [r.lead_identity_id for r in active_leads(blank.engine)] == [
        lead.lead_identity_id
    ]
    assert isinstance(lead.lead_identity_id, uuid.UUID)

    # Walking down restores the column, filled from the mapping, for 0006's code.
    _alembic(blank, "downgrade", "0006")
    assert "lead_identity_id" in _columns(blank.engine, "source_contribution")
    contribution_lead = sa.table(
        "source_contribution", sa.column("lead_identity_id", sa.Uuid())
    )
    with blank.engine.connect() as conn:
        assert conn.execute(
            sa.select(contribution_lead.c.lead_identity_id)
        ).scalar_one() == (lead.lead_identity_id)
    _alembic(blank, "upgrade", "head")


def _columns(engine: sa.Engine, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}
