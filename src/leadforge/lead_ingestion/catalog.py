"""Product Catalog: the single vocabulary source, one YAML file per vendor.

Requirement 2 of user-recognition. Vendors, products, aliases (with co-terms),
technographic UIDs, package names and customer-page paths are data under
``config/catalog/<vendor>.yaml``; this module is the generic, typed loader. Unknown
keys are errors. ``roles.yaml`` and subdirectories (``drafts/``) are not vendor files.
"""

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.target_profile import TargetProfile

__all__ = [
    "Alias",
    "Catalog",
    "CatalogError",
    "CatalogProduct",
    "CatalogVendor",
    "RoleFamily",
    "Roles",
    "UnknownCatalogKeyError",
    "UnknownTechnologyUidError",
    "default_catalog_dir",
    "load_catalog",
    "load_roles",
    "unknown_technology_uids",
]

CATALOG_DIR_ENV = "LEADFORGE_CATALOG_DIR"
ROLES_FILE = "roles.yaml"


class CatalogError(Exception):
    """Root of catalog lookup and validation failures."""


class UnknownCatalogKeyError(CatalogError):
    """A vendor or product key is not in the catalog."""


class UnknownTechnologyUidError(CatalogError):
    """Catalog UIDs absent from the provider's supported-technologies list."""

    def __init__(self, uids: list[str]) -> None:
        self.uids = tuple(uids)
        super().__init__(f"not in the provider's list: {', '.join(uids)}")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Alias(_Model):
    text: str
    co_terms: tuple[str, ...] = ()


class CatalogProduct(_Model):
    key: str
    name: str = ""
    aliases: tuple[Alias, ...]
    technology_uids: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    customer_paths: tuple[str, ...] = ()


class VendorIdentity(_Model):
    key: str
    name: str
    domains: tuple[str, ...] = ()
    partner_domains: tuple[str, ...] = ()


class _VendorFile(_Model):
    vendor: VendorIdentity
    products: tuple[CatalogProduct, ...]
    ecosystem: tuple[CatalogProduct, ...] = ()


class CatalogVendor(_Model):
    key: str
    name: str
    domains: tuple[str, ...]
    partner_domains: tuple[str, ...]
    products: tuple[CatalogProduct, ...]
    ecosystem: tuple[CatalogProduct, ...]


class Catalog:
    """Loaded vendors, looked up by key."""

    def __init__(self, vendors: Mapping[str, CatalogVendor]) -> None:
        self._vendors = dict(sorted(vendors.items()))

    def vendor_keys(self) -> tuple[str, ...]:
        return tuple(self._vendors)

    def vendors(self) -> tuple[CatalogVendor, ...]:
        return tuple(self._vendors.values())

    def vendor(self, key: str) -> CatalogVendor:
        try:
            return self._vendors[key]
        except KeyError:
            raise UnknownCatalogKeyError(f"unknown vendor: {key}") from None

    def product(self, vendor_key: str, product_key: str) -> CatalogProduct:
        for product in self.vendor(vendor_key).products:
            if product.key == product_key:
                return product
        raise UnknownCatalogKeyError(f"unknown product: {vendor_key}/{product_key}")

    def technology_uids(self) -> tuple[str, ...]:
        """Every UID in the catalog, first appearance order, no repeats."""
        uids: dict[str, None] = {}
        for vendor in self._vendors.values():
            for entry in (*vendor.products, *vendor.ecosystem):
                uids.update(dict.fromkeys(entry.technology_uids))
        return tuple(uids)

    def to_profile(
        self,
        vendor_key: str,
        *,
        product_keys: Sequence[str] | None = None,
        competitors: Sequence[str] = (),
        uid_source: str,
        alias_source: str,
    ) -> TargetProfile:
        """The only constructor of a profile outside tests.

        ``vendor_key``'s selected products (all if ``product_keys`` is None) plus its
        ecosystem become technologies; every product of each vendor in ``competitors``
        becomes a competitor. Per term, ``uid_source`` gets the technology UIDs and
        ``alias_source`` the alias texts; an empty UID list yields no column.
        """
        vendor = self.vendor(vendor_key)
        if product_keys is None:
            chosen = vendor.products
        else:
            chosen = tuple(self.product(vendor_key, key) for key in product_keys)
        rivals = [
            p
            for key in competitors
            if key != vendor_key
            for p in self.vendor(key).products
        ]

        def column(entries: Sequence[CatalogProduct]) -> dict[str, dict[str, object]]:
            out: dict[str, dict[str, object]] = {}
            for entry in entries:
                cols: dict[str, object] = {}
                if entry.technology_uids:
                    cols[uid_source] = list(entry.technology_uids)
                cols[alias_source] = [a.text for a in entry.aliases]
                out[entry.key] = cols
            return out

        return TargetProfile(
            technologies=column((*chosen, *vendor.ecosystem)),
            competitors=column(rivals),
        )


def default_catalog_dir() -> Path:
    """``LEADFORGE_CATALOG_DIR`` or ``config/catalog`` resolved from this package."""
    override = os.environ.get(CATALOG_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "config" / "catalog"


def unknown_technology_uids(
    listed: Mapping[str, object], configured: frozenset[str]
) -> list[str]:
    """Configured UIDs absent from the list, sorted."""
    return sorted(configured - listed.keys())


def _parse(path: Path) -> CatalogVendor:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        parsed = _VendorFile.model_validate(data)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(
            str(path), key_path="", detail=type(exc).__name__
        ) from None
    except ValidationError as exc:
        # Location and error type only: the input may hold a mistyped secret.
        first = exc.errors()[0]
        key_path = ".".join(str(part) for part in first["loc"])
        raise ConfigurationError(
            str(path), key_path=key_path, detail=first["type"]
        ) from None
    return CatalogVendor(
        **parsed.vendor.model_dump(),
        products=parsed.products,
        ecosystem=parsed.ecosystem,
    )


def load_catalog(
    directory: Path | None = None,
    *,
    listed_technologies: Mapping[str, object] | None = None,
) -> Catalog:
    """Load every ``*.yaml`` vendor file in ``directory`` (default directory if None).

    With ``listed_technologies`` (UID -> row of the provider's list),
    every catalog UID must be in it or ``UnknownTechnologyUidError`` is raised.
    """
    root = default_catalog_dir() if directory is None else directory
    if not root.is_dir():
        raise ConfigurationError(str(root), key_path="", detail="not a directory")
    vendors: dict[str, CatalogVendor] = {}
    for path in sorted(root.glob("*.yaml")):
        if path.name == ROLES_FILE:
            continue
        vendor = _parse(path)
        if vendor.key in vendors:
            raise ConfigurationError(
                str(path), key_path="vendor.key", detail="duplicate vendor"
            )
        vendors[vendor.key] = vendor
    catalog = Catalog(vendors)
    if listed_technologies is not None:
        absent = unknown_technology_uids(
            listed_technologies, frozenset(catalog.technology_uids())
        )
        if absent:
            raise UnknownTechnologyUidError(absent)
    return catalog


class RoleFamily(_Model):
    key: str
    core: tuple[str, ...] = ()
    adjacent: tuple[str, ...] = ()
    irrelevant: tuple[str, ...] = ()


class Roles(_Model):
    """``roles.yaml``: role families plus seniority tokens (Req 7.1)."""

    families: tuple[RoleFamily, ...]
    seniority: tuple[str, ...] = ()

    @property
    def core(self) -> tuple[str, ...]:
        return tuple(t for f in self.families for t in f.core)

    @property
    def adjacent(self) -> tuple[str, ...]:
        return tuple(t for f in self.families for t in f.adjacent)

    @property
    def irrelevant(self) -> tuple[str, ...]:
        return tuple(t for f in self.families for t in f.irrelevant)


def load_roles(directory: Path | None = None) -> Roles:
    """Read and validate ``roles.yaml`` from the catalog directory."""
    root = default_catalog_dir() if directory is None else directory
    path = root / ROLES_FILE
    try:
        return Roles.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(
            str(path), key_path="", detail=type(exc).__name__
        ) from None
    except ValidationError as exc:
        first = exc.errors()[0]
        key_path = ".".join(str(part) for part in first["loc"])
        raise ConfigurationError(
            str(path), key_path=key_path, detail=first["type"]
        ) from None
