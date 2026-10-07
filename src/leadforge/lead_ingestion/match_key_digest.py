"""Keyed Match Key digests for logs (task 16.12 completion; Requirements 21.3, 21.4).

User decision (2026-10-06): a Match Key that reaches a log, the merge log or a report
is a keyed HMAC-SHA256 of its normalised value, never the value and never a plain hash
(a plain sha256 of an email or LinkedIn URL is reversible by dictionary attack).
Provisional decisions (choices.md, 16.12 completion):

* Input is ``<kind name>\\x1f<normalised value>`` in UTF-8, so the same text under two
  kinds gives two digests (domain separation); the value is the one ``match_keys``
  normalised, so a digest joins every line about one key.
* Output is the first ``DIGEST_HEX_CHARS`` (16, 64 bits) hex characters, the length
  the project already uses for its one truncated digest (``projection``'s company id).
  Enough to join log lines; the key, not the length, is what stops a dictionary.
* The secret is ``LEADFORGE_MATCH_KEY_SECRET``, read from the environment the run
  already loaded (``.env`` included), decoded as raw UTF-8 of the stripped value, at
  least ``MIN_SECRET_BYTES`` (32) bytes. Rejected: hex/base64 decoding (one more way
  to misconfigure; ``openssl rand -hex 32`` gives 64 usable bytes as text anyway).
* Absent or blank: a per-run random key (``secrets.token_bytes(32)``) and ONE warning
  naming the variable and the minimum length, no key material and no personal data.
  ``comparable_across_runs`` is then ``False``; the run record states it and the run
  report prints it, because digests of two such runs never match.
* Too short: ``ConfigurationError`` naming the variable and the minimum, never the
  value or its length, raised with no chained exception (fails closed: no run).
* The key lives in a slot, is never in ``repr``, and the digester refuses pickling
  and copying, so it cannot travel into a pickle, a log or an error by accident. The
  variable is a built-in setting (``env_example``), so ``log_redaction`` also scrubs
  its value from every log line.
"""

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from typing import NoReturn

import structlog

from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.match_keys import MatchKey

__all__ = [
    "DIGEST_HEX_CHARS",
    "MATCH_KEY_SECRET_ENV",
    "MIN_SECRET_BYTES",
    "MatchKeyDigester",
    "match_key_digester_from_environ",
]

MATCH_KEY_SECRET_ENV = "LEADFORGE_MATCH_KEY_SECRET"
MIN_SECRET_BYTES = 32
DIGEST_HEX_CHARS = 16
_SEPARATOR = "\x1f"

_log = structlog.get_logger(__name__)


class MatchKeyDigester:
    """HMAC-SHA256 of a Match Key under one secret; the secret is never shown."""

    __slots__ = ("_comparable", "_key")

    def __init__(self, key: bytes, *, comparable_across_runs: bool) -> None:
        if len(key) < MIN_SECRET_BYTES:
            raise ValueError(f"a match key secret needs {MIN_SECRET_BYTES} bytes")
        self._key = bytes(key)
        self._comparable = comparable_across_runs

    @property
    def comparable_across_runs(self) -> bool:
        """``False`` for a per-run random key: its digests match no other run's."""
        return self._comparable

    def digest(self, key: MatchKey) -> str:
        message = f"{key.kind.name.lower()}{_SEPARATOR}{key.value}".encode()
        mac = hmac.new(self._key, message, hashlib.sha256).hexdigest()
        return mac[:DIGEST_HEX_CHARS]

    def __repr__(self) -> str:
        return f"MatchKeyDigester(comparable_across_runs={self._comparable})"

    def __reduce__(self) -> NoReturn:
        raise TypeError("a MatchKeyDigester holds a secret and cannot be serialised")

    def __copy__(self) -> NoReturn:
        raise TypeError("a MatchKeyDigester holds a secret and cannot be copied")

    def __deepcopy__(self, memo: object) -> NoReturn:
        raise TypeError("a MatchKeyDigester holds a secret and cannot be copied")


def match_key_digester_from_environ(environ: Mapping[str, str]) -> MatchKeyDigester:
    """The run's digester: keyed from ``environ``, else a random per-run key."""
    text = environ.get(MATCH_KEY_SECRET_ENV)
    secret = text.strip().encode() if isinstance(text, str) else b""
    if not secret:
        _log.warning(
            "match_key_secret_absent",
            variable=MATCH_KEY_SECRET_ENV,
            min_secret_bytes=MIN_SECRET_BYTES,
            comparable_across_runs=False,
        )
        return MatchKeyDigester(
            secrets.token_bytes(MIN_SECRET_BYTES), comparable_across_runs=False
        )
    if len(secret) < MIN_SECRET_BYTES:
        raise ConfigurationError(
            "environment",
            key_path=MATCH_KEY_SECRET_ENV,
            detail=f"must be at least {MIN_SECRET_BYTES} bytes of UTF-8 text",
        )
    return MatchKeyDigester(secret, comparable_across_runs=True)
