"""Credential redaction for structured logs and reports (task 8.4, 10.5, 21.3).

A ``Redactor`` is seeded with the *values* of the credential manifest and is installed
as a structlog processor by ``configure_logging``, so an accidental interpolation of a
credential is scrubbed before any renderer sees it. ``redact(text)`` is the same
scrubber for reports (task 18.3).

Manifest and seeding (``credential_manifest``, ``secrets_from_environ``): the manifest
is ``env_example.BUILTIN_SETTINGS`` plus every registered adapter's ``required_env``.
Values come from an injected environ, never the process environment. Two built-in
settings are not credentials: ``LLM_PROVIDER`` / ``LLM_MODEL`` are skipped (seeding
``openai`` would corrupt every log line that names the provider), and ``DATABASE_URL``
seeds only its password, so the path stays readable in logs.

Provisional decisions (see choices.md, task 8.4):

* Matching is by exact text, case-sensitive, per string. A secret split across two log
  fields, or hashed or encrypted, is not detected (accepted false negative).
* Length: stripped values under ``MIN_TOKEN_LEN`` are ignored (false positives, e.g. a
  value of ``1``, would mask ordinary words; a real key is never that short). From
  ``MIN_TOKEN_LEN`` to ``MIN_SUBSTRING_LEN - 1`` characters a value matches only as a
  whole token (not inside an identifier). From ``MIN_SUBSTRING_LEN`` it matches
  anywhere, also inside another word, because a leak glued to text is still a leak.
  Blank values never match, so an empty secret cannot mask every string.
* Encodings also scrubbed: the stripped and the unstripped value, percent-encoding
  (``quote`` and ``quote_plus``), JSON string escaping, and standard and URL-safe
  base64 with and without padding. Not covered: base64 of a longer string that merely
  contains the value (HTTP Basic ``user:key``), hex, other transformations.
* Overlapping secrets: every match start is found and overlapping spans merge into one
  ``***``, so no fragment of either survives.
* The processor never mutates the caller's event dict; it returns a new one.
* Raw-payload guard: a value under a key named like a provider payload (``RAW_KEYS`` or
  ending in ``_payload``, ``_body``, ``_response``) is replaced by a size marker, at any
  nesting depth; so is any container whose strings total over ``MAX_VALUE_CHARS``, and
  a longer string is truncated, after redaction so no secret fragment survives the cut.
  Scalars (numbers, booleans, ``None``) under such keys are kept.
* ``exc_info`` still present is rendered to an ``exception`` string and scrubbed; place
  the processor after ``format_exc_info`` (as ``configure_logging`` does) for the usual
  case. Traceback text gets a ten-times larger bound.
* ``capture_logs`` replaces the whole configured chain with its capture processor for
  the duration of the context, so captured entries are *pre-redaction*. Tests that must
  see redacted entries use ``capture_logs(processors=[active_redactor()])``. The chain
  is restored on exit, so ``configure_logging`` and ``capture_logs`` do not interfere.
* Logs go to stderr as JSON, so they never mix with report output on stdout.
"""

from __future__ import annotations

import base64
import re
import sys
import traceback
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote, quote_plus

import structlog
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from leadforge.lead_ingestion.database import DATABASE_URL_ENV
from leadforge.lead_ingestion.env_example import BUILTIN_SETTINGS
from leadforge.lead_ingestion.registry import SourceRegistry

__all__ = [
    "MASK",
    "MAX_VALUE_CHARS",
    "MIN_SUBSTRING_LEN",
    "MIN_TOKEN_LEN",
    "NON_SECRET_SETTINGS",
    "RAW_KEYS",
    "Redactor",
    "active_redactor",
    "configure_logging",
    "credential_manifest",
    "redact",
    "secrets_from_environ",
]

MASK = "***"
MIN_TOKEN_LEN = 4
MIN_SUBSTRING_LEN = 8
MAX_VALUE_CHARS = 2000
_LONG_KEYS = frozenset({"exception", "stack"})
_LONG_FACTOR = 10
_MAX_DEPTH = 32
NON_SECRET_SETTINGS = frozenset({"LLM_PROVIDER", "LLM_MODEL"})
RAW_KEYS = frozenset(
    {
        "raw",
        "payload",
        "body",
        "response",
        "raw_payload",
        "raw_response",
        "raw_body",
        "raw_json",
        "raw_data",
        "response_body",
        "response_json",
        "request_body",
        "json_body",
        "provider_response",
    }
)
_RAW_SUFFIXES = ("_payload", "_body", "_response")
_WORD = "A-Za-z0-9_"


def _is_raw_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    name = key.lower().replace("-", "_")
    return name in RAW_KEYS or name.endswith(_RAW_SUFFIXES)


def _encodings(value: str) -> set[str]:
    raw = value.encode("utf-8")
    out = {
        value,
        quote(value, safe=""),
        quote_plus(value),
        value.replace("\\", "\\\\").replace('"', '\\"'),
    }
    for encoder in (base64.b64encode, base64.urlsafe_b64encode):
        text = encoder(raw).decode("ascii")
        out |= {text, text.rstrip("=")}
    return out


def _trie_pattern(words: Iterable[str]) -> str:
    """Regex matching any of ``words``, sharing common prefixes between branches.

    A flat alternation costs O(words) per text position; the trie keeps matching cheap
    with thousands of secrets.
    """
    end = object()
    root: dict[Any, Any] = {}
    for word in words:
        node = root
        for ch in word:
            node = node.setdefault(ch, {})
        node[end] = True

    def emit(node: dict[Any, Any]) -> str:
        prefix = ""
        # Walk single-child chains iteratively: secrets can be thousands of chars.
        while len(node) == 1 and end not in node:
            ((ch, node),) = node.items()
            prefix += re.escape(ch)
        children = sorted(
            ((ch, c) for ch, c in node.items() if ch is not end), key=lambda p: p[0]
        )
        branches = [re.escape(ch) + emit(child) for ch, child in children]
        if not branches:
            return prefix
        body = branches[0] if len(branches) == 1 else "(?:" + "|".join(branches) + ")"
        return prefix + (f"(?:{body})?" if end in node else body)

    return emit(root)


class Redactor:
    """Scrubs the given secret values; also usable as a structlog processor."""

    def __init__(self, secrets: Iterable[str]) -> None:
        substrings: set[str] = set()
        tokens: set[str] = set()
        for secret in secrets:
            for form in {secret, secret.strip()}:
                if len(form) < MIN_TOKEN_LEN or not form.strip():
                    continue
                for variant in _encodings(form):
                    if len(variant) >= MIN_SUBSTRING_LEN:
                        substrings.add(variant)
                    elif len(variant) >= MIN_TOKEN_LEN:
                        tokens.add(variant)
        tokens -= substrings
        alts = []
        if substrings:
            alts.append(f"({_trie_pattern(substrings)})")
        if tokens:
            alts.append(f"(?<![{_WORD}])({_trie_pattern(tokens)})(?![{_WORD}])")
        self._pattern = (
            re.compile("(?=" + "|".join(alts) + ")", re.DOTALL) if alts else None
        )
        self._count = len(substrings) + len(tokens)

    def __len__(self) -> int:
        return self._count

    def redact(self, text: str) -> str:
        """``text`` with every seeded secret (and its encodings) replaced by ``***``."""
        if self._pattern is None or not text:
            return text
        spans: list[list[int]] = []
        for match in self._pattern.finditer(text):
            start, end = match.span(1) if match.group(1) is not None else match.span(2)
            if spans and start <= spans[-1][1]:
                spans[-1][1] = max(spans[-1][1], end)
            else:
                spans.append([start, end])
        if not spans:
            return text
        out: list[str] = []
        last = 0
        for start, end in spans:
            out += [text[last:start], MASK]
            last = end
        out.append(text[last:])
        return "".join(out)

    # -- recursive scrubbing -----------------------------------------------------

    def redact_value(self, value: Any) -> Any:
        """Recursively scrub strings, keys, containers and exception text."""
        return self._clean(value, 0, set(), guard=False)

    def _clean(self, obj: Any, depth: int, path: set[int], *, guard: bool) -> Any:
        if obj is None or isinstance(obj, bool):
            return obj
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, bytes | bytearray):
            text = bytes(obj).decode("utf-8", errors="replace")
            scrubbed = self.redact(text)
            return obj if scrubbed == text else scrubbed
        if isinstance(obj, Mapping | list | tuple | set | frozenset):
            if depth >= _MAX_DEPTH:
                return "<omitted: nested too deeply>"
            if id(obj) in path:
                return "<omitted: cycle>"
            path.add(id(obj))
            try:
                return self._clean_container(obj, depth, path, guard=guard)
            finally:
                path.discard(id(obj))
        return self._clean_object(obj)

    def _clean_container(
        self, obj: Any, depth: int, path: set[int], *, guard: bool
    ) -> Any:
        if isinstance(obj, Mapping):
            result: dict[Any, Any] = {}
            for key, item in obj.items():
                clean_key = self._clean(key, depth + 1, path, guard=False)
                if not isinstance(clean_key, str | int | float | bool | type(None)):
                    clean_key = str(clean_key)
                if guard and _is_raw_key(key) and _is_payload_like(item):
                    result[clean_key] = _marker(item)
                else:
                    result[clean_key] = self._clean(item, depth + 1, path, guard=guard)
            return result
        items = [self._clean(i, depth + 1, path, guard=guard) for i in obj]
        if isinstance(obj, list):
            return items
        if isinstance(obj, tuple):
            return tuple(items)
        return sorted(items, key=repr)

    def _clean_object(self, obj: Any) -> Any:
        try:
            text = repr(obj) if isinstance(obj, BaseException) else str(obj)
        except Exception:  # noqa: BLE001 - a hostile __str__ must not break logging
            return "<unprintable object>"
        scrubbed = self.redact(text)
        return obj if scrubbed == text else scrubbed

    # -- structlog processor -------------------------------------------------------

    def __call__(
        self, logger: Any, method_name: str, event_dict: Mapping[str, Any]
    ) -> dict[str, Any]:
        event = dict(event_dict)
        if event.get("exc_info"):
            rendered = _render_exc_info(event.pop("exc_info"))
            if rendered:
                previous = event.get("exception")
                event["exception"] = (
                    f"{previous}\n{rendered}" if isinstance(previous, str) else rendered
                )
        else:
            event.pop("exc_info", None)
        out: dict[str, Any] = {}
        for key, value in event.items():
            clean_key = self._clean(key, 1, set(), guard=False)
            if _is_raw_key(key) and _is_payload_like(value):
                out[clean_key] = _marker(value)
                continue
            clean = self._clean(value, 1, set(), guard=True)
            bound = MAX_VALUE_CHARS * (_LONG_FACTOR if key in _LONG_KEYS else 1)
            out[clean_key] = _bound(clean, bound)
        return out


def _render_exc_info(info: Any) -> str:
    if info is True:
        info = sys.exc_info()
    if isinstance(info, BaseException):
        info = (type(info), info, info.__traceback__)
    if not isinstance(info, tuple) or len(info) != 3 or info[0] is None:
        return ""
    return "".join(traceback.format_exception(*info)).rstrip("\n")


def _is_payload_like(value: Any) -> bool:
    return not (value is None or isinstance(value, bool | int | float))


def _size(value: Any) -> str:
    if isinstance(value, str | bytes | bytearray):
        return f"{len(value)} chars"
    try:
        return f"{len(value)} items"
    except TypeError:
        return "unknown size"


def _marker(value: Any) -> str:
    return f"<omitted raw payload: {type(value).__name__} of {_size(value)}>"


def _weight(value: Any, cap: int) -> int:
    """Approximate characters in ``value``, stopping once past ``cap``."""
    total = 0
    stack = [value]
    while stack and total <= cap:
        item = stack.pop()
        if isinstance(item, str):
            total += len(item)
        elif isinstance(item, Mapping):
            total += len(item)
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list | tuple):
            total += len(item)
            stack.extend(item)
        else:
            total += 1
    return total


def _bound(value: Any, bound: int) -> Any:
    if isinstance(value, str):
        if len(value) <= bound:
            return value
        return f"{value[:bound]}...<truncated {len(value) - bound} chars>"
    if isinstance(value, Mapping | list | tuple) and _weight(value, bound) > bound:
        return f"<omitted oversized {type(value).__name__}: over {bound} chars>"
    return value


# -- manifest ----------------------------------------------------------------------


def credential_manifest(registry: SourceRegistry) -> tuple[str, ...]:
    """Every variable live mode reads: built-in settings, then adapter variables."""
    names = [variable for variable, *_ in BUILTIN_SETTINGS]
    extra: set[str] = set()
    for name in registry.names():
        extra.update(registry.source_class(name).required_env)
    names += sorted(extra - set(names))
    return tuple(names)


def secrets_from_environ(
    environ: Mapping[str, str], names: Iterable[str]
) -> tuple[str, ...]:
    """Secret values of ``names`` in ``environ``, minus blank and non-secret ones."""
    found: dict[str, None] = {}
    for name in names:
        value = environ.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        if name in NON_SECRET_SETTINGS:
            continue
        if name == DATABASE_URL_ENV:
            try:
                password = make_url(value).password
            except ArgumentError:
                password = None
            if password:
                found[password] = None
            continue
        found[value] = None
    return tuple(found)


# -- configuration -----------------------------------------------------------------

_active = Redactor(())


def _set_active(redactor: Redactor) -> None:
    global _active
    _active = redactor


def active_redactor() -> Redactor:
    """The redactor installed by the last ``configure_logging`` (empty before that)."""
    return _active


def redact(text: str) -> str:
    """Scrub ``text`` with the active redactor; the entry point for run reports."""
    return _active.redact(text)


def configure_logging(
    environ: Mapping[str, str],
    registry: SourceRegistry | None = None,
    *,
    renderer: Any = None,
    extra_secrets: Iterable[str] = (),
) -> Redactor:
    """Install the redaction chain into structlog, seeded from ``environ``.

    Replaces any previous configuration and active redactor (secrets of an earlier call
    are forgotten). Loggers are not cached, so modules that created a logger at import
    time pick the new chain up.
    """
    manifest = credential_manifest(registry or SourceRegistry.discover())
    redactor = Redactor([*secrets_from_environ(environ, manifest), *extra_secrets])
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redactor,
            renderer or structlog.processors.JSONRenderer(),
        ],
        logger_factory=lambda *_: structlog.PrintLogger(sys.stderr),
        cache_logger_on_first_use=False,
    )
    _set_active(redactor)
    return redactor
