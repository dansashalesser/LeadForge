"""The outreach slice keeps to its two boundaries (outreach requirements 9.2, 9.3).

* It imports only the ingestion slice's public API, listed below by module and name.
* It imports no network transport and no mail library, so nothing can be sent.

Both are AST scans of every non-test module in the slice. Adding an ingestion import
means adding one line to ``ALLOWED_INGESTION_IMPORTS`` with its reason.
"""

import ast
from pathlib import Path

import pytest

import leadforge.outreach as slice_pkg

SLICE_ROOT = Path(slice_pkg.__file__).resolve().parent
INGESTION = "leadforge.lead_ingestion"

# (module, name) pairs; name "*" allows every name of that module.
ALLOWED_INGESTION_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {
        (f"{INGESTION}.ingest_runner", "run_ingestion"),
        (f"{INGESTION}.ingest_runner", "IngestionOutcome"),
        (f"{INGESTION}.target_profile", "TargetProfile"),
        (f"{INGESTION}.store.lead_reader", "StoredLead"),
        (f"{INGESTION}.store.lead_reader", "CrmState"),
        (f"{INGESTION}.store.lead_reader", "list_leads"),
        (f"{INGESTION}.store.lead_reader", "load_lead"),
        (f"{INGESTION}.store.lead_reader", "crm_state"),
        # CanonicalLead and its value types.
        (f"{INGESTION}.models", "*"),
        # The one store the run uses: engine from the environment, migrated to head
        # before the search row is written (the run itself migrates again, idempotent).
        (f"{INGESTION}.database", "create_store_engine"),
        (f"{INGESTION}.store.migrate", "upgrade_to_head"),
        # Command-line setup shared with the ``ingest`` command: env file, logging,
        # configuration errors, the lock error, and the read-only schema check.
        (f"{INGESTION}.database", "DatabaseConfigError"),
        (f"{INGESTION}.env_file", "EnvFileError"),
        (f"{INGESTION}.env_file", "load_env_file_into_process"),
        (f"{INGESTION}.errors", "ConfigurationError"),
        (f"{INGESTION}.ingest_runner", "RunInProgressError"),
        (f"{INGESTION}.log_redaction", "configure_logging"),
        (f"{INGESTION}.store.migrate", "StoreNotMigratedError"),
        (f"{INGESTION}.store.migrate", "require_head"),
        (f"{INGESTION}.target_profile", "load_target_profile"),
        # The shared declarative Base the four outreach tables are mapped on, and the
        # error its append-only guard raises (tables.py).
        (f"{INGESTION}.store.models", "Base"),
        (f"{INGESTION}.store.models", "AppendOnlyViolationError"),
        # The report says which sources ran synthetic, read from the run's own rows.
        (f"{INGESTION}.store.models", "SourceRun"),
        # The usage slice (user-recognition): the catalog (the only vocabulary
        # source, Req 2.7) with its loader and unknown-key error, and its types for
        # product/vendor lookup, the SERP backend contract, the shared throttle and the
        # injected transport.
        (f"{INGESTION}.catalog", "Catalog"),
        (f"{INGESTION}.catalog", "UnknownCatalogKeyError"),
        (f"{INGESTION}.catalog", "load_catalog"),
        (f"{INGESTION}.catalog", "CatalogProduct"),
        # usage/eval.py: eval cases declare their own aliases in the catalog shape.
        (f"{INGESTION}.catalog", "Alias"),
        (f"{INGESTION}.catalog", "CatalogVendor"),
        # usage/drafts.py: drafts are validated by the catalog schema, never trusted.
        (f"{INGESTION}.catalog", "CatalogError"),
        (f"{INGESTION}.catalog", "DRAFTS_DIR"),
        (f"{INGESTION}.catalog", "UnknownTechnologyUidError"),
        (f"{INGESTION}.catalog", "VendorDocument"),
        (f"{INGESTION}.catalog", "default_catalog_dir"),
        (f"{INGESTION}.catalog", "parse_vendor_file"),
        (f"{INGESTION}.catalog", "unknown_technology_uids"),
        (f"{INGESTION}.adapters.search_backends", "SearchBackend"),
        (f"{INGESTION}.throttle", "SourceThrottle"),
        (f"{INGESTION}.transport", "Transport"),
        # Person Fit's role families (config/catalog/roles.yaml), read by the service.
        (f"{INGESTION}.catalog", "load_roles"),
        # usage/demo_wiring.py: the synthetic flow's SERP and pages come from the demo
        # dataset's own transport and page table (no socket); _Tables is the demo's
        # table index that both need, with no public constructor yet.
        (f"{INGESTION}.adapters.search_backends", "select_backend"),
        (f"{INGESTION}.demo.generator", "DATA_DIR"),
        (f"{INGESTION}.demo.transport", "DemoLog"),
        (f"{INGESTION}.demo.transport", "DemoPages"),
        (f"{INGESTION}.demo.transport", "DemoTransport"),
        (f"{INGESTION}.demo.transport", "_Tables"),
    }
)

# Top-level packages and modules that open a connection or send mail.
FORBIDDEN_TRANSPORTS = frozenset(
    {
        "httpx",
        "requests",
        "aiohttp",
        "urllib3",
        "websockets",
        "socket",
        "ssl",
        "smtplib",
        "imaplib",
        "poplib",
        "ftplib",
        "telnetlib",
        "http.client",
        "urllib.request",
        "asyncio.streams",
        "email.mime",
        f"{INGESTION}.transport",
        f"{INGESTION}.mcp_transport",
        f"{INGESTION}.base_source",
        f"{INGESTION}.adapters",
    }
)

# Narrow per-file exceptions to the ban above, keyed by path relative to the slice.
# usage/fetch.py reads public web pages (GET) with httpx; usage/serp.py sends search
# queries through the injected ingestion Transport (read-only SerpApi lookups).
NETWORK_ALLOWED: dict[str, frozenset[str]] = {
    "usage/fetch.py": frozenset({"httpx"}),
    "usage/serp.py": frozenset({f"{INGESTION}.transport", f"{INGESTION}.adapters"}),
    # The demo flow builds httpx.Response objects from the demo page table (no client,
    # no socket) and picks the
    # SERP backend class from the adapters package.
    "usage/demo_wiring.py": frozenset({"httpx", f"{INGESTION}.adapters"}),
}


def _modules(root: Path) -> list[Path]:
    found = [
        p
        for p in sorted(root.rglob("*.py"))
        if "tests" not in p.relative_to(root).parts
    ]
    if not found:
        raise AssertionError(f"no modules to scan under {root}")
    return found


def _imports(path: Path) -> list[tuple[int, str, str]]:
    """(line, module, name) per imported name; name is "" for ``import module``."""
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, a.name, "") for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError(f"{path}:{node.lineno} relative import")
            out.extend((node.lineno, node.module or "", a.name) for a in node.names)
        elif isinstance(node, ast.Call) and _dynamic(node):
            out.append((node.lineno, _dynamic(node), ""))
    return out


def _dynamic(call: ast.Call) -> str:
    """The module named by ``__import__("x")`` or ``import_module("x")``, else ""."""
    func = call.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if name in {"__import__", "import_module"} and call.args:
        arg = call.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
    return ""


def _outside_the_allowlist(root: Path) -> list[str]:
    bad: list[str] = []
    for path in _modules(root):
        for line, module, name in _imports(path):
            if module != INGESTION and not module.startswith(f"{INGESTION}."):
                continue
            allowed = (module, name) in ALLOWED_INGESTION_IMPORTS or (
                (module, "*") in ALLOWED_INGESTION_IMPORTS and name != ""
            )
            if not allowed:
                bad.append(f"{path.name}:{line} {module} {name}".rstrip())
    return bad


def _forbidden_hits(path: Path) -> list[tuple[int, str, str, str]]:
    """(line, module, name, forbidden entry) for each banned import in ``path``."""
    hits: list[tuple[int, str, str, str]] = []
    for line, module, name in _imports(path):
        reached = [module, f"{module}.{name}" if name else module]
        for f in sorted(FORBIDDEN_TRANSPORTS):
            if any(r == f or r.startswith(f"{f}.") for r in reached):
                hits.append((line, module, name, f))
                break
    return hits


def _transports(
    root: Path, allowed: dict[str, frozenset[str]] | None = None
) -> list[str]:
    allowed = NETWORK_ALLOWED if allowed is None else allowed
    bad: list[str] = []
    for path in _modules(root):
        rel = path.relative_to(root).as_posix()
        for line, module, name, forbidden in _forbidden_hits(path):
            if forbidden in allowed.get(rel, frozenset()):
                continue
            bad.append(f"{path.name}:{line} {module} {name}".rstrip())
    return bad


# Verifies: outreach requirements 9.2
def test_the_slice_imports_only_the_allowed_ingestion_public_api() -> None:
    assert _outside_the_allowlist(SLICE_ROOT) == []


# Verifies: outreach requirements 9.3
def test_the_slice_imports_no_network_transport_or_mail_library() -> None:
    assert _transports(SLICE_ROOT) == []


def _planted(tmp_path: Path, source: str) -> Path:
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "source",
    [
        f"from {INGESTION}.adapters import provider_one\n",
        f"from {INGESTION}.store.models import IngestionRun\n",
        f"import {INGESTION}.orchestrator\n",
        f"from {INGESTION} import adapters\n",
        f"from {INGESTION}.store.lead_reader import _load\n",
        f"x = __import__('{INGESTION}.registry')\n",
    ],
)
def test_the_ingestion_scan_flags_an_import_outside_the_allowlist(
    tmp_path: Path, source: str
) -> None:
    assert _outside_the_allowlist(_planted(tmp_path, source))


@pytest.mark.parametrize(
    "source",
    [
        f"from {INGESTION}.ingest_runner import run_ingestion\n",
        f"from {INGESTION}.models import CanonicalLead, Employment\n",
        f"from {INGESTION}.store.lead_reader import crm_state, list_leads\n",
    ],
)
def test_the_ingestion_scan_accepts_the_public_api(tmp_path: Path, source: str) -> None:
    assert _outside_the_allowlist(_planted(tmp_path, source)) == []


@pytest.mark.parametrize(
    "source",
    [
        "import httpx\n",
        "from requests import get\n",
        "import smtplib\n",
        "import socket\n",
        "from http import client\n",
        "from urllib.request import urlopen\n",
        "from email.mime.text import MIMEText\n",
        f"from {INGESTION}.transport import RestTransport\n",
        "import importlib\nimportlib.import_module('httpx')\n",
    ],
)
def test_the_transport_scan_flags_a_network_or_mail_import(
    tmp_path: Path, source: str
) -> None:
    assert _transports(_planted(tmp_path, source))


def test_the_transport_scan_accepts_plain_imports(tmp_path: Path) -> None:
    source = (
        "import json\nfrom email.utils import parseaddr\nfrom decimal import Decimal\n"
    )
    assert _transports(_planted(tmp_path, source)) == []


def test_a_scan_over_no_modules_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="no modules"):
        _modules(tmp_path)


# Verifies: outreach requirements 9.3 (the exception table cannot go stale or widen)
def test_the_network_allowance_names_only_existing_files_and_imports_they_use() -> None:
    for rel, forbidden in NETWORK_ALLOWED.items():
        path = SLICE_ROOT / rel
        assert path.is_file(), f"{rel} no longer exists: drop its allowance"
        used = {hit[3] for hit in _forbidden_hits(path)}
        assert used == set(forbidden), f"{rel} allowance differs from imports: {used}"


def test_a_file_outside_the_allowance_is_still_flagged(tmp_path: Path) -> None:
    (tmp_path / "usage").mkdir()
    (tmp_path / "usage" / "other.py").write_text("import httpx\n", encoding="utf-8")
    assert _transports(tmp_path)
