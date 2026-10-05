"""Static guard: provider raw schemas stay private to the ingestion slice (1.1).

Convention: every provider raw Pydantic schema lives under `RAW_SCHEMA_PACKAGE`.
Modules outside the slice may import the slice's public types (e.g. `CanonicalLead`)
but never anything under that package.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

from leadforge.lead_ingestion.send_prohibition import send_capable_reason

RAW_SCHEMA_PACKAGE = "leadforge.lead_ingestion.adapters"


@dataclass(frozen=True)
class RawSchemaImport:
    """One import statement outside the slice that reaches a provider raw schema."""

    path: Path
    """Absolute path of the offending `.py` file (outside the ingestion slice)."""

    lineno: int
    """1-based line number of the import statement within `path`."""

    module: str
    """Fully qualified module name that was imported, resolved to absolute form
    (relative imports included), e.g. `leadforge.lead_ingestion.adapters.provider_one`.
    When one statement imports several raw-schema names, this is the first."""


def find_raw_schema_imports_outside_slice(
    src_root: Path, slice_root: Path
) -> list[RawSchemaImport]:
    """Return every import of `RAW_SCHEMA_PACKAGE` from a module outside the slice."""
    if not src_root.is_dir():
        raise NotADirectoryError(f"src root does not exist: {src_root}")
    src_root = src_root.resolve()
    slice_root = slice_root.resolve()
    violations: list[RawSchemaImport] = []
    for path in sorted(src_root.rglob("*.py")):
        if path.is_relative_to(slice_root):
            continue
        package = _package_of(path, src_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            raw = [m for m in _imported_modules(node, package) if _is_raw_schema(m)]
            if raw:  # one violation per import statement, not per imported name
                violations.append(RawSchemaImport(path, node.lineno, raw[0]))
    return violations


def _package_of(path: Path, src_root: Path) -> list[str]:
    """Dotted package parts containing `path`, relative to the src root."""
    return list(path.relative_to(src_root).parent.parts)


def _imported_modules(
    node: ast.Import | ast.ImportFrom, package: list[str]
) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    base = _absolute_base(node, package)
    names = [f"{base}.{alias.name}" for alias in node.names]
    return [base, *names] if base else names


def _absolute_base(node: ast.ImportFrom, package: list[str]) -> str:
    if node.level == 0:
        return node.module or ""
    anchor = package[: len(package) - node.level + 1]
    return ".".join([*anchor, *([node.module] if node.module else [])])


def _is_raw_schema(module: str) -> bool:
    return module == RAW_SCHEMA_PACKAGE or module.startswith(f"{RAW_SCHEMA_PACKAGE}.")


# --------------------------------- orchestration depends on the contract only (2.4)

ORCHESTRATION_STEMS = ("orchestrator", "orchestration")
"""A module or package of one of these names, directly in the slice, is the layer."""

CONTRACT_BASE = "BaseLeadSource"


@dataclass(frozen=True)
class AdapterReference:
    """One place the orchestration layer names a concrete adapter."""

    path: Path
    """Absolute path of the offending orchestration module."""

    lineno: int
    """1-based line of the import or name that references the adapter."""

    name: str
    """The imported adapter module or the concrete adapter class name."""


def find_concrete_adapter_references(slice_root: Path) -> list[AdapterReference]:
    """Every import of the adapters package or use of a concrete adapter class name
    inside the orchestration layer. Absent orchestration modules yield no violations.
    """
    slice_root = slice_root.resolve()
    src_root = slice_root.parent.parent
    classes = _concrete_adapter_classes(slice_root / "adapters")
    violations: list[AdapterReference] = []
    for path in _orchestration_modules(slice_root):
        package = _package_of(path, src_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                hit = next(
                    (m for m in _imported_modules(node, package) if _is_raw_schema(m)),
                    None,
                )
                if hit is None:
                    hit = next(
                        (
                            a.name.rsplit(".", 1)[-1]
                            for a in node.names
                            if a.name.rsplit(".", 1)[-1] in classes
                        ),
                        None,
                    )
                if hit is not None:
                    violations.append(AdapterReference(path, node.lineno, hit))
            elif isinstance(node, ast.Call) and _dynamic_import_target(node):
                violations.append(
                    AdapterReference(path, node.lineno, _dynamic_import_target(node))
                )
            elif isinstance(node, ast.Name) and node.id in classes:
                violations.append(AdapterReference(path, node.lineno, node.id))
            elif isinstance(node, ast.Attribute) and node.attr in classes:
                violations.append(AdapterReference(path, node.lineno, node.attr))
    return violations


def _dynamic_import_target(call: ast.Call) -> str:
    """The adapters module named by `__import__("...")` or `import_module("...")`,
    or "" if this call is not one (a non-literal argument cannot be resolved)."""
    func = call.func
    called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if called not in ("__import__", "import_module") or not call.args:
        return ""
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value if _is_raw_schema(arg.value) else ""
    return ""


def _orchestration_modules(slice_root: Path) -> list[Path]:
    found: list[Path] = []
    for stem in ORCHESTRATION_STEMS:
        if (slice_root / f"{stem}.py").is_file():
            found.append(slice_root / f"{stem}.py")
        if (slice_root / stem).is_dir():
            found.extend(sorted((slice_root / stem).rglob("*.py")))
    return found


def _concrete_adapter_classes(adapters_root: Path) -> set[str]:
    """Names of classes under the adapters package deriving, transitively, from the
    contract base within that package."""
    if not adapters_root.is_dir():
        return set()
    bases: dict[str, set[str]] = {}
    for path in sorted(adapters_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(
                    b.id if isinstance(b, ast.Name) else b.attr
                    for b in node.bases
                    if isinstance(b, ast.Name | ast.Attribute)
                )
    concrete: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, parents in bases.items():
            if name not in concrete and (
                CONTRACT_BASE in parents or parents & concrete
            ):
                concrete.add(name)
                changed = True
    return concrete


# ------------------------------------- no adapter reaches a send or write endpoint (11)

WRITE_VERBS = frozenset({"PUT", "PATCH", "DELETE"})
TRANSPORT_SEND_MODULES = frozenset(
    {"base_source.py", "transport.py", "mcp_transport.py", "auth.py"}
)
"""Slice modules that may call ``Transport.send``: the contract's single dispatch
point, the transports themselves, the MCP fallback, and the OAuth token fetch."""


@dataclass(frozen=True)
class EndpointViolation:
    """One place source code could reach a send or write endpoint."""

    path: Path
    """Absolute path of the offending module."""

    lineno: int
    """1-based line of the literal, call or keyword."""

    detail: str
    """The path literal, verb, or call that was found."""


def find_send_path_literals(adapters_root: Path) -> list[EndpointViolation]:
    """Every string under the adapters that is a path naming a send word.

    A path starts with ``/`` and holds no whitespace, so a sentence that mentions a
    send word is not flagged. Strings built from literals are folded first, so
    adjacent literals, ``"/v1/" + "send"`` and an f-string are checked as the whole
    path (``{}`` stands for each interpolated value). One violation per line.

    Not catchable by an AST scan: a path assembled from variables, ``join``, ``%`` or
    ``.format`` of a non-literal, or read from data. The runtime check on the declared
    endpoints (``send_prohibition``) and the transport that refuses any undeclared
    endpoint are the backstop for those.
    """
    violations: list[EndpointViolation] = []
    for path, tree in _parsed(adapters_root):
        seen: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.expr):
                continue
            value = _folded_text(node)
            if (
                value is not None
                and node.lineno not in seen
                and value.startswith("/")
                and not any(c.isspace() for c in value)
                and send_capable_reason(value) is not None
            ):
                seen.add(node.lineno)
                violations.append(EndpointViolation(path, node.lineno, value))
    return violations


def _folded_text(node: ast.expr) -> str | None:
    """The text of a string literal, f-string or ``+`` of those; None otherwise."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(v.value) if isinstance(v, ast.Constant) else "{}" for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded_text(node.left), _folded_text(node.right)
        return None if left is None or right is None else left + right
    return None


def find_write_verb_uses(adapters_root: Path) -> list[EndpointViolation]:
    """Every ``.put/.patch/.delete`` call, ``PUT/PATCH/DELETE`` literal (any case, so
    ``getattr(client, "delete")`` too) and ``from <lib> import delete [as x]``."""
    violations: list[EndpointViolation] = []
    for path, tree in _parsed(adapters_root):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr.upper() in WRITE_VERBS
            ):
                violations.append(EndpointViolation(path, node.lineno, node.func.attr))
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.strip().upper() in WRITE_VERBS
            ):
                violations.append(EndpointViolation(path, node.lineno, node.value))
            elif isinstance(node, ast.ImportFrom):
                violations.extend(
                    EndpointViolation(path, node.lineno, alias.name)
                    for alias in node.names
                    if alias.name.upper() in WRITE_VERBS
                )
    return violations


NETWORK_CLIENT_ROOTS = frozenset(
    {"httpx", "requests", "aiohttp", "urllib3", "httplib2", "websockets", "socket",
     "smtplib", "imaplib", "poplib", "ftplib", "telnetlib", "xmlrpc"}
)  # fmt: skip
NETWORK_CLIENT_MODULES = frozenset({"http.client", "urllib.request"})
NETWORK_CLIENT_MODULE_NAME = "transport.py"
"""The one slice module, directly in the slice root, that may open a connection."""


def find_network_client_imports(slice_root: Path) -> list[EndpointViolation]:
    """Every import of an HTTP, socket or mail client outside the slice's transport.

    An adapter, or a module added later, that imports ``requests`` or ``smtplib`` could
    issue a write or a send that no declared endpoint describes; the only door out of
    the slice is ``transport.py``. URL parsing (``urllib.parse``) is not a client.
    Literal ``__import__`` and ``import_module`` calls are included. Not catchable: a
    module name read from data, or a client reached through another third-party
    package that wraps one.
    """
    violations: list[EndpointViolation] = []
    package = _package_of(slice_root / "x.py", slice_root.parent)
    for path, tree in _parsed(slice_root):
        if path == slice_root / NETWORK_CLIENT_MODULE_NAME:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = _imported_modules(node, package)
            elif isinstance(node, ast.Call):
                names = [_dynamic_module(node)]
            else:
                continue
            hit = next((n for n in names if _is_network_client(n)), None)
            if hit is not None:
                violations.append(EndpointViolation(path, node.lineno, hit))
    return violations


def _dynamic_module(call: ast.Call) -> str:
    """The literal module named by ``__import__`` or ``import_module``, else ``""``."""
    func = call.func
    called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
    if called not in ("__import__", "import_module") or not call.args:
        return ""
    arg = call.args[0]
    return (
        arg.value
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        else ""
    )


def _is_network_client(module: str) -> bool:
    return (
        module.split(".")[0] in NETWORK_CLIENT_ROOTS or module in NETWORK_CLIENT_MODULES
    )


def find_direct_transport_sends(src_root: Path) -> list[EndpointViolation]:
    """Every ``<x>.send(..., json_body=...)`` call outside `TRANSPORT_SEND_MODULES`.

    ``json_body`` is the keyword only ``Transport.send`` takes. Adapters, and anything
    else, dispatch through ``BaseLeadSource``, which paces and classifies the call.
    """
    if not src_root.is_dir():
        raise NotADirectoryError(f"src root does not exist: {src_root}")
    violations: list[EndpointViolation] = []
    for path, tree in _parsed(src_root):
        if path.name in TRANSPORT_SEND_MODULES and path.parent.name == "lead_ingestion":
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send"
                and any(k.arg == "json_body" for k in node.keywords)
            ):
                violations.append(EndpointViolation(path, node.lineno, "send"))
    return violations


def _parsed(root: Path) -> list[tuple[Path, ast.Module]]:
    """Every module under ``root`` except tests and fixtures, parsed (test code may
    plant violations, and a fixture is data)."""
    return [
        (p, ast.parse(p.read_text(encoding="utf-8"), filename=str(p)))
        for p in sorted(root.rglob("*.py"))
        if not {"tests", "fixtures"} & set(p.relative_to(root).parts)
    ]
