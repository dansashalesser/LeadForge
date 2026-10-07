"""Outreach settings come from config, and a bad value is a named error (14.1)."""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from leadforge.outreach.config import OutreachConfig, load_outreach_config
from leadforge.outreach.errors import OutreachConfigError

SHIPPED = Path(__file__).resolve().parents[4] / "config" / "outreach.yaml"


def _document() -> dict[str, object]:
    loaded = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "outreach.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def _with(document: dict[str, object], dotted: str, value: object) -> dict[str, object]:
    node: dict[str, object] = document
    *parents, leaf = dotted.split(".")
    for key in parents:
        child = node[key]
        assert isinstance(child, dict)
        node = child
    node[leaf] = value
    return document


# Verifies: outreach requirements 14.1
def test_the_shipped_file_loads_with_every_setting() -> None:
    config = load_outreach_config(SHIPPED)

    assert isinstance(config, OutreachConfig)
    assert config.qualify.weights.total() > 0
    assert 0 <= config.qualify.threshold <= 1
    assert "customer" in config.qualify.customer_stages
    assert config.triggers.accept_delay_days == 2
    assert config.triggers.invite_timeout.days == 5
    assert config.messages.invite_max_chars == 300
    assert config.messages.max_regenerations >= 0
    assert config.llm.timeout_s > 0
    assert config.llm.compile_retries >= 0
    assert config.llm.provider
    assert config.llm.model
    assert config.outbox_path == Path("outbox/dry_run.jsonl")
    assert config.sources.domain_key


# Verifies: outreach requirements 14.1
def test_a_changed_value_changes_the_setting_with_no_code_change(
    tmp_path: Path,
) -> None:
    path = _write(tmp_path, _with(_document(), "qualify.weights.intent", "0.4"))

    assert load_outreach_config(path).qualify.weights.intent == Decimal("0.4")


# Verifies: outreach requirements 14.1
@pytest.mark.parametrize(
    ("dotted", "value"),
    [
        ("qualify.threshold", 1.5),
        ("qualify.threshold", "high"),
        ("qualify.weights.icp_fit", -1),
        ("triggers.accept_delay_days", -2),
        ("triggers.invite_timeout_days", "soon"),
        ("messages.invite_max_chars", 0),
        ("messages.max_regenerations", -1),
        ("messages.max_hook_facts", 0),
        ("llm.timeout_s", 0),
        ("llm.compile_retries", -1),
        ("simulation.accept_rate", 2),
        ("qualify.customer_stages", [""]),
        ("sources.domain_filter", ""),
    ],
)
def test_a_bad_value_is_a_named_error_that_never_echoes_it(
    tmp_path: Path, dotted: str, value: object
) -> None:
    path = _write(tmp_path, _with(_document(), dotted, value))

    with pytest.raises(OutreachConfigError) as raised:
        load_outreach_config(path)

    assert raised.value.key_path.startswith(dotted.split(".")[0])
    assert dotted.split(".")[-1] in raised.value.key_path
    assert str(path) in str(raised.value)
    assert "high" not in str(raised.value)
    assert "soon" not in str(raised.value)


# Verifies: outreach requirements 14.1
def test_a_missing_setting_an_unknown_key_and_zero_weights_are_refused(
    tmp_path: Path,
) -> None:
    missing = _document()
    del missing["triggers"]
    unknown = _with(_document(), "llm.retries", 3)
    zero = _document()
    weights = zero["qualify"]
    assert isinstance(weights, dict)
    weights["weights"] = dict.fromkeys(weights["weights"], 0)

    for document, key in ((missing, "triggers"), (unknown, "llm.retries")):
        with pytest.raises(OutreachConfigError) as raised:
            load_outreach_config(_write(tmp_path, document))
        assert raised.value.key_path == key
    with pytest.raises(OutreachConfigError, match="sum to zero"):
        load_outreach_config(_write(tmp_path, zero))


# Verifies: outreach requirements 14.1
def test_a_missing_file_and_a_non_mapping_document_are_named_errors(
    tmp_path: Path,
) -> None:
    with pytest.raises(OutreachConfigError, match="cannot read file"):
        load_outreach_config(tmp_path / "absent.yaml")
    with pytest.raises(OutreachConfigError, match="mapping"):
        load_outreach_config(_write(tmp_path, ["a", "list"]))
    broken = tmp_path / "broken.yaml"
    broken.write_text("a: [unclosed", encoding="utf-8")
    with pytest.raises(OutreachConfigError, match="not valid YAML"):
        load_outreach_config(broken)
