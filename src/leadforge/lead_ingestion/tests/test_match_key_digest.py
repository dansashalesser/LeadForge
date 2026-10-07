"""Keyed HMAC-SHA256 Match Key digests (task 16.12 completion, Requirement 21.4).

User decision: a Match Key that reaches a log is a keyed HMAC (HMAC-SHA256) of its
normalised value, never a plain hash (a plain hash of an email or LinkedIn URL is
reversible by dictionary attack). The secret comes from ``LEADFORGE_MATCH_KEY_SECRET``
(raw UTF-8, at least 32 bytes); absent, a random per-run key is used and the run is
marked not comparable across runs; too short fails closed without echoing it.
"""

import copy
import hashlib
import hmac
import json
import pickle
import random
import string

import pytest
import structlog

from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.match_key_digest import (
    DIGEST_HEX_CHARS,
    MATCH_KEY_SECRET_ENV,
    MIN_SECRET_BYTES,
    MatchKeyDigester,
    match_key_digester_from_environ,
)
from leadforge.lead_ingestion.match_keys import MatchKey, MatchKeyKind

SENTINEL = "sentinel-secret-Qx7v-" + "k" * 40  # >= 32 bytes, easy to scan for
OTHER = "another-secret-value-" + "z" * 40
EMAIL = MatchKey(MatchKeyKind.VERIFIED_EMAIL, "zebulon.quixote@canary-mail.example")
LINKEDIN = MatchKey(MatchKeyKind.LINKEDIN_URL, "linkedin.com/in/zebulon-canary")


def keyed(secret: str = SENTINEL) -> MatchKeyDigester:
    return match_key_digester_from_environ({MATCH_KEY_SECRET_ENV: secret})


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_digest_is_hmac_sha256_of_kind_and_value_truncated() -> None:
    expected = hmac.new(
        SENTINEL.encode("utf-8"),
        b"verified_email\x1fzebulon.quixote@canary-mail.example",
        hashlib.sha256,
    ).hexdigest()[:DIGEST_HEX_CHARS]
    assert keyed().digest(EMAIL) == expected
    assert DIGEST_HEX_CHARS == 16


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_digest_is_not_a_plain_hash_of_the_value() -> None:
    plain = {
        hashlib.sha256(EMAIL.value.encode()).hexdigest()[:DIGEST_HEX_CHARS],
        hashlib.sha256(f"verified_email\x1f{EMAIL.value}".encode()).hexdigest()[
            :DIGEST_HEX_CHARS
        ],
    }
    assert keyed().digest(EMAIL) not in plain


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_same_key_gives_the_same_digest_within_and_across_runs() -> None:
    first, second = keyed(), keyed()
    assert first.digest(EMAIL) == first.digest(EMAIL) == second.digest(EMAIL)
    assert first.comparable_across_runs
    assert second.comparable_across_runs


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_different_key_gives_a_different_digest() -> None:
    assert keyed(SENTINEL).digest(EMAIL) != keyed(OTHER).digest(EMAIL)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_kind_is_bound_into_the_digest() -> None:
    digester = keyed()
    same_text = MatchKey(MatchKeyKind.LINKEDIN_URL, EMAIL.value)
    assert digester.digest(same_text) != digester.digest(EMAIL)


# Verifies: specs/lead-source-adapters/requirements.md#21.4 (property)
@pytest.mark.parametrize("seed", range(5))
def test_digests_are_deterministic_fixed_length_hex_and_distinct(seed: int) -> None:
    rng = random.Random(seed)
    alphabet = string.printable + "\u00e9\u00df\u4e2d"
    values = {
        "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        for _ in range(300)
    }
    digester = keyed()
    digests = {}
    for value in values:
        key = MatchKey(MatchKeyKind.NAME_DOMAIN, value)
        d = digester.digest(key)
        assert d == digester.digest(key)
        assert len(d) == DIGEST_HEX_CHARS
        assert set(d) <= set("0123456789abcdef")
        digests[d] = value
    assert len(digests) == len(values)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_surrounding_whitespace_is_not_part_of_the_secret() -> None:
    assert keyed(f"  {SENTINEL}\n").digest(EMAIL) == keyed().digest(EMAIL)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
@pytest.mark.parametrize("value", [None, "", "   "])
def test_an_absent_secret_uses_a_random_per_run_key_and_warns_once(
    value: str | None,
) -> None:
    environ = {} if value is None else {MATCH_KEY_SECRET_ENV: value}
    with structlog.testing.capture_logs() as logs:
        first = match_key_digester_from_environ(environ)
    second = match_key_digester_from_environ(environ)
    assert not first.comparable_across_runs
    assert first.digest(EMAIL) == first.digest(EMAIL)
    assert first.digest(EMAIL) != second.digest(EMAIL)  # a fresh key per run
    assert len(logs) == 1
    (line,) = logs
    assert line["log_level"] == "warning"
    assert line["event"] == "match_key_secret_absent"
    assert line["comparable_across_runs"] is False
    assert set(line) <= {
        "event",
        "log_level",
        "comparable_across_runs",
        "variable",
        "min_secret_bytes",
    }


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_configured_secret_emits_no_warning() -> None:
    with structlog.testing.capture_logs() as logs:
        keyed()
    assert logs == []


# Verifies: specs/lead-source-adapters/requirements.md#21.3
@pytest.mark.parametrize(
    "short", ["Qz9", "Qz9" + "a" * (MIN_SECRET_BYTES - 4), "  Qz9" + "b" * 28 + "  "]
)
def test_a_too_short_secret_fails_closed_without_echoing_it(short: str) -> None:
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(ConfigurationError) as caught,
    ):
        match_key_digester_from_environ({MATCH_KEY_SECRET_ENV: short})
    error = caught.value
    text = " ".join([str(error), repr(error), repr(error.args), repr(vars(error))])
    assert short.strip() not in text
    assert MATCH_KEY_SECRET_ENV in text
    assert str(MIN_SECRET_BYTES) in text
    assert error.__cause__ is None
    assert error.__context__ is None
    assert logs == []


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_multibyte_secret_length_is_counted_in_utf8_bytes() -> None:
    sixteen_chars = "é" * 16  # 16 characters, 32 UTF-8 bytes
    assert keyed(sixteen_chars).comparable_across_runs
    with pytest.raises(ConfigurationError):
        keyed("é" * 15 + "x")  # 31 bytes


# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_the_key_never_appears_in_repr_str_or_pickles() -> None:
    digester = keyed()
    blobs = [repr(digester), str(digester), repr(vars(type(digester)))]
    for blob in blobs:
        assert SENTINEL not in blob
        assert SENTINEL.encode().hex() not in blob
    with pytest.raises(TypeError):
        pickle.dumps(digester)
    with pytest.raises(TypeError):
        copy.copy(digester)
    assert not hasattr(digester, "__dict__")


# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_an_ephemeral_key_never_appears_in_its_warning() -> None:
    with structlog.testing.capture_logs() as logs:
        digester = match_key_digester_from_environ({})
    blob = json.dumps(logs, default=repr) + repr(digester)
    raw = digester._key  # the test inspects the private key on purpose
    assert raw.hex() not in blob
    assert repr(raw) not in blob
