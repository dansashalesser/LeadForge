"""The root command carries the ingestion and outreach commands (12.1, 12.2)."""

import ast
import shutil
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from typer.testing import CliRunner

import leadforge.lead_ingestion as ingestion_pkg
from leadforge.cli import app
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.outreach.tables import OutreachMessage, OutreachSearch

CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"
runner = CliRunner()
SEARCH_COMMANDS = ["free-text", "workers", "users"]


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    for name in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE", "LEADFORGE_MATCH_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    discovered = SourceRegistry.discover()
    for source in discovered.names():
        for variable in discovered.source_class(source).required_env:
            monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'store.db'}")
    return tmp_path


# Verifies: outreach requirements 12.1
def test_every_outreach_command_appears_in_the_help() -> None:
    top = runner.invoke(app, ["--help"])
    group = runner.invoke(app, ["outreach", "--help"])
    search = runner.invoke(app, ["outreach", "search", "--help"])

    assert top.exit_code == group.exit_code == search.exit_code == 0
    assert "outreach" in top.output
    assert "ingest" in top.output
    for command in ("search", "tick", "messages", "report", "scorecard"):
        assert command in group.output
    for command in SEARCH_COMMANDS:
        assert command in search.output


# Verifies: outreach requirements 12.1
def test_the_leadforge_script_points_at_the_root_app() -> None:
    text = (Path(__file__).resolve().parents[4] / "pyproject.toml").read_text()

    assert 'leadforge = "leadforge.cli:app"' in text


# Verifies: outreach requirements 9.2
def test_ingestion_never_imports_outreach() -> None:
    root = Path(ingestion_pkg.__file__).parent
    offenders = [
        str(p)
        for p in root.rglob("*.py")
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("leadforge.outreach")
        )
        or (
            isinstance(node, ast.Import)
            and any(a.name.startswith("leadforge.outreach") for a in node.names)
        )
    ]

    assert offenders == []


# Verifies: outreach requirements 12.2
# Verifies: outreach requirements 1.4
def test_a_search_prints_its_plan_then_the_funnel_and_the_outbox_path(
    workdir: Path,
) -> None:
    result = runner.invoke(app, ["outreach", "search", "users", "--product", "mongodb"])

    assert result.exit_code == 0, result.output
    plan_at = result.output.index('"mode": "users"')
    funnel_at = result.output.index("gathered")
    assert plan_at < funnel_at
    assert "selected:" in result.output
    assert "rejected:" in result.output
    assert "needs_enrichment:" in result.output
    assert f"outbox: {Path('outbox/dry_run.jsonl')}" in result.output
    assert "judge: off" in result.output


# Verifies: outreach requirements 12.1
def test_each_search_mode_runs_from_the_command_line(workdir: Path) -> None:
    free = runner.invoke(app, ["outreach", "search", "free-text", "teams on mongodb"])
    users = runner.invoke(
        app, ["outreach", "search", "users", "--product", "couchbase"]
    )
    workers = runner.invoke(
        app, ["outreach", "search", "workers", "Acme", "--domain", "acme.example"]
    )

    assert (free.exit_code, users.exit_code, workers.exit_code) == (0, 0, 0)
    assert "compiler: offline" in free.output
    assert "acme.example" in workers.output


# Verifies: outreach requirements 3.3
def test_a_workers_search_with_no_domain_exits_one_naming_the_missing_domain(
    workdir: Path,
) -> None:
    result = runner.invoke(app, ["outreach", "search", "workers", "Nowhere Inc"])

    assert result.exit_code == 1
    assert "no known domain" in result.output
    assert not (workdir / "store.db").exists() or _searches(workdir) == 0


def _searches(workdir: Path) -> int:
    engine = sa.create_engine(f"sqlite:///{workdir / 'store.db'}")
    try:
        with Session(engine) as session:
            return (
                session.scalar(sa.select(sa.func.count()).select_from(OutreachSearch))
                or 0
            )
    finally:
        engine.dispose()


# Verifies: outreach requirements 1.1
def test_an_unknown_term_or_a_query_naming_nothing_exits_one(workdir: Path) -> None:
    result = runner.invoke(
        app, ["outreach", "search", "free-text", "people who garden"]
    )

    assert result.exit_code == 1
    assert "error:" in result.output


# Verifies: outreach requirements 12.1
def test_tick_messages_and_report_work_on_a_stored_search(workdir: Path) -> None:
    runner.invoke(app, ["outreach", "search", "users", "--product", "mongodb"])
    engine = sa.create_engine(f"sqlite:///{workdir / 'store.db'}")
    with Session(engine) as session:
        search_id = session.scalars(sa.select(OutreachSearch.id)).one()
        messages_stored = session.scalar(
            sa.select(sa.func.count()).select_from(OutreachMessage)
        )
    engine.dispose()

    tick = runner.invoke(
        app, ["outreach", "tick", "--search", str(search_id), "--days", "9"]
    )
    listed = runner.invoke(app, ["outreach", "messages", "--search", str(search_id)])
    md = runner.invoke(app, ["outreach", "report", "--search", str(search_id)])
    js = runner.invoke(
        app, ["outreach", "report", "--search", str(search_id), "--format", "json"]
    )

    assert tick.exit_code == listed.exit_code == md.exit_code == js.exit_code == 0
    assert "action(s)" in tick.output
    assert f"{messages_stored} message(s)" in listed.output
    assert "## Funnel" in md.output
    assert '"funnel"' in js.output


# Verifies: outreach requirements 12.1
def test_tick_refuses_both_a_time_and_a_number_of_days(workdir: Path) -> None:
    result = runner.invoke(
        app, ["outreach", "tick", "--days", "1", "--now", "2026-10-07"]
    )

    assert result.exit_code != 0


# Verifies: outreach requirements 12.1
def test_the_readers_name_the_fix_on_a_store_that_was_never_set_up(
    workdir: Path,
) -> None:
    result = runner.invoke(
        app, ["outreach", "report", "--search", "00000000-0000-0000-0000-000000000000"]
    )

    assert result.exit_code == 2
    assert "configuration error" in result.output


# Verifies: outreach requirements 11.4
def test_the_report_command_masks_unless_reveal(workdir: Path) -> None:
    runner.invoke(app, ["outreach", "search", "users", "--product", "mongodb"])
    engine = sa.create_engine(f"sqlite:///{workdir / 'store.db'}")
    with Session(engine) as session:
        search_id = session.scalars(sa.select(OutreachSearch.id)).one()
    engine.dispose()

    plain = runner.invoke(
        app, ["outreach", "report", "--search", str(search_id), "--format", "json"]
    )
    shown = runner.invoke(
        app,
        [
            "outreach",
            "report",
            "--search",
            str(search_id),
            "--format",
            "json",
            "--reveal",
        ],
    )

    assert "***@" in plain.output
    assert "***@" not in shown.output
    assert plain.output != shown.output


# Verifies: outreach requirements 15.2
def test_the_scorecard_command_prints_the_mismatch_count(workdir: Path) -> None:
    runner.invoke(app, ["outreach", "search", "users", "--product", "mongodb"])
    engine = sa.create_engine(f"sqlite:///{workdir / 'store.db'}")
    with Session(engine) as session:
        search_id = session.scalars(sa.select(OutreachSearch.id)).one()
    engine.dispose()

    result = runner.invoke(app, ["outreach", "scorecard", "--search", str(search_id)])
    missing = runner.invoke(
        app,
        [
            "outreach",
            "scorecard",
            "--search",
            str(search_id),
            "--answer-key",
            "nope.json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "mismatches:" in result.output
    assert "mismatched" in result.output
    assert missing.exit_code != 0


# Verifies: specs/user-recognition/requirements.md#2.2
def test_a_users_search_with_no_product_exits_cleanly_before_any_provider_call(
    workdir: Path,
) -> None:
    result = runner.invoke(app, ["outreach", "search", "users"])

    assert result.exit_code != 0
    assert "catalog product" in result.output
    assert "plan (" not in result.output  # no plan shown, so nothing was spent


# Verifies: specs/user-recognition/requirements.md#2.2
def test_include_ecosystem_adds_the_vendors_ecosystem_terms_to_the_plan(
    workdir: Path,
) -> None:
    from leadforge.lead_ingestion.catalog import load_catalog

    vendor = next(v for v in load_catalog().vendors() if v.ecosystem)
    base = [
        *("outreach", "search", "users"),
        *("--vendor", vendor.key, "--product", vendor.products[0].key),
    ]
    ecosystem_key = f'"{vendor.ecosystem[0].key}"'

    without = runner.invoke(app, base)
    with_eco = runner.invoke(app, [*base, "--include-ecosystem"])

    assert without.exit_code == 0, without.output
    assert with_eco.exit_code == 0, with_eco.output
    assert ecosystem_key not in without.output
    assert ecosystem_key in with_eco.output


# Verifies: specs/user-recognition/requirements.md#4.5
def test_usage_budget_overrides_the_config_search_budget_for_one_run(
    workdir: Path,
) -> None:
    from leadforge.outreach.config import load_outreach_config
    from leadforge.outreach.runtime import with_usage_overrides

    config = load_outreach_config()

    changed = with_usage_overrides(config, include_ecosystem=True, usage_budget=7)

    assert changed.usage.budget.searches == 7
    assert changed.usage.budget.fetches == config.usage.budget.fetches
    assert changed.usage.include_ecosystem is True
    assert with_usage_overrides(config) is config
    with pytest.raises(ValueError, match="usage-budget"):
        with_usage_overrides(config, usage_budget=-1)


# Verifies: specs/user-recognition/requirements.md#4.5
def test_a_negative_usage_budget_is_a_clean_cli_error(workdir: Path) -> None:
    result = runner.invoke(
        app,
        ["outreach", "search", "users", "--product", "mongodb", "--usage-budget", "-1"],
    )

    assert result.exit_code != 0
    assert "usage-budget" in result.output
    assert "plan (" not in result.output


# Verifies: specs/user-recognition/requirements.md#2.2
def test_an_unknown_vendor_is_a_clean_error_not_a_traceback(workdir: Path) -> None:
    result = runner.invoke(
        app, ["outreach", "search", "users", "--vendor", "nope", "--product", "x"]
    )

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
