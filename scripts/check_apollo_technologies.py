"""Check the Product Catalog's Apollo technology UIDs against Apollo's live list.

Dev tool, not part of a run. Apollo publishes its supported technologies as a CSV
(``Category,Technology``) at ``LIST_URL``, linked from the people search docs. The
list is Apollo's data and is not vendored (user decision 2026-10-06): the repo keeps
only the excerpt for configured terms, ``fixtures/apollo/`` ``TECHNOLOGY_EXCERPT``,
which a test holds equal to the catalog.

Usage: ``APOLLO_API_KEY=... uv run python scripts/check_apollo_technologies.py
[--rows] [--catalog DIR]``. Exit 0 when every configured UID is listed, 1 when one
is not (named on stderr), 2 without a key, on a download failure or when the
download is not the list. ``--rows`` prints the excerpt for the configured UIDs to
stdout, to paste over the fixture. The list is held in memory only; nothing is
written to disk, and the key is never printed and is sent to Apollo's host only.
"""

import argparse
import csv
import io
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import httpx

from leadforge.lead_ingestion.adapters.apollo import technology_rows
from leadforge.lead_ingestion.catalog import load_catalog, unknown_technology_uids
from leadforge.lead_ingestion.errors import NormalizationError

API_HOST = "api.apollo.io"
LIST_URL = f"https://{API_HOST}/v1/auth/supported_technologies_csv"
KEY_ENV = "APOLLO_API_KEY"
KEY_HEADER = "x-api-key"
TIMEOUT_SECONDS = 30
HEADER = ("Category", "Technology")

Rows = Mapping[str, tuple[str, str]]


def configured_uids(catalog_dir: Path | None = None) -> frozenset[str]:
    """The UIDs Apollo is asked with: every technology UID of the catalog."""
    return frozenset(load_catalog(catalog_dir).technology_uids())


def missing(listed: Rows, configured: frozenset[str]) -> list[str]:
    """Configured UIDs absent from Apollo's list, sorted."""
    return unknown_technology_uids(listed, configured)


def excerpt(listed: Rows, configured: frozenset[str]) -> str:
    """The fixture excerpt: the header and the listed rows for configured UIDs."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(listed[uid] for uid in sorted(configured & listed.keys()))
    return out.getvalue()


def fetch(key: str, *, transport: httpx.BaseTransport | None = None) -> str:
    """Download the list into memory; raises ``httpx.HTTPError``.

    The key rides only on requests to Apollo's own host: a redirect elsewhere (a
    storage link, say) is followed without it.
    """

    def key_for_apollo_only(request: httpx.Request) -> None:
        if request.url.host == API_HOST:
            request.headers[KEY_HEADER] = key
        else:
            request.headers.pop(KEY_HEADER, None)

    with httpx.Client(
        transport=transport,
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        event_hooks={"request": [key_for_apollo_only]},
    ) as client:
        response = client.get(LIST_URL)
        response.raise_for_status()
        return response.text


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: Callable[[str], str] = fetch,
    environ: Mapping[str, str] = os.environ,
) -> int:
    parser = argparse.ArgumentParser(description="Check Apollo technology UIDs.")
    parser.add_argument("--rows", action="store_true", help="print the excerpt")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="catalog directory (default: shipped)",
    )
    args = parser.parse_args(argv)
    key = environ.get(KEY_ENV, "").strip()
    if not key:
        print(f"{KEY_ENV} is not set", file=sys.stderr)
        return 2
    try:
        text = fetch(key)
        listed = technology_rows(text)
    except httpx.HTTPError as exc:
        # Status or type only: a message or body could quote the request or a person.
        reason = (
            f"HTTP {exc.response.status_code}"
            if isinstance(exc, httpx.HTTPStatusError)
            else type(exc).__name__
        )
        print(f"download failed: {reason}", file=sys.stderr)
        return 2
    except NormalizationError:
        print("download is not a Category,Technology list", file=sys.stderr)
        return 2
    configured = configured_uids(args.catalog)
    if args.rows:
        print(excerpt(listed, configured), end="")
    absent = missing(listed, configured)
    for uid in absent:
        print(f"not in Apollo's list: {uid}", file=sys.stderr)
    if not absent:
        print(f"all {len(configured)} configured UIDs are listed", file=sys.stderr)
    return 1 if absent else 0


if __name__ == "__main__":
    sys.exit(main())
