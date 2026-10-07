"""CLI entrypoint of `leadforge` (task 1.1).

The stub's placeholder-outcome test was removed in task 20, when `ingest` started
running the real ingestion: its behaviour is pinned in
test_end_to_end_zero_credential.py.
"""

from importlib.metadata import entry_points

from typer.testing import CliRunner

from leadforge.lead_ingestion.cli import app


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_ingest_is_a_named_subcommand_not_the_app_root() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "ingest" in result.output


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_leadforge_console_script_points_at_slice_cli() -> None:
    scripts = entry_points(group="console_scripts", name="leadforge")

    assert [ep.value for ep in scripts] == ["leadforge.cli:app"]
