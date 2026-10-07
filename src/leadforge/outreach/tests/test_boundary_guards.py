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
        # The shared declarative Base the four outreach tables are mapped on, and the
        # error its append-only guard raises (tables.py).
        (f"{INGESTION}.store.models", "Base"),
        (f"{INGESTION}.store.models", "AppendOnlyViolationError"),
        # The report says which sources ran synthetic, read from the run's own rows.
        (f"{INGESTION}.store.models", "SourceRun"),
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


def _transports(root: Path) -> list[str]:
    bad: list[str] = []
    for path in _modules(root):
        for line, module, name in _imports(path):
            reached = [module, f"{module}.{name}" if name else module]
            if any(
                r == f or r.startswith(f"{f}.")
                for r in reached
                for f in FORBIDDEN_TRANSPORTS
            ):
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
