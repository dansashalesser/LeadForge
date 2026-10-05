"""CLI entrypoint stub for `leadforge ingest` (task 1.1)."""

from importlib.metadata import entry_points

from typer.testing import CliRunner

from leadforge.lead_ingestion.cli import PLACEHOLDER_MESSAGE, app


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_ingest_command_exits_zero_with_placeholder() -> None:
    result = CliRunner().invoke(app, ["ingest"])

    assert result.exit_code == 0, result.output
    assert PLACEHOLDER_MESSAGE in result.output


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_ingest_is_a_named_subcommand_not_the_app_root() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "ingest" in result.output


# Verifies: specs/lead-source-adapters/requirements.md#1.1
def test_leadforge_console_script_points_at_slice_cli() -> None:
    scripts = entry_points(group="console_scripts", name="leadforge")

    assert [ep.value for ep in scripts] == ["leadforge.lead_ingestion.cli:app"]
