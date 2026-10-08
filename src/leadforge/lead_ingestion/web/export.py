"""A store's leads as an Excel workbook, for the dashboard's download button.

Rows are built from ``lead_json`` output, so masking is exactly what the UI shows:
contact identifiers stay masked unless the request asked for ``reveal``.
"""

import io
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font

__all__ = ["XLSX_TYPE", "leads_xlsx"]

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CELL_MAX = 32_767  # Excel's limit on one cell's text
_COLUMNS = (
    "Lead ID", "Name", "Email", "Email status", "Role address", "LinkedIn",
    "Company", "Company domains", "Title", "Other current jobs", "Sources",
    "Tech signals", "Intent signals", "Opted out", "Suppressed", "Primary domain",
    "Domain tie flagged", "Retired at", "Computed at",
)  # fmt: skip


def leads_xlsx(leads: Iterable[Mapping[str, Any]]) -> bytes:
    """One sheet, one row per lead, header bold, frozen and filterable."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None  # a new Workbook always has one sheet
    sheet.title = "Leads"
    rows: list[Sequence[object]] = [_COLUMNS, *(_row(s) for s in leads)]
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row, start=1):
            cell = sheet.cell(r, c)
            # Provider text: write it as text, so a leading "=" is never a formula.
            if isinstance(value, str):
                cell.value = ILLEGAL_CHARACTERS_RE.sub("", value)[:_CELL_MAX]
                cell.data_type = "s"
            else:
                cell.value = value  # type: ignore[assignment]
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _row(stored: Mapping[str, Any]) -> Sequence[object]:
    lead = stored["lead"]
    jobs = lead["employments"]
    current = [j for j in jobs if j["is_current"] is True]
    # The UI's "current job": the first current one, else the first listed.
    job = current[0] if current else (jobs[0] if jobs else None)
    company = job["company"] if job else None
    return (
        stored["lead_id"],
        lead["full_name"],
        lead["email"],
        lead["email_status"],
        lead["email_is_role_address"],
        lead["linkedin_url"],
        company and company["name"],
        company and ", ".join(company["domains"]),
        job and job["title"],
        "; ".join(_job(j) for j in current if j is not job),
        ", ".join(stored["contributing_sources"]),
        ", ".join(s["label"] for s in lead["tech_signals"]),
        ", ".join(s["label"] for s in lead["intent_signals"]),
        lead["opt_out"],
        lead["suppressed"],
        stored["primary_domain"],
        stored["primary_domain_flagged"],
        stored["retired_at"],
        stored["computed_at"],
    )


def _job(job: Mapping[str, Any]) -> str:
    company = job["company"]
    name = company["name"] or ", ".join(company["domains"]) or "?"
    return f"{job['title']} at {name}" if job["title"] else name
