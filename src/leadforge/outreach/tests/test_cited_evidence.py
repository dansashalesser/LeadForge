"""Rebuilding the evidence a Verdict cited from the records its run filed."""

import uuid
from datetime import date
from decimal import Decimal

from leadforge.lead_ingestion.catalog import Alias, CatalogProduct, CatalogVendor
from leadforge.lead_ingestion.models import CanonicalLead
from leadforge.outreach.usage.records import (
    ClassifierStamp,
    EvidenceClass,
    EvidenceRecord,
    Relationship,
)
from leadforge.outreach.usage.stage import StageConfig, cited_evidence, person_url_of

A = CatalogProduct(key="prod_a", name="Product A", aliases=(Alias(text="Product A"),))
B = CatalogProduct(key="prod_b", name="Product B", aliases=(Alias(text="Product B"),))
VENDOR = CatalogVendor(
    key="vend",
    name="Vend",
    domains=("vend.example",),
    partner_domains=(),
    products=(A, B),
    ecosystem=(),
)
TODAY = date(2026, 10, 8)
PAT = "https://www.linkedin.com/in/pat"


def _record(
    product: str,
    cls: EvidenceClass,
    rel: Relationship,
    on: date | None,
    url: str = "https://acme.example/blog",
) -> EvidenceRecord:
    return EvidenceRecord(
        company_key="acme.example",
        product_key=product,
        evidence_class=cls,
        source="s",
        url=url,
        observed_on=on,
        quote="We run Product A.",
        relationship=rel,
        confidence=Decimal("0.9"),
        snippet_only=False,
        classifier=ClassifierStamp(
            kind="offline", model=None, prompt_version="v", input_hash="h"
        ),
    )


def test_only_the_best_products_counted_records_and_this_persons_statement() -> None:
    old_past = _record(
        "prod_a", EvidenceClass.THIRD_PARTY_CONTENT, Relationship.USED_PAST,
        date(2025, 1, 1),
    )  # fmt: skip
    now = _record(
        "prod_a", EvidenceClass.OWN_DOMAIN_CONTENT, Relationship.USES_NOW,
        date(2026, 1, 1),
    )  # fmt: skip
    other_product = _record(
        "prod_b", EvidenceClass.THIRD_PARTY_CONTENT, Relationship.USED_PAST,
        date(2026, 2, 1),
    )  # fmt: skip
    pat = _record(
        "prod_a", EvidenceClass.PERSON_SELF_STATED, Relationship.USES_NOW, None, PAT
    )
    sam = _record(
        "prod_a", EvidenceClass.PERSON_SELF_STATED, Relationship.USES_NOW, None,
        "https://www.linkedin.com/in/sam",
    )  # fmt: skip

    cited = cited_evidence(
        [old_past, now, other_product, sam, pat],
        VENDOR,
        ["prod_a", "prod_b"],
        PAT,
        StageConfig(),
        TODAY,
    )

    assert cited == (now, pat)


def test_a_person_with_no_linkedin_url_is_filed_under_their_lead_id() -> None:
    lead_id = uuid.uuid4()
    lead = CanonicalLead.model_validate({"full_name": "Pat"})

    assert person_url_of(lead_id, lead) == f"person:{lead_id}"
