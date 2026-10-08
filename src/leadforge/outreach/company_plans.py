"""Search Plans for the two company modes (requirements 3.1, 3.3, 4.1, 4.2).

Both are lookups in the Product Catalog, so their plans carry compiler ``offline``: no
model is asked.

* workers: the people at a company, searched by the vendor's domains. A domain given
  in the request wins over the catalog; with neither, ``MissingDomainError`` stops the
  search before ingestion.
* users: the people and companies that use a vendor's products, searched by catalog
  product keys. There is no default vendor: a request naming no product is
  ``NoProductSelectedError``, and an unknown vendor or product is
  ``UnknownCatalogKeyError``, both before any provider call.
"""

import re

from leadforge.lead_ingestion.catalog import (
    Catalog,
    CatalogVendor,
    UnknownCatalogKeyError,
)
from leadforge.outreach.errors import MissingDomainError, NoProductSelectedError
from leadforge.outreach.search_plan import SearchPlan, SearchRequest

__all__ = ["users_plan", "workers_plan"]


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def _vendor_named(catalog: Catalog, name: str) -> CatalogVendor | None:
    wanted = _normalize(name)
    for vendor in catalog.vendors():
        if wanted in (_normalize(vendor.key), _normalize(vendor.name)):
            return vendor
    return None


def workers_plan(request: SearchRequest, catalog: Catalog) -> SearchPlan:
    vendor = _vendor_named(catalog, request.query)
    domains = request.domains or (vendor.domains if vendor is not None else ())
    if not domains:
        raise MissingDomainError(request.query)
    return SearchPlan(
        mode="workers",
        query=request.query,
        company=request.query,
        domains=tuple(dict.fromkeys(domains)),
        compiler="offline",
    )


def users_plan(request: SearchRequest, catalog: Catalog) -> SearchPlan:
    if not request.products:
        raise NoProductSelectedError
    vendor = (
        catalog.vendor(request.vendor)
        if request.vendor
        else _owner(catalog, request.products[0])
    )
    for key in request.products:
        catalog.product(vendor.key, key)
    return SearchPlan(
        mode="users",
        query=request.query,
        company=vendor.name,
        terms=request.products,
        compiler="offline",
    )


def _owner(catalog: Catalog, product_key: str) -> CatalogVendor:
    for vendor in catalog.vendors():
        if any(p.key == product_key for p in vendor.products):
            return vendor
    raise UnknownCatalogKeyError(f"unknown product: {product_key}")
