"""The leads workbook: provider text is written as text, never as a formula."""

import io

from openpyxl import load_workbook

from leadforge.lead_ingestion.web.export import leads_xlsx


def _lead(name: str, title: str) -> dict[str, object]:
    company = {"name": "Acme", "domains": ["acme.example"]}
    return {
        "lead_id": "1",
        "lead": {
            "full_name": name,
            "email": None,
            "email_status": "unknown",
            "email_is_role_address": False,
            "linkedin_url": None,
            "employments": [
                {"company": company, "title": title, "is_current": True},
                {"company": company, "title": "CTO", "is_current": True},
            ],
            "tech_signals": [{"label": "Tech X"}],
            "intent_signals": [],
            "opt_out": False,
            "suppressed": False,
        },
        "contributing_sources": ["source_a", "source_b"],
        "primary_domain": "acme.example",
        "primary_domain_flagged": False,
        "retired_at": None,
        "computed_at": "2026-10-08T00:00:00+00:00",
    }


def test_text_that_looks_like_a_formula_stays_text() -> None:
    book = load_workbook(io.BytesIO(leads_xlsx([_lead("=1+1", "Eng\x07ineer")])))
    sheet = book["Leads"]

    assert sheet["B2"].value == "=1+1"
    assert sheet["B2"].data_type == "s"
    assert sheet["I2"].value == "Engineer"
    assert sheet["J2"].value == "CTO at Acme"
    assert sheet["K2"].value == "source_a, source_b"
    assert sheet["L2"].value == "Tech X"
