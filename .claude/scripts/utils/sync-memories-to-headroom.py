#!/usr/bin/env python3
"""Bidirectional sync: harness markdown memories ↔ headroom SQLite DB.

Run with headroom's own Python — the only interpreter that can import headroom.
Ask uv where that is rather than naming its tool directory, which is relocatable
via UV_TOOL_DIR / XDG_DATA_HOME and differs under a pipx install:

    "$(uv tool dir)/headroom-ai/bin/python" scripts/utils/sync-memories-to-headroom.py

hooks/claude/session-start-hook.sh resolves it the same way, through
scripts/lib/tool-paths.sh's find_tool_python.

Import direction:  ~/.claude/projects/<harness>/memory/*.md  →  headroom SQLite
Export direction:  headroom SQLite new memories  →  MEMORY.md (Headroom section)

Uses headroom's built-in fingerprint no-op — cheap on repeated calls.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from headroom import paths as headroom_paths
from headroom.memory.backends.local import LocalBackend, LocalBackendConfig
from headroom.memory.sync import sync
from headroom.memory.sync_adapters.claude_code import ClaudeCodeAdapter


def _harness_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if parent.name == ".claude":
            return parent.parent
    raise RuntimeError(
        f"sync-memories-to-headroom.py is not inside a .claude/ tree: {__file__}"
    )


HARNESS_PATH = _harness_root()
DB_PATH = headroom_paths.memory_db_path()  # ~/.headroom/memory.db


_PROJECT_DIR_TRANSLATION = str.maketrans({"/": "-", "\\": "-", ".": "-"})


def _claude_project_dir(project_path: Path) -> str:
    """Encode path as Claude Code does: replace / \\ and . all with -."""
    return str(project_path).translate(_PROJECT_DIR_TRANSLATION)


MEMORY_DIR = Path.home() / ".claude" / "projects" / _claude_project_dir(HARNESS_PATH) / "memory"
USER_ID = Path.home().name


async def main() -> int:
    if not MEMORY_DIR.exists():
        print(f"[headroom-sync] memory dir missing: {MEMORY_DIR}", file=sys.stderr)
        return 1

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = LocalBackendConfig(db_path=str(DB_PATH))
    backend = LocalBackend(config)
    adapter = ClaudeCodeAdapter(memory_dir=MEMORY_DIR)

    result = await sync(backend, adapter, user_id=USER_ID)

    if result.imported or result.exported:
        print(
            f"[headroom-sync] imported={result.imported} exported={result.exported} "
            f"skipped={result.skipped_unchanged} ({result.duration_ms:.0f}ms)",
            flush=True,
        )
    # else: quiet no-op — fingerprints matched, nothing changed

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
