"""Static guard: provider raw schemas stay private to the ingestion slice (1.1).

Convention: every provider raw Pydantic schema lives under `RAW_SCHEMA_PACKAGE`.
Modules outside the slice may import the slice's public types (e.g. `CanonicalLead`)
but never anything under that package.
"""

import ast
import re
from collections.abc import Mapping
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
            if isinstance(node, ast.Call) and _is_raw_schema(_dynamic_module(node)):
                violations.append(
                    RawSchemaImport(path, node.lineno, _dynamic_module(node))
                )
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


# ------------------------- canonical boundary and persistence placement rules (19.4)


@dataclass(frozen=True)
class RuleViolation:
    """One structural-rule breach: where it is and the identifier that breaches it.

    `detail` is a module, name or construct kind, never source text, so a message built
    from it cannot carry a secret that happens to sit in a string literal.
    """

    path: Path
    """Absolute path of the offending module."""

    lineno: int
    """1-based line of the offending statement or expression."""

    detail: str
    """The imported module, called name or construct kind that breaches the rule."""

    scope: str = ""
    """Module-level function or class holding the site; "" at module level."""


@dataclass(frozen=True)
class BuilderAllowance:
    """Where in one module a `CanonicalLead` may be built, and why."""

    functions: frozenset[str]
    """Module-level functions or classes whose bodies may build one."""

    reason: str


CANONICAL_LEAD_BUILDERS = {
    "projection.py": BuilderAllowance(
        frozenset({"_build_lead"}),
        "the Merge Engine's projection builds the one lead from resolved "
        "contributions (8.12)",
    ),
    "models.py": BuilderAllowance(
        frozenset({"share_company_signals"}),
        "returns copies of projected leads with one shared CompanySignal per "
        "company; the class definition itself is not a construction",
    ),
    "store/lead_reader.py": BuilderAllowance(
        frozenset({"_rehydrate"}),
        "rehydrates a lead the Merge Engine projected and the store saved, by "
        "validating the stored values; the module imports no Merge Engine module "
        "and a round-trip property test proves the lead equal to the projection "
        "(follow-up, user request 2026-10-06)",
    ),
}
"""Only these functions may build or copy a `CanonicalLead`; the allowance is per
function, so a new construction elsewhere in the same module is still a violation."""

_CANONICAL_LEAD = "CanonicalLead"
_LEAD_BUILD_METHODS = frozenset(
    {"model_validate", "model_validate_json", "model_validate_strings",
     "model_construct", "construct", "parse_obj", "parse_raw", "from_orm"}
)  # fmt: skip

BELOW_CONTRACT_MODULES = {
    "base_source.py": "the contract itself: it dispatches through the Transport "
    "protocol and builds the default transport for a mode",
    "transport.py": "the REST transport",
    "mcp_transport.py": "the MCP transport",
    "auth.py": "the OAuth token fetch goes through a Transport",
}
BELOW_CONTRACT_DIRS = ("adapters",)
"""Slice modules and directories on or below the contract (20.1), each with its reason.
Every other production module is above it and may name no transport type; a module
added later is held to the rule until it is declared here."""

TRANSPORT_LIBRARY_ROOTS = NETWORK_CLIENT_ROOTS | {"mcp"}
TRANSPORT_SLICE_MODULES = ("transport", "mcp_transport")
_TRANSPORT_ANNOTATION = re.compile(
    r"\b(?:" + "|".join(sorted(TRANSPORT_LIBRARY_ROOTS)) + r")\."
)

ENGINE_SPECIFIC_ALLOWLIST = {
    "database.py": "engine resolution: the one module that names a backend (SQLite, "
    "PostgreSQL), maps URL aliases and sets SQLite's foreign-key pragma (9.3)",
    "structure_guard.py": "this scanner: it holds the backend names it searches for "
    "as string data (string constants only; imports and attributes are still "
    "scanned)",
}
"""Slice modules outside the migrations directory allowed to name a database engine.
Only the engine rule is relaxed for them: every other rule still scans these files."""

STRING_DATA_ONLY_MODULES = frozenset({"structure_guard.py"})
"""Allowlisted modules where only string constants are exempt, not code."""

MIGRATIONS_PARTS = ("store", "migrations")
_ENGINE_DRIVER_ROOTS = frozenset(
    {"psycopg", "psycopg2", "asyncpg", "aiosqlite", "sqlite3", "pg8000"}
)
_ENGINE_ATTRS = frozenset({"dialect", "dialects", "get_backend_name", "get_dialect"})
_ENGINE_NAMES = frozenset({"sqlite", "postgresql", "postgres"})
_ENGINE_URL_PREFIXES = tuple(
    f"{name}{sep}" for name in _ENGINE_NAMES for sep in (":", "+")
)
_PRAGMA = re.compile(r"^\s*pragma\s", re.IGNORECASE)
_EXECUTE_CALLS = frozenset({"execute", "executemany", "exec_driver_sql", "text", "DDL"})
_SCHEMA_CREATION_NAMES = frozenset({"create_all", "drop_all"})
_NON_PRODUCTION_DIRS = frozenset({"tests", "fixtures"})


def _scan(root: Path, rule: str) -> list[tuple[Path, ast.Module]]:
    """The production modules a rule scans; an empty walk fails loudly.

    Only a top-level ``tests`` or ``fixtures`` directory is excluded, so a directory of
    that name deeper in production code is still scanned. A module that cannot be read
    or parsed raises instead of being skipped.
    """
    modules = [
        (p, ast.parse(p.read_text(encoding="utf-8"), filename=str(p)))
        for p in sorted(root.rglob("*.py"))
        if p.relative_to(root).parts[0] not in _NON_PRODUCTION_DIRS
    ]
    if not modules:
        raise RuntimeError(f"{rule}: no modules under {root} to scan")
    return modules


def _package_parts(slice_root: Path, path: Path) -> list[str]:
    return _package_of(path, slice_root.parent.parent)


def find_init_reexports(slice_root: Path) -> list[RuleViolation]:
    """Every import of the adapters package in the slice's ``__init__.py``.

    Importing a raw schema there would re-export it as ``leadforge.lead_ingestion.X``,
    which the outside-the-slice import scan sees only as an import of the slice.
    Not catchable: a relative ``import_module(".adapters", package)``.
    """
    violations: list[RuleViolation] = []
    init = slice_root / "__init__.py"
    scanned = False
    for path, tree in _scan(slice_root, "init re-exports"):
        if path != init:
            continue
        scanned = True
        package = _package_parts(slice_root, path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = _imported_modules(node, package)
            elif isinstance(node, ast.Call):
                names = [_dynamic_module(node)]
            else:
                continue
            hit = next((n for n in names if _is_raw_schema(n)), None)
            if hit is not None:
                violations.append(RuleViolation(path, node.lineno, hit))
    if not scanned:
        raise RuntimeError(f"init re-exports: {init} was not scanned")
    return violations


def find_canonical_lead_constructions(
    slice_root: Path, allowed: Mapping[str, BuilderAllowance] = CANONICAL_LEAD_BUILDERS
) -> list[RuleViolation]:
    """Every production site that builds a `CanonicalLead` outside `allowed`.

    Sites: a ``CanonicalLead(...)`` call (also ``mod.CanonicalLead(...)``, an import
    alias and a ``CL = CanonicalLead`` alias), a ``CanonicalLead.model_validate``-style
    classmethod call, ``TypeAdapter(CanonicalLead)``, a subclass, and
    ``.model_copy(update=)`` on a name annotated ``CanonicalLead`` in the same function
    (a changed copy is a new lead). Not catchable: a lead reached through an
    unannotated variable, ``getattr``, ``type(x)(...)`` or ``exec``.
    """
    violations: list[RuleViolation] = []
    for path, tree in _scan(slice_root, "canonical lead construction"):
        allowance = allowed.get(path.relative_to(slice_root).as_posix())
        aliases = _lead_aliases(tree)
        for stmt in tree.body:
            scope = (
                stmt.name
                if isinstance(
                    stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
                )
                else ""
            )
            if allowance is not None and scope in allowance.functions:
                continue
            violations.extend(
                RuleViolation(path, lineno, detail, scope)
                for lineno, detail in sorted(_lead_build_sites(stmt, aliases))
            )
    return violations


def _lead_aliases(tree: ast.Module) -> set[str]:
    """`CanonicalLead` and every name bound to it by ``as`` or a plain assignment."""
    names = {_CANONICAL_LEAD}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            bound: list[str] = []
            if isinstance(node, ast.ImportFrom):
                bound = [a.asname for a in node.names if a.asname and a.name in names]
            elif isinstance(node, ast.Assign) and _name_of(node.value) in names:
                bound = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if not set(bound) <= names:
                names.update(bound)
                changed = True
    return names


def _lead_build_sites(stmt: ast.stmt, aliases: set[str]) -> set[tuple[int, str]]:
    sites: set[tuple[int, str]] = set()
    for node in ast.walk(stmt):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            lead_names = _lead_annotated_names(node, aliases)
            sites.update(
                (n.lineno, "model_copy")
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and _is_update_copy(n, lead_names)
            )
        elif isinstance(node, ast.ClassDef):
            if any(_name_of(b) in aliases for b in node.bases):
                sites.add((node.lineno, "subclass"))
        elif isinstance(node, ast.Call):
            detail = _lead_call_detail(node, aliases)
            if detail:
                sites.add((node.lineno, detail))
    return sites


def _lead_call_detail(call: ast.Call, aliases: set[str]) -> str:
    func = call.func
    if _name_of(func) in aliases:
        return _CANONICAL_LEAD
    if (
        isinstance(func, ast.Attribute)
        and func.attr in _LEAD_BUILD_METHODS
        and _name_of(func.value) in aliases
    ):
        return func.attr
    if _name_of(func) == "TypeAdapter" and any(
        _name_of(a) in aliases for a in call.args
    ):
        return "TypeAdapter"
    return ""


def _name_of(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ""


def _lead_annotated_names(
    func: ast.FunctionDef | ast.AsyncFunctionDef, aliases: set[str]
) -> set[str]:
    def names_lead(annotation: ast.expr) -> bool:
        text = ast.unparse(annotation)
        return any(re.search(rf"\b{re.escape(a)}\b", text) for a in aliases)

    args = [*func.args.posonlyargs, *func.args.args, *func.args.kwonlyargs]
    names = {a.arg for a in args if a.annotation and names_lead(a.annotation)}
    names.update(
        n.target.id
        for n in ast.walk(func)
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and names_lead(n.annotation)
    )
    return names


def _is_update_copy(node: ast.Call, lead_names: set[str]) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "model_copy"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in lead_names
        and any(k.arg == "update" for k in node.keywords)
    )


def find_transport_leaks_above_contract(slice_root: Path) -> list[RuleViolation]:
    """Every transport library or slice-transport reference above the contract.

    Above ``BaseLeadSource`` nothing may name REST or MCP (20.1). "Above" is every
    production module not declared in `BELOW_CONTRACT_MODULES` / `BELOW_CONTRACT_DIRS`,
    so a module added later is covered. A violation is a ``httpx``, other client
    library or ``mcp`` import, an import of the slice's ``transport`` or
    ``mcp_transport``, a literal ``__import__``/``import_module`` of one, or an
    annotation (also a string one) naming a library type such as ``httpx.Response``.
    Not catchable: a library type reached through an unannotated value.
    """
    violations: list[RuleViolation] = []
    for path, tree in _scan(slice_root, "transport types above the contract"):
        if _is_below_contract(path.relative_to(slice_root)):
            continue
        package = _package_parts(slice_root, path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.expr | ast.stmt | ast.arg):
                continue
            hit = ""
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = _imported_modules(node, package)
            elif isinstance(node, ast.Call):
                names = [_dynamic_module(node)]
            else:
                names = []
                hit = _annotation_leak(node)
            hit = hit or next((n for n in names if _is_transport_type_module(n)), "")
            if hit:
                violations.append(RuleViolation(path, node.lineno, hit))
    return violations


def _is_below_contract(rel: Path) -> bool:
    if len(rel.parts) == 1:
        return rel.name in BELOW_CONTRACT_MODULES
    return rel.parts[0] in BELOW_CONTRACT_DIRS


def _annotation_leak(node: ast.AST) -> str:
    """``annotation:<library>`` when a type annotation names a transport library."""
    annotations: list[ast.expr] = []
    if isinstance(node, ast.arg | ast.AnnAssign) and node.annotation:
        annotations.append(node.annotation)
    elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns:
        annotations.append(node.returns)
    for annotation in annotations:
        for part in ast.walk(annotation):
            text = (
                part.value
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
                else ast.unparse(part)
                if isinstance(part, ast.Attribute)
                else ""
            )
            found = _TRANSPORT_ANNOTATION.search(text)
            if found:
                return f"annotation:{found.group(0).rstrip('.')}"
    return ""


def _is_transport_type_module(module: str) -> bool:
    if not module:
        return False
    if (
        module.split(".")[0] in TRANSPORT_LIBRARY_ROOTS
        or module in NETWORK_CLIENT_MODULES
    ):
        return True
    prefix = "leadforge.lead_ingestion"
    return any(
        module == f"{prefix}.{m}" or module.startswith(f"{prefix}.{m}.")
        for m in TRANSPORT_SLICE_MODULES
    )


def find_engine_specific_references(
    slice_root: Path, allowed: Mapping[str, str] = ENGINE_SPECIFIC_ALLOWLIST
) -> list[RuleViolation]:
    """Every database-engine reference outside the migrations directory and `allowed`.

    A driver import (``psycopg``, ``aiosqlite``, ``sqlite3``, ...), also through a
    literal ``import_module``, a ``sqlalchemy.dialects`` import, a
    ``.dialect``/``.dialects``/``get_backend_name`` access, a backend-named string or
    URL, and a ``PRAGMA`` statement (9.3, 9.4). Text that merely mentions a backend is
    not flagged. Modules in `STRING_DATA_ONLY_MODULES` are exempt for string constants
    only. Not catchable: ``getattr(e, "dialect")``.
    """
    violations: list[RuleViolation] = []
    for path, tree in _scan(slice_root, "engine-specific code"):
        rel = path.relative_to(slice_root)
        if (
            rel.as_posix() in allowed and rel.as_posix() not in STRING_DATA_ONLY_MODULES
        ) or rel.parts[: len(MIGRATIONS_PARTS)] == MIGRATIONS_PARTS:
            continue
        strings = rel.as_posix() not in STRING_DATA_ONLY_MODULES
        if not strings and rel.as_posix() not in allowed:
            strings = True
        package = _package_parts(slice_root, path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.expr | ast.stmt):
                continue
            detail = _engine_detail(node, package, strings)
            if detail:
                violations.append(RuleViolation(path, node.lineno, detail))
    return violations


def _is_engine_module(module: str) -> bool:
    return module.split(".")[0] in _ENGINE_DRIVER_ROOTS or module.startswith(
        "sqlalchemy.dialects"
    )


def _engine_detail(node: ast.expr | ast.stmt, package: list[str], strings: bool) -> str:
    if isinstance(node, ast.Import | ast.ImportFrom):
        return next(
            (m for m in _imported_modules(node, package) if _is_engine_module(m)), ""
        )
    if isinstance(node, ast.Call) and _is_engine_module(_dynamic_module(node)):
        return _dynamic_module(node)
    if isinstance(node, ast.Attribute) and node.attr in _ENGINE_ATTRS:
        return node.attr
    if strings and isinstance(node, ast.Constant) and isinstance(node.value, str):
        low = node.value.strip().lower()
        if _PRAGMA.match(node.value):
            return "pragma"
        if low in _ENGINE_NAMES:
            return low
        return next(
            (p.rstrip(":+") for p in _ENGINE_URL_PREFIXES if low.startswith(p)), ""
        )
    return ""


def find_string_built_sql(slice_root: Path) -> list[RuleViolation]:
    """Every statement handed to ``execute``/``text`` that is built from a string.

    Bound parameters only (9.4): an f-string, a ``+`` chain that mixes a string with a
    non-literal, a ``%`` format or ``str.format`` as the first argument of ``execute``,
    ``executemany``, ``exec_driver_sql``, ``text`` or ``DDL``. Migrations are scanned
    too. Not catchable: a statement assembled in a variable first, or passed by keyword.
    """
    violations: list[RuleViolation] = []
    for path, tree in _scan(slice_root, "string-built SQL"):
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            callee = _name_of(node.func)
            kind = _string_build_kind(node.args[0])
            if callee in _EXECUTE_CALLS and kind:
                violations.append(
                    RuleViolation(path, node.lineno, f"{kind} passed to {callee}")
                )
    return violations


def _concat_operands(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return [*_concat_operands(node.left), *_concat_operands(node.right)]
    return [node]


def _string_build_kind(arg: ast.expr) -> str:
    if isinstance(arg, ast.JoinedStr):
        return "f-string"
    if isinstance(arg, ast.BinOp):
        if isinstance(arg.op, ast.Mod) and _is_str(arg.left):
            return "percent-format"
        operands = _concat_operands(arg)
        if (
            isinstance(arg.op, ast.Add)
            and any(_is_str(o) for o in operands)
            and not all(_is_str_constant(o) for o in operands)
        ):
            return "concatenation"
    if (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "format"
        and _is_str(arg.func.value)
    ):
        return "str.format"
    return ""


def _is_str_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_str(node: ast.expr) -> bool:
    return isinstance(node, ast.JoinedStr) or _is_str_constant(node)


SCHEMA_CREATION_ALLOWLIST = {
    "structure_guard.py": "this scanner: it holds the method names it searches for "
    "as string data (strings only; a call, attribute or definition is still flagged)",
}
"""Slice modules allowed to hold ``create_all``/``drop_all`` as string data only."""


def find_schema_creation_calls(
    slice_root: Path, allowed: Mapping[str, str] = SCHEMA_CREATION_ALLOWLIST
) -> list[RuleViolation]:
    """Every ``create_all``/``drop_all`` reference in production code, migrations too.

    Migrations are the only schema path (9.7); they use Alembic operations, never the
    metadata's ``create_all``. Covers an attribute, a bare name, an imported name, a
    function or class definition of that name, and a string, also one built from
    literals (``getattr(meta, "create_all")``). Modules in `allowed` are exempt for
    strings only.
    """
    violations: list[RuleViolation] = []
    for path, tree in _scan(slice_root, "schema creation"):
        strings = path.relative_to(slice_root).as_posix() not in allowed
        for node in ast.walk(tree):
            if not isinstance(node, ast.expr | ast.stmt):
                continue
            name = _schema_creation_name(node, strings)
            if name in _SCHEMA_CREATION_NAMES:
                violations.append(RuleViolation(path, node.lineno, name))
    return violations


def _schema_creation_name(node: ast.expr | ast.stmt, strings: bool) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name
    if isinstance(node, ast.ImportFrom):
        return next(
            (a.name for a in node.names if a.name in _SCHEMA_CREATION_NAMES), ""
        )
    if strings and isinstance(node, ast.expr):
        return _folded_text(node) or ""
    return ""
