"""Task 8.2: load the environment file without overriding the process (Req 10.6)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from leadforge.lead_ingestion.env_file import (
    DEFAULT_ENV_FILE,
    ENV_FILE_VARIABLE,
    EnvFileError,
    load_env_file,
    load_env_file_into_process,
    parse_env_text,
    resolve_env_file_path,
)

SECRET = "sk-planted-SECRET-9f8e7d"


def _write(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_file_values_are_loaded_into_the_injected_mapping(tmp_path: Path) -> None:
    f = _write(tmp_path / ".env", "A=1\nB=two\n")
    env: dict[str, str] = {}
    applied = load_env_file(env, f)
    assert env == {"A": "1", "B": "two"}
    assert applied == frozenset({"A", "B"})


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_process_environment_wins_over_the_file(tmp_path: Path) -> None:
    f = _write(tmp_path / ".env", "A=from-file\nB=from-file\n")
    env = {"A": "from-process"}
    applied = load_env_file(env, f)
    assert env == {"A": "from-process", "B": "from-file"}
    assert applied == frozenset({"B"})


# Verifies: specs/lead-source-adapters/requirements.md#10.6
@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_process_value_counts_as_unset(tmp_path: Path, blank: str) -> None:
    f = _write(tmp_path / ".env", "A=from-file\n")
    env = {"A": blank}
    load_env_file(env, f)
    assert env == {"A": "from-file"}


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_absent_file_is_not_an_error(tmp_path: Path) -> None:
    env = {"A": "1"}
    assert load_env_file(env, tmp_path / "missing.env") == frozenset()
    assert env == {"A": "1"}


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_broken_symlink_counts_as_absent(tmp_path: Path) -> None:
    link = tmp_path / ".env"
    link.symlink_to(tmp_path / "nowhere")
    assert load_env_file({}, link) == frozenset()


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_symlink_to_a_file_is_followed(tmp_path: Path) -> None:
    real = _write(tmp_path / "real.env", "A=1\n")
    link = tmp_path / ".env"
    link.symlink_to(real)
    env: dict[str, str] = {}
    load_env_file(env, link)
    assert env == {"A": "1"}


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_directory_in_place_of_the_file_fails_loudly(tmp_path: Path) -> None:
    d = tmp_path / ".env"
    d.mkdir()
    with pytest.raises(EnvFileError, match="not a file"):
        load_env_file({}, d)


# Verifies: specs/lead-source-adapters/requirements.md#10.6
@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_file_fails_loudly_without_content(tmp_path: Path) -> None:
    f = _write(tmp_path / ".env", f"A={SECRET}\n")
    f.chmod(0)
    try:
        with pytest.raises(EnvFileError) as info:
            load_env_file({}, f)
    finally:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert SECRET not in str(info.value)


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_parse_rules() -> None:
    text = (
        "﻿# a comment\r\n"
        "\r\n"
        "export A=1\r\n"
        "B = 'x=y'  # trailing\r\n"
        'C="has # hash"\r\n'
        "D=a=b=c\r\n"
        "E=\r\n"
        "  F=spaced\n"
    )
    assert parse_env_text(text) == {
        "A": "1",
        "B": "x=y",
        "C": "has # hash",
        "D": "a=b=c",
        "E": "",
        "F": "spaced",
    }


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_duplicate_key_in_file_last_wins() -> None:
    assert parse_env_text("A=1\nA=2\n") == {"A": "2"}


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_bom_in_a_real_file_is_stripped(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_bytes(b"\xef\xbb\xbfA=1\r\n")
    env: dict[str, str] = {}
    load_env_file(env, f)
    assert env == {"A": "1"}


# Verifies: specs/lead-source-adapters/requirements.md#10.6
@pytest.mark.parametrize(
    ("text", "line"),
    [
        ("this is not an assignment\n", 1),
        ("A=1\n\n\nnot valid\n", 4),
        ("A=1\r\n# c\r\n\r\n=novalue\r\n", 4),
        ("A=1\nJUSTAKEY\n", 2),
    ],
)
def test_malformed_line_names_the_line_number_only(text: str, line: int) -> None:
    secretive = text.replace("not valid", f"not valid {SECRET}")
    with pytest.raises(EnvFileError) as info:
        parse_env_text(secretive)
    message = str(info.value)
    assert f"line {line}" in message
    assert SECRET not in message
    assert SECRET not in repr(info.value)
    assert "not valid" not in message


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_malformed_file_applies_nothing(tmp_path: Path) -> None:
    f = _write(tmp_path / ".env", f"A=1\nbad {SECRET}\n")
    env: dict[str, str] = {}
    with pytest.raises(EnvFileError, match="line 2"):
        load_env_file(env, f)
    assert env == {}


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_error_names_the_file_but_not_the_content(tmp_path: Path) -> None:
    f = _write(tmp_path / "my.env", f"bad {SECRET}\n")
    with pytest.raises(EnvFileError) as info:
        load_env_file({}, f)
    assert "my.env" in str(info.value)
    assert SECRET not in str(info.value)
    assert info.value.__cause__ is None


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_invalid_utf8_fails_loudly_without_bytes(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_bytes(b"A=ok\nB=\xff\xfe" + SECRET.encode() + b"\n")
    with pytest.raises(EnvFileError, match="UTF-8") as info:
        load_env_file({}, f)
    assert SECRET not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_values_never_appear_in_the_applied_report_or_repr(tmp_path: Path) -> None:
    f = _write(tmp_path / ".env", f"A={SECRET}\n")
    applied = load_env_file({}, f)
    assert SECRET not in repr(applied)


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_default_path_is_dot_env_in_the_base_dir(tmp_path: Path) -> None:
    assert DEFAULT_ENV_FILE == ".env"
    assert resolve_env_file_path({}, base_dir=tmp_path) == tmp_path / ".env"


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_default_path_without_base_dir_is_cwd_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert resolve_env_file_path({}) == tmp_path / ".env"


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_path_is_overridden_by_variable_then_by_argument(tmp_path: Path) -> None:
    via_env = resolve_env_file_path(
        {ENV_FILE_VARIABLE: "conf/prod.env"}, base_dir=tmp_path
    )
    assert via_env == tmp_path / "conf" / "prod.env"
    explicit = resolve_env_file_path(
        {ENV_FILE_VARIABLE: "conf/prod.env"},
        tmp_path / "other.env",
        base_dir=tmp_path,
    )
    assert explicit == tmp_path / "other.env"


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_blank_path_variable_is_unset(tmp_path: Path) -> None:
    assert resolve_env_file_path({ENV_FILE_VARIABLE: "  "}, base_dir=tmp_path) == (
        tmp_path / ".env"
    )


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_path_variable_in_the_file_is_not_honoured(tmp_path: Path) -> None:
    other = _write(tmp_path / "other.env", "A=other\n")
    f = _write(tmp_path / ".env", f"{ENV_FILE_VARIABLE}={other}\nA=main\n")
    env: dict[str, str] = {}
    load_env_file(env, f)
    assert env["A"] == "main"


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_loader_finds_the_default_file_via_the_injected_mapping(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "custom.env", "A=1\n")
    env = {ENV_FILE_VARIABLE: "custom.env"}
    load_env_file(env, base_dir=tmp_path)
    assert env["A"] == "1"


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_injected_loading_never_touches_the_real_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LF_8_2_PROBE", raising=False)
    f = _write(tmp_path / ".env", "LF_8_2_PROBE=1\n")
    load_env_file({}, f)
    assert "LF_8_2_PROBE" not in os.environ


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_process_entry_point_mutates_os_environ_and_respects_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LF_8_2_NEW", "x")
    monkeypatch.delenv("LF_8_2_NEW")  # teardown restores to unset
    monkeypatch.setenv("LF_8_2_SET", "process")
    f = _write(tmp_path / ".env", "LF_8_2_NEW=file\nLF_8_2_SET=file\n")
    applied = load_env_file_into_process(f)
    assert os.environ["LF_8_2_NEW"] == "file"
    assert os.environ["LF_8_2_SET"] == "process"
    assert applied == frozenset({"LF_8_2_NEW"})


# Verifies: specs/lead-source-adapters/requirements.md#10.6
def test_permission_denied_is_wrapped_even_when_running_as_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = _write(tmp_path / ".env", f"A={SECRET}\n")

    def deny(self: Path) -> bytes:
        raise PermissionError(13, f"denied {SECRET}")

    monkeypatch.setattr(Path, "read_bytes", deny)
    with pytest.raises(EnvFileError, match="PermissionError") as info:
        load_env_file({}, f)
    assert SECRET not in str(info.value)
    assert info.value.__cause__ is None
