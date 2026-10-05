"""Run outcome to exit code (task 11.3, Requirements 6.4 and 6.5).

Built from ``SourceResult`` records directly; the mapping is pure and never sees an
adapter. Disabled sources are absent from the results, so they never count.
"""

import dataclasses

import pytest

from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import (
    SourceOutcome,
    SourceResult,
    SourceStatus,
)
from leadforge.lead_ingestion.run_exit import RunExit, map_run_exit


def _result(
    name: str,
    status: SourceStatus,
    *,
    attempted: int = 1,
    succeeded: int = 0,
    failed: int = 0,
    skipped: int = 0,
    error: str | None = None,
) -> SourceResult:
    outcome = SourceOutcome(
        name, status, attempted, succeeded, failed, skipped, 0, error
    )
    return SourceResult(name, DataMode.LIVE, "test", None, None, outcome)


def _ok(name: str, attempted: int = 1) -> SourceResult:
    return _result(name, SourceStatus.OK, attempted=attempted, succeeded=1)


def _bad(name: str, status: SourceStatus, error: str | None = None) -> SourceResult:
    return _result(name, status, failed=1, error=error)


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_all_failed_exits_nonzero_naming_each_source_and_class() -> None:
    run = map_run_exit(
        (
            _bad("alpha", SourceStatus.UNAUTHORIZED),
            _bad("beta", SourceStatus.RATE_LIMITED),
            _bad("gamma", SourceStatus.NORMALIZATION_FAILED),
        )
    )

    assert isinstance(run, RunExit)
    assert run.exit_code != 0
    assert "alpha: unauthorized" in run.summary
    assert "beta: rate_limited" in run.summary
    assert "gamma: normalization_failed" in run.summary


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_summary_carries_names_and_classes_not_error_text() -> None:
    secret = "jane.doe@example.com"
    run = map_run_exit(
        (_bad("alpha", SourceStatus.COMPLIANCE_RESTRICTED, error=f"subject={secret}"),)
    )

    assert secret not in run.summary
    assert "alpha: compliance_restricted" in run.summary


# Verifies: specs/lead-source-adapters/requirements.md#6.5
def test_partial_success_exits_zero_with_per_source_counts() -> None:
    run = map_run_exit(
        (
            _result("alpha", SourceStatus.OK, attempted=3, succeeded=1, failed=0),
            _result("beta", SourceStatus.TRANSIENT, attempted=3, failed=1),
        )
    )

    assert run.exit_code == 0
    assert "alpha: attempted=3 succeeded=1 failed=0" in run.summary
    assert "beta: transient attempted=3 succeeded=0 failed=1" in run.summary


# Verifies: specs/lead-source-adapters/requirements.md#6.5
def test_all_succeeded_exits_zero() -> None:
    run = map_run_exit((_ok("alpha"), _ok("beta")))

    assert run.exit_code == 0
    assert "alpha: attempted=1 succeeded=1 failed=0" in run.summary


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_halted_source_with_no_success_counts_as_failed() -> None:
    halted = _result(
        "alpha", SourceStatus.UNAUTHORIZED, failed=1, skipped=2, attempted=1
    )

    run = map_run_exit((halted,))

    assert run.exit_code != 0
    assert "alpha: unauthorized" in run.summary


# Verifies: specs/lead-source-adapters/requirements.md#6.5
def test_source_with_a_success_then_a_failure_still_counts_as_succeeded() -> None:
    mixed = _result("alpha", SourceStatus.TRANSIENT, attempted=2, succeeded=1, failed=1)

    assert map_run_exit((mixed,)).exit_code == 0


# Verifies: specs/lead-source-adapters/requirements.md#6.5
def test_no_enabled_sources_exits_nonzero_because_none_succeeded() -> None:
    run = map_run_exit(())

    assert run.exit_code != 0
    assert "no enabled sources" in run.summary


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_every_source_status_has_a_recorded_decision() -> None:
    # A new SourceStatus must be added here deliberately; success is by call count.
    decided = {s: s is SourceStatus.OK for s in SourceStatus}
    assert set(decided) == set(SourceStatus)
    assert len(SourceStatus) == 9
    for status in SourceStatus:
        failed = map_run_exit((_result("a", status, failed=1),))
        assert failed.exit_code == 1
        assert status is SourceStatus.OK or f"a: {status.value} " in failed.summary
        succeeded = map_run_exit((_result("a", status, succeeded=1),))
        assert succeeded.exit_code == 0


# Verifies: specs/lead-source-adapters/requirements.md#6.4
def test_ok_status_without_a_successful_call_is_not_named_ok() -> None:
    run = map_run_exit((_result("alpha", SourceStatus.OK, attempted=0),))

    assert run.exit_code == 1
    assert "alpha: no_successful_calls" in run.summary
    assert "alpha: ok" not in run.summary


def test_exit_code_is_a_plain_int_and_result_is_immutable() -> None:
    run = map_run_exit((_ok("a"),))

    assert type(run.exit_code) is int
    with pytest.raises(dataclasses.FrozenInstanceError):
        run.exit_code = 5  # type: ignore[misc]


def test_summary_lists_sources_in_input_order_one_line_each() -> None:
    run = map_run_exit(
        (_bad("zed", SourceStatus.FAILED), _bad("abe", SourceStatus.FAILED))
    )

    lines = run.summary.split("\n")
    assert lines[0] == "all enabled sources failed"
    assert [x.split(":")[0] for x in lines[1:]] == ["zed", "abe"]
    assert not run.summary.endswith("\n")


def test_control_characters_in_a_source_name_cannot_forge_summary_lines() -> None:
    run = map_run_exit((_bad("evil\nfake: ok\x1b[31m", SourceStatus.FAILED),))

    assert len(run.summary.split("\n")) == 2
    assert "\x1b" not in run.summary
