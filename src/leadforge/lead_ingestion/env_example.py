"""Credential example file generated from the registry (task 8.3, requirement 10).

``.env.example`` is the union of every registered adapter's ``required_env`` and
``optional_env`` (non-secret plan settings), each with the adapter's ``env_notes`` line
when it declares one, plus the database URL and the LLM provider, model and key
settings, so a reviewer sees every variable live mode reads and no real value. The
committed file is checked byte for byte against ``render_env_example`` (10.4), so an
adapter reading an undocumented variable fails the suite; regenerate with
``REGENERATE_COMMAND``.

Provisional decisions (see choices.md, task 8.3):

* The three settings requirement 10.1 names are built in, plus
  ``LEADFORGE_MATCH_KEY_SECRET`` (task 16.12 completion: the HMAC-SHA256 key for Match
  Key digests in the merge log), so it is documented and, being a built-in setting,
  redacted by ``log_redaction`` like any credential. ``RAW_RETENTION_DAYS``,
  ``LEADFORGE_MODE`` and ``LEADFORGE_ENV_FILE`` are tuning knobs with defaults, not
  credentials, and are left out; add them to ``BUILTIN_SETTINGS`` to document them.
* The documentation URL is the adapter's ``docs_url``, else its first rate bucket's
  ``doc_url`` (by bucket name); neither makes generation fail.
* Every value is empty. The generator never reads the process environment, so no
  secret can reach the file.
* Names, source names and URLs that could break out of a comment line are rejected.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from leadforge.lead_ingestion.database import DATABASE_URL_ENV
from leadforge.lead_ingestion.match_key_digest import (
    MATCH_KEY_SECRET_ENV,
    MIN_SECRET_BYTES,
)
from leadforge.lead_ingestion.registry import SourceRegistry

__all__ = [
    "BUILTIN_SETTINGS",
    "DEFAULT_PATH",
    "REGENERATE_COMMAND",
    "RETIRED_VARIABLES",
    "ManifestError",
    "main",
    "render_env_example",
    "write_env_example",
]

DEFAULT_PATH = Path(".env.example")
REGENERATE_COMMAND = "uv run python -m leadforge.lead_ingestion.env_example"

# Design correction N3: no code path may call the Custom Search JSON API.
RETIRED_VARIABLES = frozenset({"GOOGLE_CSE_API_KEY", "GOOGLE_CSE_CX"})

# (variable, owner label, documentation URL, what it is); output order is this order.
BUILTIN_SETTINGS: tuple[tuple[str, str, str, str], ...] = (
    (
        DATABASE_URL_ENV,
        "database",
        "https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls",
        "SQLAlchemy URL; leave empty for the local default database file",
    ),
    (
        "LEADFORGE_LLM_PROVIDER",
        "llm",
        "https://python.langchain.com/docs/how_to/chat_models_universal_init/",
        "chat model provider passed to LangChain init_chat_model; empty means the "
        "llm.provider of config/outreach.yaml",
    ),
    (
        "LEADFORGE_LLM_MODEL",
        "llm",
        "https://python.langchain.com/docs/how_to/chat_models_universal_init/",
        "chat model name passed to LangChain init_chat_model; empty means the "
        "llm.model of config/outreach.yaml",
    ),
    (
        "ANTHROPIC_API_KEY",
        "llm",
        "https://docs.anthropic.com/en/api/getting-started",
        "key for the default anthropic provider (another provider reads "
        "<PROVIDER>_API_KEY); unset means offline templates and no live users search",
    ),
    (
        MATCH_KEY_SECRET_ENV,
        "merge log",
        "https://docs.python.org/3/library/hmac.html",
        "secret key for the HMAC-SHA256 Match Key digests in merge logs; at least "
        f"{MIN_SECRET_BYTES} bytes of UTF-8 text (e.g. the output of "
        "openssl rand -hex 32); keep it stable to compare digests across runs; "
        "unset means a random per-run key and digests that match no other run",
    ),
)

_HEADER = (
    "# LeadForge credential example. GENERATED FILE: do not edit by hand.\n"
    f"# Regenerate with: {REGENERATE_COMMAND}\n"
    "# Copy to .env and fill in values. Never commit real values.\n"
)


class ManifestError(ValueError):
    """The registry declares something that cannot be documented safely."""


def _safe_text(value: str, what: str) -> str:
    if not value.strip() or not value.isprintable():
        raise ManifestError(f"{what} must be non-blank printable text, got {value!r}")
    return value


def _env_name(name: str, owner: str) -> str:
    _safe_text(name, f"{owner}: variable name")
    if "#" in name or '"' in name or "'" in name or not name.isascii():
        raise ManifestError(f"{owner}: unusable variable name {name!r}")
    if name in RETIRED_VARIABLES:
        raise ManifestError(
            f"{owner} declares retired variable {name}: no code path may call the "
            "Custom Search API"
        )
    return name


def _doc_url(source_class: type, owner: str) -> str:
    url = getattr(source_class, "docs_url", "")
    if not isinstance(url, str):
        raise ManifestError(f"{owner}: docs_url must be a str, got {url!r}")
    if not url:
        buckets = getattr(source_class, "rate_limit", {})
        url = next((buckets[k].doc_url for k in sorted(buckets)), "")
    if not url:
        raise ManifestError(
            f"{owner} reads environment variables but declares no docs_url and no "
            "rate bucket doc_url to name in .env.example"
        )
    if (
        not url.isprintable()
        or not url.isascii()
        or any(c.isspace() for c in url)
        or not url.startswith(("https://", "http://"))
    ):
        raise ManifestError(f"{owner}: docs_url must be a single http(s) URL: {url!r}")
    return url


def _notes(
    source_class: type, owner: str, variables: tuple[str, ...]
) -> dict[str, str]:
    """The adapter's ``env_notes``: one printable line per variable it declares."""
    declared = getattr(source_class, "env_notes", {})
    notes: dict[str, str] = {}
    for variable, note in declared.items():
        if variable not in variables:
            raise ManifestError(f"{owner}: note for undeclared variable {variable!r}")
        if not isinstance(note, str):
            raise ManifestError(f"{owner}: note for {variable} must be a str")
        notes[variable] = _safe_text(note, f"{owner}: note for {variable}")
    return notes


def render_env_example(registry: SourceRegistry) -> str:
    """The example file text for ``registry``: sorted, LF-only, one final newline."""
    # variable -> (note, {(owner, url)})
    entries: dict[str, tuple[str, set[tuple[str, str]]]] = {}
    order = [variable for variable, *_ in BUILTIN_SETTINGS]
    for variable, owner, url, note in BUILTIN_SETTINGS:
        entries[variable] = (note, {(owner, url)})
    adapter_vars: set[str] = set()
    for name in registry.names():
        source_class = registry.source_class(name)
        owner = _safe_text(name, "source name")
        variables = (*source_class.required_env, *source_class.optional_env)
        notes = _notes(source_class, owner, variables)
        if not variables:
            continue
        url = _doc_url(source_class, owner)
        for variable in variables:
            _env_name(variable, owner)
            if variable not in entries:
                entries[variable] = ("", set())
                adapter_vars.add(variable)
            note, providers = entries[variable]
            mine = notes.get(variable, "")
            if mine and note and mine != note:
                raise ManifestError(
                    f"{owner}: note for {variable} differs from another declaration"
                )
            entries[variable] = (note or mine, providers | {(owner, url)})
    order += sorted(adapter_vars)

    out = [_HEADER]
    for variable in order:
        note, providers = entries[variable]
        lines = [f"# {note}"] if note else []
        lines += [f"# {owner} docs: {url}" for owner, url in sorted(providers)]
        out.append("\n".join([*lines, f"{variable}="]) + "\n")
    return "\n".join(out)


def write_env_example(registry: SourceRegistry, path: Path = DEFAULT_PATH) -> bool:
    """Write the generated file with LF endings; True if the content changed."""
    data = render_env_example(registry).encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m leadforge.lead_ingestion.env_example",
        description="Generate .env.example from the adapter registry.",
    )
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if the file is out of date"
    )
    args = parser.parse_args(argv)
    registry = SourceRegistry.discover()
    if args.check:
        data = render_env_example(registry).encode("utf-8")
        if args.path.is_file() and args.path.read_bytes() == data:
            return 0
        print(
            f"{args.path} is out of date. Regenerate with: {REGENERATE_COMMAND}",
            file=sys.stderr,
        )
        return 1
    changed = write_env_example(registry, args.path)
    print(f"{args.path}: {'written' if changed else 'already up to date'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
