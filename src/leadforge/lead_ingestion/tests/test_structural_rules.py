"""Canonical-boundary and persistence structural rules (19.4).

Rules already proven elsewhere, not repeated here: raw-schema imports outside the slice
and the orchestrator's concrete-adapter references (``test_slice_structure``), SQL
strings, ``sqlalchemy.dialects`` and ``text`` outside migrations
(``test_store_schema``), dialect names outside ``database.py``
(``test_database_engine``), ``create_all`` attribute calls and the
migrations-cover-every-table check (``test_store_migrations``).

This module adds the gaps those scans leave and proves each new scan with one mutation
per rule over synthetic source (never the real files), plus a non-vacuity check that the
scan walks real modules.
"""

import ast
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import leadforge.lead_ingestion as slice_pkg
from leadforge.lead_ingestion.structure_guard import (
    CANONICAL_LEAD_BUILDERS,
    ENGINE_SPECIFIC_ALLOWLIST,
    SCHEMA_CREATION_ALLOWLIST,
    BuilderAllowance,
    _scan,
    find_canonical_lead_constructions,
    find_engine_specific_references,
    find_init_reexports,
    find_raw_schema_imports_outside_slice,
    find_schema_creation_calls,
    find_string_built_sql,
    find_transport_leaks_above_contract,
)

SLICE_ROOT = Path(slice_pkg.__file__).resolve().parent
SRC_ROOT = SLICE_ROOT.parents[1]
ADAPTERS = "leadforge.lead_ingestion.adapters"


def _slice(tmp_path: Path, **modules: str) -> Path:
    """A fake slice root holding `modules`: ``name`` -> source, ``__`` for ``/``, and
    ``init`` for the slice's ``__init__.py``."""
    root = tmp_path / "src" / "leadforge" / "lead_ingestion"
    root.mkdir(parents=True)
    for name, source in modules.items():
        stem = "__init__" if name == "init" else name.replace("__", "/")
        path = root / f"{stem}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return root


# real tree is clean and really scanned


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_scans_walk_a_meaningful_number_of_real_modules() -> None:
    walked = {
        p.relative_to(SLICE_ROOT).as_posix() for p, _ in _scan(SLICE_ROOT, "walk")
    }

    assert len(walked) >= 40
    assert {
        "orchestrator.py",
        "projection.py",
        "models.py",
        "database.py",
        "store/models.py",
    } <= walked
    assert any(w.startswith("store/migrations/versions/") for w in walked)
    assert any(w.startswith("adapters/") for w in walked)


# Verifies: specs/lead-source-adapters/requirements.md#1.1
@pytest.mark.parametrize(
    "scan",
    [
        find_init_reexports,
        find_canonical_lead_constructions,
        find_transport_leaks_above_contract,
        find_engine_specific_references,
        find_string_built_sql,
        find_schema_creation_calls,
    ],
)
def test_every_scan_refuses_an_empty_walk(scan: object, tmp_path: Path) -> None:
    root = _slice(tmp_path)
    with pytest.raises(RuntimeError, match="no modules"):
        scan(root)  # type: ignore[operator]


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_real_slice_has_no_boundary_or_persistence_violations() -> None:
    assert find_init_reexports(SLICE_ROOT) == []
    assert find_canonical_lead_constructions(SLICE_ROOT) == []
    assert find_transport_leaks_above_contract(SLICE_ROOT) == []
    assert find_engine_specific_references(SLICE_ROOT) == []
    assert find_string_built_sql(SLICE_ROOT) == []
    assert find_schema_creation_calls(SLICE_ROOT) == []


# 1.1 raw schema: dynamic imports, re-export


# Verifies: specs/lead-source-adapters/requirements.md#1.1
@pytest.mark.parametrize(
    "source",
    [
        f'import importlib\nm = importlib.import_module("{ADAPTERS}.provider_one")\n',
        f'm = __import__("{ADAPTERS}.provider_one")\n',
        f'from importlib import import_module\nm = import_module("{ADAPTERS}")\n',
    ],
)
def test_raw_schema_scan_flags_dynamic_import_outside_slice(
    tmp_path: Path, source: str
) -> None:
    src_root = tmp_path / "src"
    slice_root = src_root / "leadforge" / "lead_ingestion"
    slice_root.mkdir(parents=True)
    offender = src_root / "leadforge" / "scoring" / "score.py"
    offender.parent.mkdir(parents=True)
    offender.write_text(source)

    (hit,) = find_raw_schema_imports_outside_slice(src_root, slice_root)

    assert hit.path == offender.resolve()
    assert hit.module.startswith(ADAPTERS)
    assert hit.lineno >= 1


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_real_tree_outside_the_slice_is_walked() -> None:
    outside = [p for p in SRC_ROOT.rglob("*.py") if not p.is_relative_to(SLICE_ROOT)]

    assert outside, "the outside-slice scan would be vacuous"


# Verifies: specs/lead-source-adapters/requirements.md#1.1
@pytest.mark.parametrize(
    "source",
    [
        f"from {ADAPTERS}.provider_one import ProviderOneRaw\n",
        "from .adapters import provider_one\n",
        "from .adapters.provider_one.raw import PersonRaw as Person\n",
        f'import importlib\nx = importlib.import_module("{ADAPTERS}.provider_one")\n',
    ],
)
def test_init_reexport_scan_flags_adapter_import_in_slice_init(
    tmp_path: Path, source: str
) -> None:
    root = _slice(tmp_path, init=source, base_source="x = 1\n")

    (hit,) = find_init_reexports(root)

    assert hit.path == root / "__init__.py"
    assert hit.lineno >= 1
    assert "adapters" in hit.detail


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_init_reexport_scan_allows_a_plain_init(tmp_path: Path) -> None:
    root = _slice(tmp_path, init='"""Slice."""\n', base_source="x = 1\n")

    assert find_init_reexports(root) == []


# 1.1 only the Merge Engine builds CanonicalLead


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_canonical_lead_builders_allowlist_is_documented() -> None:
    # The store's read path rehydrates a saved projection (follow-up 2026-10-06).
    assert set(CANONICAL_LEAD_BUILDERS) == {
        "projection.py",
        "models.py",
        "store/lead_reader.py",
    }
    assert all(len(a.reason) > 20 for a in CANONICAL_LEAD_BUILDERS.values())
    assert all(a.functions for a in CANONICAL_LEAD_BUILDERS.values())


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_the_store_reader_rehydrates_and_never_projects() -> None:
    """The reader's exemption covers rehydrating stored values only: it imports no
    Merge Engine module, so it cannot re-project, re-resolve or re-cluster a lead."""
    tree = ast.parse((SLICE_ROOT / "store" / "lead_reader.py").read_text())
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    merge_engine = {
        f"leadforge.lead_ingestion.{name}"
        for name in (
            "projection",
            "conflicts",
            "clustering",
            "superseded",
            "remerge",
            "over_merge",
            "primary_domain",
        )
    }
    assert not imported & merge_engine
    (allowance,) = (
        a for path, a in CANONICAL_LEAD_BUILDERS.items() if path.startswith("store/")
    )
    assert allowance.functions == {"_rehydrate"}


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_construction_allowance_is_exactly_the_real_builder_functions() -> None:
    """A stale or over-wide entry fails: each allowed function holds a real site."""
    hits = find_canonical_lead_constructions(SLICE_ROOT, allowed={})

    assert {(h.path.relative_to(SLICE_ROOT).as_posix(), h.scope) for h in hits} == {
        (module, fn)
        for module, a in CANONICAL_LEAD_BUILDERS.items()
        for fn in a.functions
    }


# Verifies: specs/lead-source-adapters/requirements.md#1.1
@pytest.mark.parametrize(
    ("source", "detail"),
    [
        (
            "from m import CanonicalLead\nx = CanonicalLead(email=None)\n",
            "CanonicalLead",
        ),
        ("import m\nx = m.CanonicalLead(email=None)\n", "CanonicalLead"),
        (
            "from m import CanonicalLead\nx = CanonicalLead.model_validate({})\n",
            "model_validate",
        ),
        (
            "from m import CanonicalLead\nx = CanonicalLead.model_construct()\n",
            "model_construct",
        ),
        (
            "from m import CanonicalLead\n"
            "def f(lead: CanonicalLead):\n"
            "    return lead.model_copy(update={'a': 1})\n",
            "model_copy",
        ),
        (
            "from m import CanonicalLead\n"
            "def f(lead: 'CanonicalLead | None'):\n"
            "    return lead.model_copy(update={'a': 1})\n",
            "model_copy",
        ),
    ],
)
def test_construction_scan_flags_a_lead_built_outside_the_merge_engine(
    tmp_path: Path, source: str, detail: str
) -> None:
    root = _slice(tmp_path, normalizer=source)

    (hit,) = find_canonical_lead_constructions(root)

    assert hit.path == root / "normalizer.py"
    assert hit.detail == detail
    assert hit.lineno >= 2


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_construction_scan_allows_the_merge_engine_and_other_models(
    tmp_path: Path,
) -> None:
    root = _slice(
        tmp_path,
        projection=(
            "from m import CanonicalLead\n"
            "def _build_lead():\n    return CanonicalLead(email=None)\n"
        ),
        companies=(
            "from m import CompanySignal\n"
            "def f(c: CompanySignal):\n    return c.model_copy(update={'a': 1})\n"
        ),
    )

    assert find_canonical_lead_constructions(root) == []


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_construction_scan_ignores_tests_and_fixtures(tmp_path: Path) -> None:
    root = _slice(
        tmp_path,
        projection="x = 1\n",
        tests__test_a="x = CanonicalLead()\n",
        fixtures__f="x = CanonicalLead()\n",
    )

    assert find_canonical_lead_constructions(root) == []


# 20.1 no transport type above the contract


# Verifies: specs/lead-source-adapters/requirements.md#20.1
@pytest.mark.parametrize(
    ("module", "source", "detail"),
    [
        ("orchestrator", "import httpx\n", "httpx"),
        ("orchestrator", "from httpx import Response\n", "httpx"),
        ("projection", "import mcp\n", "mcp"),
        ("models", "from mcp.client import stdio\n", "mcp.client"),
        ("conflicts", "import requests\n", "requests"),
        ("orchestrator", "import aiohttp\n", "aiohttp"),
        (
            "orchestrator",
            "from leadforge.lead_ingestion.transport import Transport\n",
            "leadforge.lead_ingestion.transport",
        ),
        (
            "projection",
            "from leadforge.lead_ingestion import mcp_transport\n",
            "leadforge.lead_ingestion.mcp_transport",
        ),
        ("orchestrator", "from .transport import RestTransport\n", "transport"),
        (
            "orchestrator",
            'import importlib\nm = importlib.import_module("httpx")\n',
            "httpx",
        ),
    ],
)
def test_transport_scan_flags_transport_types_above_the_contract(
    tmp_path: Path, module: str, source: str, detail: str
) -> None:
    root = _slice(tmp_path, **{module: source})

    (hit,) = find_transport_leaks_above_contract(root)

    assert hit.path == root / f"{module}.py"
    assert hit.detail.endswith(detail)
    assert hit.lineno >= 1


# Verifies: specs/lead-source-adapters/requirements.md#20.1
def test_transport_scan_allows_the_contract_and_the_transports_themselves(
    tmp_path: Path,
) -> None:
    root = _slice(
        tmp_path,
        orchestrator=(
            "from leadforge.lead_ingestion.base_source import BaseLeadSource\n"
        ),
        base_source="from leadforge.lead_ingestion.transport import Transport\n",
        transport="import httpx\n",
        mcp_transport="import mcp\n",
        adapters__one="from leadforge.lead_ingestion.transport import Transport\n",
    )

    assert find_transport_leaks_above_contract(root) == []


# 9.4 engine-specific code placement


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_engine_specific_allowlist_names_only_the_engine_module_with_a_reason() -> None:
    assert set(ENGINE_SPECIFIC_ALLOWLIST) == {"database.py", "structure_guard.py"}
    assert all(len(reason) > 20 for reason in ENGINE_SPECIFIC_ALLOWLIST.values())


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_engine_scan_is_not_vacuous_on_the_real_engine_module() -> None:
    hits = find_engine_specific_references(SLICE_ROOT, allowed={})

    assert {h.path.name for h in hits} == {"database.py", "structure_guard.py"}


# Verifies: specs/lead-source-adapters/requirements.md#9.4
@pytest.mark.parametrize(
    ("source", "detail"),
    [
        ("import psycopg\n", "psycopg"),
        ("import psycopg2.extras\n", "psycopg2.extras"),
        ("from asyncpg import connect\n", "asyncpg"),
        ("import aiosqlite\n", "aiosqlite"),
        ("import sqlite3\n", "sqlite3"),
        ("from sqlalchemy.dialects.postgresql import JSONB\n", "sqlalchemy.dialects"),
        ("from sqlalchemy.dialects import sqlite\n", "sqlalchemy.dialects"),
        ('URL = "postgresql+psycopg://u@h/d"\n', "postgresql"),
        ('URL = "sqlite:///x.db"\n', "sqlite"),
        ('Q = "PRAGMA journal_mode=WAL"\n', "pragma"),
        ("name = engine.dialect.name\n", "dialect"),
        ("name = engine.url.get_backend_name()\n", "get_backend_name"),
    ],
)
def test_engine_scan_flags_engine_specific_code_outside_the_engine_module(
    tmp_path: Path, source: str, detail: str
) -> None:
    root = _slice(tmp_path, store__contributions=source)

    (hit,) = find_engine_specific_references(root)

    assert hit.path == root / "store" / "contributions.py"
    assert detail in hit.detail
    assert hit.lineno >= 1


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_engine_scan_allows_the_allowlisted_module_migrations_and_prose(
    tmp_path: Path,
) -> None:
    root = _slice(
        tmp_path,
        database='import sqlite3\nX = "PRAGMA foreign_keys=ON"\nY = "sqlite"\n',
        store__migrations__versions__v1="import sqlite3\n",
        store__repo=(
            '"""Works on SQLite and PostgreSQL alike."""\nX = "sqlite is fine here"\n'
        ),
    )

    assert find_engine_specific_references(root) == []


# 9.4 bound parameters only


# Verifies: specs/lead-source-adapters/requirements.md#9.4
@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ('def f(s, x):\n    s.execute(f"SELECT {x}")\n', "f-string"),
        ('def f(s, x):\n    s.execute("SELECT " + x)\n', "concatenation"),
        ('def f(s, x):\n    s.execute("SELECT %s" % x)\n', "percent-format"),
        ('def f(s, x):\n    s.execute("SELECT {}".format(x))\n', "str.format"),
        (
            'def f(c, x):\n    c.exec_driver_sql(f"DELETE FROM t WHERE a={x}")\n',
            "f-string",
        ),
        ('def f(c, x):\n    c.executemany(f"x{x}", [])\n', "f-string"),
        (
            'import sqlalchemy as sa\ndef f(x):\n    return sa.text(f"{x}")\n',
            "f-string",
        ),
        (
            'from sqlalchemy import text\ndef f(x):\n    return text("a" + x)\n',
            "concatenation",
        ),
        ('def f(op, x):\n    op.execute(f"DROP TABLE {x}")\n', "f-string"),
    ],
)
def test_string_built_sql_scan_flags_each_construction(
    tmp_path: Path, source: str, kind: str
) -> None:
    root = _slice(tmp_path, store__repo=source)

    (hit,) = find_string_built_sql(root)

    assert hit.path == root / "store" / "repo.py"
    assert kind in hit.detail
    assert hit.lineno >= 1


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_string_built_sql_scan_reports_no_sql_text_in_detail(tmp_path: Path) -> None:
    root = _slice(
        tmp_path, store__repo='def f(s):\n    s.execute(f"SELECT {SECRET_XYZ}")\n'
    )

    (hit,) = find_string_built_sql(root)

    assert "SELECT" not in hit.detail
    assert "SECRET_XYZ" not in hit.detail


# Verifies: specs/lead-source-adapters/requirements.md#9.4
def test_string_built_sql_scan_allows_literals_and_bound_statements(
    tmp_path: Path,
) -> None:
    root = _slice(
        tmp_path,
        database='def f(cur):\n    cur.execute("PRAGMA foreign_keys=ON")\n',
        store__repo=(
            "import sqlalchemy as sa\n"
            "def f(s, M, ids):\n"
            "    s.execute(sa.delete(M).where(M.id.in_(ids)))\n"
            '    s.execute(sa.text("SELECT 1 WHERE :a = :b"), {"a": 1, "b": 2})\n'
            '    print(f"deleted {len(ids)}")\n'
        ),
    )

    assert find_string_built_sql(root) == []


# 9.7 migrations are the schema path


# Verifies: specs/lead-source-adapters/requirements.md#9.7
@pytest.mark.parametrize(
    ("source", "detail"),
    [
        ("def f(e, b):\n    b.metadata.create_all(e)\n", "create_all"),
        ("def f(e, m):\n    m.drop_all(e)\n", "drop_all"),
        ('def f(m, e):\n    getattr(m, "create_all")(e)\n', "create_all"),
        ("from x import create_all\n", "create_all"),
        ("from x import drop_all as d\n", "drop_all"),
    ],
)
def test_schema_creation_scan_flags_runtime_creation_in_application_code(
    tmp_path: Path, source: str, detail: str
) -> None:
    root = _slice(tmp_path, cli=source)

    (hit,) = find_schema_creation_calls(root)

    assert hit.path == root / "cli.py"
    assert hit.detail == detail


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_schema_creation_scan_covers_migrations_and_ignores_tests(
    tmp_path: Path,
) -> None:
    root = _slice(
        tmp_path,
        store__migrations__versions__v1="def u(m, e):\n    m.create_all(e)\n",
        tests__test_x="m.create_all(e)\n",
        cli="x = 1\n",
    )

    (hit,) = find_schema_creation_calls(root)

    assert hit.path.name == "v1.py"


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_schema_creation_scan_is_not_vacuous_and_its_allowlist_is_documented() -> None:
    hits = find_schema_creation_calls(SLICE_ROOT, allowed={})

    assert (
        {h.path.name for h in hits}
        == set(SCHEMA_CREATION_ALLOWLIST)
        == {"structure_guard.py"}
    )
    assert all(len(r) > 20 for r in SCHEMA_CREATION_ALLOWLIST.values())


# --- self-review additions: allowances stay narrow, scanners are true checks


def _only(root: Path, scan: object) -> list[tuple[str, int, str]]:
    hits = scan(root)  # type: ignore[operator]
    return [(h.path.relative_to(root).as_posix(), h.lineno, h.detail) for h in hits]


# Verifies: specs/lead-source-adapters/requirements.md#1.1
@pytest.mark.parametrize(
    "source",
    [
        "from m import CanonicalLead as CL\nx = CL()\n",
        "from m import CanonicalLead\nCL = CanonicalLead\nx = CL()\n",
        "from m import CanonicalLead\nclass S(CanonicalLead):\n    pass\n",
        "from m import CanonicalLead\nfrom pydantic import TypeAdapter\n"
        "A = TypeAdapter(CanonicalLead)\n",
        "from m import CanonicalLead\nx = CanonicalLead.from_orm(o)\n",
        "from m import CanonicalLead\nf = lambda: CanonicalLead()\n",
        "from m import CanonicalLead\n@d(CanonicalLead())\ndef f(): ...\n",
    ],
)
def test_construction_scan_flags_aliases_subclasses_and_adapters(
    tmp_path: Path, source: str
) -> None:
    root = _slice(tmp_path, normalizer=source)

    assert find_canonical_lead_constructions(root) != []


# Verifies: specs/lead-source-adapters/requirements.md#1.1
@pytest.mark.parametrize(
    "source",
    [
        "'\"\"CanonicalLead()\"\"'\n# CanonicalLead()\nx = 'CanonicalLead()'\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
        "    from m import CanonicalLead\ndef f(l: 'CanonicalLead'): return l\n",
        "from m import CanonicalLead\ndef f(l: CanonicalLead):\n"
        "    return l.model_copy(deep=True)\n",
    ],
)
def test_construction_scan_ignores_prose_annotations_and_plain_copies(
    tmp_path: Path, source: str
) -> None:
    root = _slice(tmp_path, normalizer=source)

    assert find_canonical_lead_constructions(root) == []


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_construction_allowance_is_per_function_not_per_file(tmp_path: Path) -> None:
    allowed = {"projection.py": BuilderAllowance(frozenset({"build"}), "x" * 30)}
    root = _slice(
        tmp_path,
        projection=(
            "from m import CanonicalLead\n"
            "def build():\n    return CanonicalLead()\n"
            "def other():\n    return CanonicalLead()\n"
            "TOP = CanonicalLead()\n"
        ),
    )

    hits = find_canonical_lead_constructions(root, allowed)

    assert sorted((h.lineno, h.scope) for h in hits) == [(5, "other"), (6, "")]


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_a_production_directory_named_tests_below_the_top_is_still_scanned(
    tmp_path: Path,
) -> None:
    root = _slice(
        tmp_path, store__tests__x="import sqlite3\n", tests__test_a="import sqlite3\n"
    )

    assert _only(root, find_engine_specific_references) == [
        ("store/tests/x.py", 1, "sqlite3")
    ]


# Verifies: specs/lead-source-adapters/requirements.md#1.1
@pytest.mark.parametrize(
    ("name", "source", "error"),
    [
        ("broken", "def f(:\n", SyntaxError),
    ],
)
def test_scans_fail_loudly_on_unparseable_modules(
    tmp_path: Path, name: str, source: str, error: type[Exception]
) -> None:
    root = _slice(tmp_path, **{name: source})
    with pytest.raises(error):
        find_string_built_sql(root)


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_scans_fail_loudly_on_non_utf8_modules(tmp_path: Path) -> None:
    root = _slice(tmp_path, ok="x = 1\n")
    (root / "latin.py").write_bytes(b"x = '\xe9'\n")

    with pytest.raises(UnicodeDecodeError):
        find_schema_creation_calls(root)


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_init_scan_refuses_a_slice_without_an_init_module(tmp_path: Path) -> None:
    root = _slice(tmp_path, base_source="x = 1\n")

    with pytest.raises(RuntimeError, match="__init__"):
        find_init_reexports(root)


# Verifies: specs/lead-source-adapters/requirements.md#20.1
@pytest.mark.parametrize(
    "module",
    ["run_report", "cli", "registry", "some_new_merge_step", "orchestrator__sub"],
)
def test_transport_scan_covers_every_module_not_declared_below_the_contract(
    tmp_path: Path, module: str
) -> None:
    root = _slice(tmp_path, **{module: "import httpx\n"})

    assert len(find_transport_leaks_above_contract(root)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#20.1
@pytest.mark.parametrize(
    "source",
    [
        "def f(r: 'httpx.Response'): ...\n",
        "def f() -> 'httpx.Response': ...\n",
        "class R:\n    x: 'requests.Response'\n",
        "def f(r: aiohttp.ClientResponse): ...\n",
    ],
)
def test_transport_scan_flags_library_types_named_in_annotations(
    tmp_path: Path, source: str
) -> None:
    root = _slice(tmp_path, orchestrator=source)

    assert len(find_transport_leaks_above_contract(root)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#20.1
def test_transport_scan_leaves_alone_annotations_that_merely_mention_words(
    tmp_path: Path,
) -> None:
    root = _slice(
        tmp_path,
        orchestrator="def f(r: 'Mapping[str, mcpish]', t: int) -> 'httpx_free': ...\n",
    )

    assert find_transport_leaks_above_contract(root) == []


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_engine_allowance_for_the_guard_covers_strings_only(tmp_path: Path) -> None:
    root = _slice(
        tmp_path,
        structure_guard=(
            "import sqlite3\nX = frozenset({'sqlite', 'postgresql'})\nd = e.dialect\n"
        ),
    )

    assert _only(root, find_engine_specific_references) == [
        ("structure_guard.py", 1, "sqlite3"),
        ("structure_guard.py", 3, "dialect"),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#9.3
@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy as sa\nx = sa.dialects.postgresql.JSONB\n",
        "import importlib\nm = importlib.import_module('psycopg')\n",
        "m = __import__('sqlite3')\n",
    ],
)
def test_engine_scan_flags_dialect_attribute_chains_and_dynamic_driver_imports(
    tmp_path: Path, source: str
) -> None:
    root = _slice(tmp_path, store__repo=source)

    assert len(find_engine_specific_references(root)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#9.3
def test_the_engine_module_is_still_held_to_every_other_rule(tmp_path: Path) -> None:
    root = _slice(
        tmp_path,
        database=(
            "from m import CanonicalLead\n"
            "def f(s, b, x):\n"
            "    b.metadata.create_all(s)\n"
            "    s.execute(f'SELECT {x}')\n"
            "    return CanonicalLead()\n"
        ),
    )

    assert len(find_schema_creation_calls(root)) == 1
    assert len(find_string_built_sql(root)) == 1
    assert len(find_canonical_lead_constructions(root)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_schema_creation_allowance_for_the_guard_covers_strings_only(
    tmp_path: Path,
) -> None:
    root = _slice(
        tmp_path,
        structure_guard="N = frozenset({'create_all'})\nBase.metadata.create_all(e)\n",
    )

    assert _only(root, find_schema_creation_calls) == [
        ("structure_guard.py", 2, "create_all")
    ]


# Verifies: specs/lead-source-adapters/requirements.md#9.7
@pytest.mark.parametrize(
    "source",
    [
        "def create_all():\n    pass\n",
        "x = 'crea' + 'te_all'\n",
        "class A:\n    def drop_all(self): ...\n",
    ],
)
def test_schema_creation_scan_flags_definitions_and_folded_strings(
    tmp_path: Path, source: str
) -> None:
    root = _slice(tmp_path, cli=source)

    assert len(find_schema_creation_calls(root)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#9.4
@pytest.mark.parametrize(
    ("source", "count"),
    [
        ("def f(s, a, b):\n    s.execute('SELECT ' + a + b)\n", 1),
        ("def f(s, a):\n    s.execute(a + ' WHERE x')\n", 1),
        ("def f(s):\n    s.execute(text('a' + 'b'))\n", 0),
    ],
)
def test_string_built_sql_scan_sees_chained_concatenation_only_with_a_variable(
    tmp_path: Path, source: str, count: int
) -> None:
    root = _slice(tmp_path, store__repo=source)

    assert len(find_string_built_sql(root)) == count


# Runtime: importing the package creates no schema, and the check can fail.


def _tables_after_importing(
    names: list[str], tmp_path: Path, extra_path: Path | None = None
) -> list[str]:
    """Import `names` in a fresh interpreter whose DATABASE_URL points at a tmp file
    and report the tables that file holds afterwards (a module that built an engine
    from the environment at import time and created a table shows up here)."""
    db_file = tmp_path / "import-time.db"
    script = (
        "import importlib, sys\n"
        "for n in sys.argv[1:]:\n    importlib.import_module(n)\n"
        "import sqlalchemy as sa\n"
        f"e = sa.create_engine('sqlite:///{db_file}')\n"
        "print(','.join(sa.inspect(e).get_table_names()))\n"
    )
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_file}",
        "PYTHONPATH": os.pathsep.join(
            [str(SRC_ROOT), *([str(extra_path)] if extra_path else [])]
        ),
    }
    done = subprocess.run(
        [sys.executable, "-c", script, *names],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return [t for t in done.stdout.strip().split(",") if t]


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_the_no_tables_check_fails_for_a_module_that_creates_a_table_on_import(
    tmp_path: Path,
) -> None:
    (tmp_path / "planted.py").write_text(
        "from leadforge.lead_ingestion.database import create_store_engine\n"
        "from leadforge.lead_ingestion.store.models import Base\n"
        "Base.metadata.create_all(create_store_engine())\n"
    )

    assert _tables_after_importing(["planted"], tmp_path, tmp_path) != []


# Verifies: specs/lead-source-adapters/requirements.md#9.7
def test_a_fresh_interpreter_importing_every_slice_module_creates_no_tables(
    tmp_path: Path,
) -> None:
    names = [
        m.name
        for m in pkgutil.walk_packages(slice_pkg.__path__, f"{slice_pkg.__name__}.")
        if ".tests" not in m.name and ".fixtures" not in m.name
    ]

    assert len(names) >= 40
    assert _tables_after_importing(names, tmp_path) == []
