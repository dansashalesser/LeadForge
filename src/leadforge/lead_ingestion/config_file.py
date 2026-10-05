"""Shared plumbing for the YAML files under ``config/`` (task 9.1).

Provisional decisions (see choices.md, task 9.1):

* Parsing is ``yaml`` safe-mode only: no tag can construct a Python object.
* A duplicate mapping key is an error (PyYAML would silently keep the last one), so
  two lines for one term can never quietly shadow each other.
* Every failure is a ``ConfigurationError`` naming the file and the key path. The
  text of a YAML error and any offending value are dropped, because a mistyped line
  may hold a credential; only the line number and exception class are kept.
* Loaded values are deep-frozen: mappings become read-only views, lists tuples.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from leadforge.lead_ingestion.errors import ConfigurationError

__all__ = ["check_name", "freeze", "join_path", "read_yaml_document"]

_MERGE_TAG = "tag:yaml.org,2002:merge"
_STR_TAG = "tag:yaml.org,2002:str"


def join_path(parent: str, key: str) -> str:
    return f"{parent}.{key}" if parent else key


def read_yaml_document(path: Path, *, missing_ok: bool = False) -> object:
    """Top-level object of the document; ``None`` if empty (or absent and allowed)."""
    where = str(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ConfigurationError(where, key_path="", detail="file not found") from None
    except IsADirectoryError:
        raise ConfigurationError(where, key_path="", detail="not a file") from None
    except OSError as exc:
        raise ConfigurationError(
            where, key_path="", detail=f"unreadable ({type(exc).__name__})"
        ) from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConfigurationError(where, key_path="", detail="not valid UTF-8") from None
    loader = yaml.SafeLoader(text)
    try:
        try:
            node = loader.get_single_node()
            if node is None:
                return None
            duplicate = _find_duplicate_key(node)
            if duplicate is not None:
                raise ConfigurationError(
                    where, key_path=duplicate, detail="duplicate key"
                )
            return loader.construct_document(node)
        finally:
            loader.dispose()
    except RecursionError:
        raise ConfigurationError(
            where, key_path="", detail="nested too deeply"
        ) from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        line = f" at line {mark.line + 1}" if mark is not None else ""
        raise ConfigurationError(
            where, key_path="", detail=f"invalid YAML ({type(exc).__name__}){line}"
        ) from None


def _find_duplicate_key(root: Node) -> str | None:
    """Key path of the first repeated mapping key, in document order."""
    seen: set[int] = set()  # aliases share nodes; visit each once

    def walk(node: Node, path: str) -> str | None:
        if id(node) in seen:
            return None
        seen.add(id(node))
        if isinstance(node, MappingNode):
            keys: set[tuple[str, str]] = set()
            for key_node, value_node in node.value:
                if not isinstance(key_node, ScalarNode) or key_node.tag == _MERGE_TAG:
                    child = path
                else:
                    ident = (key_node.tag, key_node.value)
                    child = join_path(path, key_node.value)
                    if ident in keys:
                        return child
                    keys.add(ident)
                    if key_node.tag != _STR_TAG:
                        child = path  # keep non-text key text out of key paths
                found = walk(value_node, child)
                if found is not None:
                    return found
        elif isinstance(node, SequenceNode):
            for index, item in enumerate(node.value):
                found = walk(item, f"{path}[{index}]")
                if found is not None:
                    return found
        return None

    return walk(root, "")


def check_name(path: Path, parent: str, key: object) -> str:
    """A name used as a mapping key: non-blank text without surrounding whitespace."""
    if not isinstance(key, str) or not key.strip() or key != key.strip():
        raise ConfigurationError(
            str(path),
            key_path=parent,
            detail="keys must be non-blank text without surrounding whitespace",
        )
    return key


def freeze(path: Path, key_path: str, value: object) -> object:
    """Deep read-only copy: mappings to views, lists to tuples, sets to frozensets.

    Shared sub-objects (YAML aliases) are frozen once, and a cycle is rejected, so a
    document of nested aliases cannot expand without bound.
    """
    memo: dict[int, object] = {}
    active: set[int] = set()

    def go(item: object) -> object:
        if isinstance(item, Mapping | list | tuple | set | frozenset):
            ident = id(item)
            if ident in memo:
                return memo[ident]
            if ident in active:
                raise ConfigurationError(
                    str(path), key_path=key_path, detail="recursive structure"
                )
            active.add(ident)
            try:
                frozen: object
                if isinstance(item, Mapping):
                    frozen = MappingProxyType({k: go(v) for k, v in item.items()})
                elif isinstance(item, set | frozenset):
                    frozen = frozenset(go(v) for v in item)
                else:
                    frozen = tuple(go(v) for v in item)
            finally:
                active.discard(ident)
            memo[ident] = frozen
            return frozen
        return item

    try:
        return go(value)
    except RecursionError:
        raise ConfigurationError(
            str(path), key_path=key_path, detail="nested too deeply"
        ) from None
