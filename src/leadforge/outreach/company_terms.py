"""Company terms read from ``config/company_terms.yaml`` (design D2, requirement 4.1).

What a company means as search input lives in config, not code: the Target Profile term
keys searched in users mode, and the domains searched in workers mode.
"""

import re
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from leadforge.outreach.config import read_yaml_mapping
from leadforge.outreach.errors import OutreachConfigError

__all__ = [
    "DEFAULT_COMPANY_TERMS_PATH",
    "CompanyEntry",
    "CompanyTerms",
    "load_company_terms",
    "normalize_company",
]

DEFAULT_COMPANY_TERMS_PATH = Path("config/company_terms.yaml")

Key = Annotated[str, Field(min_length=1)]


class CompanyEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    terms: tuple[Key, ...] = ()
    domains: tuple[Key, ...] = ()


class CompanyTerms(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    companies: dict[Key, CompanyEntry]

    def entry(self, company: str) -> CompanyEntry | None:
        """The entry for ``company`` (case and spacing ignored), or ``None``."""
        wanted = normalize_company(company)
        for name, entry in self.companies.items():
            if normalize_company(name) == wanted:
                return entry
        return None


def normalize_company(name: str) -> str:
    """A company name as a lookup key: casefolded, with one space between words."""
    return re.sub(r"\s+", " ", name).strip().casefold()


def load_company_terms(path: str | Path | None = None) -> CompanyTerms:
    """The company terms, from ``path`` or ``DEFAULT_COMPANY_TERMS_PATH``."""
    file = Path(path) if path is not None else DEFAULT_COMPANY_TERMS_PATH
    document = read_yaml_mapping(file)
    try:
        terms = CompanyTerms.model_validate(document)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False)[0]
        raise OutreachConfigError(
            str(file),
            key_path=".".join(str(part) for part in first["loc"]),
            detail=str(first["type"]),
        ) from None
    seen: dict[str, str] = {}
    for name in terms.companies:
        key = normalize_company(name)
        if key in seen:
            raise OutreachConfigError(
                str(file),
                key_path=f"companies.{name}",
                detail=f"duplicates {seen[key]!r} once case and spacing are ignored",
            )
        seen[key] = name
    return terms
