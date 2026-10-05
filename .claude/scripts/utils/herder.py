#!/usr/bin/env python3
"""herder.py — spawn and supervise Claude Code sessions, driven from the dashboard.

Backend is Herdr (https://herdr.dev). `herdr server` is a headless daemon that
needs no TTY, and every `herdr workspace|tab|pane|agent` subcommand answers with
JSON on stdout — so nothing here has to pattern-match text, which keeps it inside
the repo-wide parsing ban.

Why Herdr rather than `subprocess.Popen(["claude", "--print", ...])` (the
primitive already used by dashboard.py's skill-curator endpoints): `--print` is a
one-shot pipe with nothing to attach to. Herdr starts a *real interactive* Claude
Code session that outlives the dashboard process, reports lifecycle state the
dashboard can poll (`idle`/`working`/`blocked`/`done`), and can be attached to
from a terminal mid-run with `herdr agent attach <name>`.

Verified against Herdr 0.8.2 on macOS. Two behaviours found by probing, both
handled below and neither documented upstream:

  1. A pane inherits CLAUDE_CODE_CHILD_SESSION from whatever started the server,
     which turns transcript saving OFF. Sessions spawned that way are invisible to
     scripts/utils/token-forensics.py and agents/kiro/session-judge.md — the
     herder would blind the harness's own instrumentation. Scrubbed in two places:
     the server env when we start it, and per-workspace via `--env`.
  2. Extra agent args go after a bare `--`, e.g.
     `herdr agent start x --kind claude --pane w1:p1 -- --permission-mode acceptEdits`.
     The returned `result.argv` echoes the final command, so the flag can be
     verified rather than assumed.

Known limitation — first run in an untrusted repo:
    `agent start` fails with `agent_not_ready: agent ... blocked during startup`
    when Claude Code stops on its first-run "do you trust the files in this
    folder?" prompt. Herdr correctly refuses to call a blocked agent ready. There
    is nothing to work around in code: open the repo in Claude Code once by hand
    and accept the prompt, after which spawning it works. Observed on a repo that
    had never been opened; repos already in use spawn first try.
    The trust prompt is not the only one: a repo whose `.mcp.json` registers a
    server not yet approved stops on "New MCP server found" the same way (seen
    2026-10-01 after GitNexus setup wrote one). spawn() reads the pane before
    closing it and raises AgentBlockedError naming the prompt, so the next new
    kind of startup dialog explains itself instead of needing a hand repro.
    Errors from herdr arrive as a JSON envelope on stderr with a non-zero exit,
    so _run() parses stderr too — otherwise this surfaces as an unreadable dump.

CLI (for testing without the dashboard):
    python3 herder.py status
    python3 herder.py list
    python3 herder.py spawn <repo> <label> [--prompt TEXT] [--kind claude]
    python3 herder.py read <name>
    python3 herder.py stop <workspace_id>
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Agent kinds Herdr 0.8.2 can detect and drive. Read off `herdr agent start --help`;
# not invented here. `claude` is the default and the only one this harness targets.
AGENT_KINDS = (
    "claude", "codex", "gemini", "cursor", "copilot", "devin", "droid", "amp",
    "grok", "hermes", "kilo", "kimi", "kiro", "maki", "mastracode", "omp",
    "opencode", "pi", "qodercli", "qwen", "agy", "cline",
)

# Last-resort permission modes, used ONLY when the agent CLI cannot be probed
# (not installed, or it does not enumerate its own choices). Discovery is the real
# path — see discover_permission_modes(). Anything served from this list is
# flagged `discovered: false` so the UI can say so instead of quietly guessing.
FALLBACK_PERMISSION_MODES = ("acceptEdits", "bypassPermissions", "plan")

# Marker emitted by commander.js when an enum flag gets an invalid value:
#   error: option '--permission-mode <mode>' argument 'x' is invalid.
#   Allowed choices are acceptEdits, auto, bypassPermissions, manual, dontAsk, plan.
# Asking the CLI's own validator beats scraping --help, whose text wraps across
# lines. Split on plain string boundaries — no regex, per the repo-wide ban.
_CHOICES_MARKER = "Allowed choices are "

# Which pricing-catalog provider corresponds to each agent kind. Used to derive
# the model list from live pricing data rather than hardcoding model names.
PROVIDER_FOR_KIND = {
    "claude": "anthropic",
    "codex": "openai",
    "gemini": "google",
    "grok": "xai",
    "qwen": "alibaba",
    "kimi": "moonshotai",
}

_probe_cache: dict = {}

# Env vars scrubbed from every spawned session. CLAUDE_CODE_CHILD_SESSION marks a
# nested session and disables transcript saving; a herder-spawned agent is a
# top-level session and must be recorded like any other.
SCRUB_ENV = ("CLAUDE_CODE_CHILD_SESSION",)

START_TIMEOUT_MS = 90_000
SERVER_BOOT_TIMEOUT_S = 10.0


class HerderError(RuntimeError):
    """A Herdr call failed, or Herdr is not installed."""


class AgentBlockedError(HerderError):
    """The agent stopped on an interactive prompt before it was ready.

    `screen` is the prompt as it appeared in the pane, captured before the
    workspace is closed — otherwise the one piece of evidence that says *which*
    prompt (trust, new MCP server, …) is destroyed with the pane.
    """

    def __init__(self, message: str, screen: str):
        super().__init__(message)
        self.screen = screen


def find_herdr() -> str:
    """Locate the herdr binary. Discovered, never hardcoded.

    The installer defaults to ~/.local/bin, which is not always on PATH for a
    process started by launchd or by a GUI app, so PATH is checked first and the
    documented install dir second.
    """
    found = shutil.which("herdr")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "herdr"
    if fallback.is_file() and os.access(fallback, os.X_OK):
        return str(fallback)
    raise HerderError(
        "herdr not found on PATH or at ~/.local/bin/herdr. "
        "Install: curl -fsSL https://herdr.dev/install.sh | sh"
    )


def harness_root() -> Path:
    """Directory owning both projects.txt and install.sh. Discovered, not named."""
    for p in Path(__file__).resolve().parents:
        if (p / "projects.txt").is_file() and (p / "install.sh").is_file():
            return p
    raise HerderError(f"cannot locate harness root from {__file__}")


def _parse_choices(text: str) -> list:
    """Pull the allowed values out of a commander.js enum-validation error.

    Plain string slicing: locate the marker, take the sentence, split on commas.
    No regex — a hand-rolled pattern over CLI output is exactly the almost-right
    parser the repo-wide ban exists to prevent.
    """
    idx = text.find(_CHOICES_MARKER)
    if idx == -1:
        return []
    tail = text[idx + len(_CHOICES_MARKER):]
    stop = tail.find(".")
    if stop != -1:
        tail = tail[:stop]
    return [part.strip() for part in tail.split(",") if part.strip()]


def discover_permission_modes(kind: str = "claude") -> dict:
    """Ask the agent's own CLI which permission modes it accepts.

    Deliberately passes an invalid value: the CLI rejects it during argument
    parsing and lists the valid set, so nothing is executed and no session
    starts. Returns {"modes": [...], "discovered": bool, "source": str}.
    """
    cache_key = ("modes", kind)
    if cache_key in _probe_cache:
        return _probe_cache[cache_key]

    binary = shutil.which(kind)
    result = {"modes": list(FALLBACK_PERMISSION_MODES), "discovered": False,
              "source": f"fallback ({kind} not on PATH)"}
    if binary:
        try:
            proc = subprocess.run(
                [binary, "--permission-mode", "__sdd_probe_invalid__",
                 "--print", "probe"],
                capture_output=True, text=True, timeout=25,
                env=_clean_env(), check=False,
            )
            found = _parse_choices((proc.stderr or "") + (proc.stdout or ""))
            if found:
                result = {"modes": found, "discovered": True,
                          "source": f"{kind} --permission-mode validator"}
            else:
                result["source"] = f"fallback ({kind} did not enumerate choices)"
        except (subprocess.TimeoutExpired, OSError):
            result["source"] = f"fallback ({kind} probe failed)"

    _probe_cache[cache_key] = result
    return result


def discover_models(kind: str = "claude") -> dict:
    """Model ids the given agent can be pointed at, from live pricing data.

    Source is `.dashboard/models-pricing-history.json`, which the dashboard already
    refreshes on a 14-day cadence from a public catalogue — so the list tracks
    reality instead of a literal in this file. Family aliases (`opus`, `sonnet`, …)
    are derived from the ids present, not enumerated by hand.
    """
    cache_key = ("models", kind)
    if cache_key in _probe_cache:
        return _probe_cache[cache_key]

    provider = PROVIDER_FOR_KIND.get(kind)
    result = {"models": [], "aliases": [], "discovered": False,
              "source": f"no pricing provider mapped for {kind!r}"}
    if provider:
        catalogue = harness_root() / ".dashboard" / "models-pricing-history.json"
        try:
            data = json.loads(catalogue.read_text(encoding="utf-8"))
            snapshots = data.get("snapshots") or []
            models = (snapshots[-1] or {}).get("models") or {}
            prefix = provider + "/"
            ids = sorted(
                key[len(prefix):] for key in models if key.startswith(prefix)
            )
            aliases = sorted({
                part.split("-")[1]
                for part in ids
                if len(part.split("-")) > 1 and not part.split("-")[1].isdigit()
            })
            if ids:
                result = {
                    "models": ids,
                    "aliases": aliases,
                    "discovered": True,
                    "source": f"{catalogue.name} ({provider}, "
                              f"fetched {(snapshots[-1] or {}).get('fetched_at', '?')})",
                }
        except (OSError, ValueError, IndexError, AttributeError) as exc:
            result["source"] = f"pricing catalogue unreadable: {exc}"

    _probe_cache[cache_key] = result
    return result


def agent_options(kind: str = "claude") -> dict:
    """Everything the spawn form needs for one agent kind, all discovered."""
    modes = discover_permission_modes(kind)
    models = discover_models(kind)
    return {"kind": kind, "permission_modes": modes, "models": models}


def _clean_env() -> dict:
    env = dict(os.environ)
    for key in SCRUB_ENV:
        env.pop(key, None)
    return env


def _run(args, timeout: float = 30) -> dict:
    """Run a herdr subcommand and return its parsed JSON result.

    Raises HerderError on a non-JSON answer or on an `error` object — a caller
    must never mistake a failed call for an empty result.
    """
    cmd = [find_herdr(), *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=_clean_env(), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HerderError(f"herdr {' '.join(args)} timed out after {timeout}s") from exc

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if not out:
        # herdr reports failures as a JSON envelope on STDERR with a non-zero exit.
        # Parse it so callers get "agent_not_ready: ..." rather than a raw dump of
        # the whole command line.
        if err.startswith("{"):
            try:
                payload = json.loads(err)
            except ValueError:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                e = payload["error"]
                raise HerderError(
                    f"{e.get('code', 'error')}: {e.get('message', '')}".strip(": ")
                )
        raise HerderError(
            f"herdr {' '.join(args)} produced no output "
            f"(exit {proc.returncode}): {err[:300]}"
        )
    try:
        payload = json.loads(out)
    except ValueError as exc:
        raise HerderError(
            f"herdr {' '.join(args)} did not return JSON: {out[:300]}"
        ) from exc

    if isinstance(payload, dict) and "error" in payload:
        err = payload["error"]
        code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise HerderError(f"{code}: {msg}")
    return payload


def server_running() -> bool:
    """True when the headless server answers. False when it is not running.

    Distinguishes "not running" (an answer) from "herdr missing" (an error) —
    the caller can start a stopped server but cannot fix a missing binary.
    """
    try:
        _run(["workspace", "list"], timeout=10)
        return True
    except HerderError as exc:
        if "server_not_running" in str(exc):
            return False
        if "not found on PATH" in str(exc):
            raise
        return False


def ensure_server() -> bool:
    """Start `herdr server` if it is not already up. Returns True once reachable.

    The server is detached deliberately: it must outlive the dashboard process,
    which is the whole point of spawning sessions from a UI you may close.
    """
    if server_running():
        return True
    subprocess.Popen(
        [find_herdr(), "server"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        env=_clean_env(),
        start_new_session=True,
    )
    deadline = time.time() + SERVER_BOOT_TIMEOUT_S
    while time.time() < deadline:
        time.sleep(0.4)
        if server_running():
            return True
    raise HerderError(
        f"herdr server did not become reachable within {SERVER_BOOT_TIMEOUT_S:.0f}s"
    )


def list_agents() -> list:
    """Every agent Herdr currently knows about, flattened for the dashboard."""
    if not server_running():
        return []
    payload = _run(["agent", "list"])
    agents = payload.get("result", {}).get("agents", []) or []
    out = []
    for a in agents:
        name = a.get("name") or "?"
        entry = read_ledger(name)
        out.append({
            "name": name,
            "kind": a.get("agent") or "?",
            "status": a.get("agent_status") or "unknown",
            "cwd": a.get("cwd") or "",
            "repo": Path(a.get("cwd") or "").name,
            "workspace_id": a.get("workspace_id") or "",
            "pane_id": a.get("pane_id") or "",
            "ready": bool(a.get("interactive_ready")),
            # Present only for agents this herder started; a pane the user opened
            # by hand inside Herdr shows up here too and legitimately has none.
            "herder_spawned": entry is not None,
            "model": (entry or {}).get("model") or "",
            "permission_mode": (entry or {}).get("permission_mode") or "",
            "spend": agent_spend(name),
        })
    return out


def list_workspaces() -> list:
    if not server_running():
        return []
    payload = _run(["workspace", "list"])
    return payload.get("result", {}).get("workspaces", []) or []


def _unique_name(label: str) -> str:
    """Build a name Herdr will accept, from an arbitrary user label.

    Herdr 0.8.2 rejects anything outside: starts with a lowercase letter, then
    lowercase letters, digits, '-' or '_', 1-32 characters. A label of "boxB" was
    refused with `invalid_agent_name` until this lowercased, so the rules are
    enforced here rather than discovered by the user at spawn time.

    The 7-char `-xxxxxx` suffix keeps names unique, so the label is truncated to
    25 to stay inside the 32-char ceiling.
    """
    lowered = label.lower()
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in lowered)
    safe = safe.strip("-_")
    # Must begin with a letter: a label like "2fix" or "" needs a prefix.
    if not safe or not safe[0].isalpha():
        safe = "agent" + ("-" + safe if safe else "")
    return f"{safe[:25]}-{uuid.uuid4().hex[:6]}"


def spawn(repo: str, label: str, *, kind: str = "claude", prompt: str = "",
          permission_mode: str = "acceptEdits", model: str = "") -> dict:
    """Create a workspace in `repo`, start an agent in it, optionally prompt it.

    Returns the agent record plus the argv Herdr actually launched, so the caller
    can verify the flags rather than trusting that they were applied.
    """
    repo_path = Path(repo).expanduser()
    if not repo_path.is_dir():
        raise HerderError(f"not a directory: {repo}")
    if kind not in AGENT_KINDS:
        raise HerderError(f"unsupported agent kind {kind!r}")

    # Validate against what this agent actually accepts, not a list in this file.
    # When the probe could not run, `discovered` is False and the fallback is
    # permissive on purpose — refusing a mode we merely failed to enumerate would
    # turn a probe failure into a wrong "unsupported" error.
    modes = discover_permission_modes(kind)
    if modes["discovered"] and permission_mode not in modes["modes"]:
        raise HerderError(
            f"permission mode {permission_mode!r} not accepted by {kind} — "
            f"choices: {', '.join(modes['modes'])}"
        )

    ensure_server()
    name = _unique_name(label)

    create_args = [
        "workspace", "create",
        "--cwd", str(repo_path),
        "--label", name,
        "--no-focus",
    ]
    for key in SCRUB_ENV:
        create_args += ["--env", f"{key}="]

    created = _run(create_args, timeout=30)["result"]
    workspace_id = created["workspace"]["workspace_id"]
    pane_id = created["root_pane"]["pane_id"]

    # `--name` tags the session in Claude Code's own prompt box and /resume list,
    # so a herder-spawned run is identifiable from inside the session too, not
    # only from this ledger.
    agent_args = ["--permission-mode", permission_mode, "--name", f"herder:{name}"]
    if model:
        agent_args += ["--model", model]

    spawn_at = time.time()
    # Snapshot the transcripts that already exist, so the one this spawn creates
    # can be identified by difference rather than by being "newest".
    known_before = sorted(_transcript_stems(repo_path))

    try:
        started = _run(
            ["agent", "start", name, "--kind", kind, "--pane", pane_id,
             "--timeout", str(START_TIMEOUT_MS), "--", *agent_args],
            timeout=START_TIMEOUT_MS / 1000 + 15,
        )["result"]
    except HerderError as exc:
        blocked = _abandon_start(exc, workspace_id, pane_id, repo_path)
        if blocked:
            raise blocked from exc
        raise

    if prompt.strip():
        # No --wait: the caller is an HTTP handler. Fire the prompt and let the
        # roster poll reflect progress.
        _run(["agent", "prompt", name, prompt], timeout=30)

    record = {
        "name": name,
        "workspace_id": workspace_id,
        "pane_id": pane_id,
        "argv": started.get("argv", []),
        "status": started.get("agent", {}).get("agent_status", "unknown"),
        "repo": repo_path.name,
        "cwd": str(repo_path),
        "prompted": bool(prompt.strip()),
        "kind": kind,
        "model": model,
        "permission_mode": permission_mode,
        "spawn_at": spawn_at,
        "known_before": known_before,
        "session_id": _resolve_session_id(repo_path, known_before, name),
    }
    _write_ledger(record)
    return record


def _abandon_start(exc: HerderError, workspace_id: str, pane_id: str,
                   repo_path: Path) -> AgentBlockedError | None:
    """Close the workspace of a failed `agent start`; explain it if it was blocked.

    The pane is read BEFORE the close: on agent_not_ready it shows the prompt
    Claude Code stopped on, and closing the workspace destroys it. Returns the
    richer error to raise, or None to re-raise the original.
    """
    screen = _blocking_screen(pane_id) if "agent_not_ready" in str(exc) else ""
    # Never leave a half-built workspace behind holding a shell pane.
    try:
        _run(["workspace", "close", workspace_id], timeout=10)
    except HerderError:
        pass
    if not screen:
        return None
    headline = next(line.strip() for line in screen.splitlines() if line.strip())
    return AgentBlockedError(
        f"{exc} — blocked on: {headline}. Answer it once by running "
        f"`claude` in {repo_path}, then spawn again.",
        screen,
    )


# ── Attribution ──────────────────────────────────────────────────────────────
# A herder-spawned session is an ordinary Claude Code session: it writes a normal
# transcript under ~/.claude/projects/<slug>/, so scripts/utils/token-forensics.py
# and the session hooks already see it with no extra work (this is exactly why
# CLAUDE_CODE_CHILD_SESSION is scrubbed — with it set, none of that happens).
#
# What was missing is the reverse mapping: which transcript belongs to which
# herder agent. Claude Code offers no --session-id for a NEW session, so the id is
# recovered by taking the newest transcript in that repo created after the spawn
# instant. That is a heuristic, and it is recorded as one — `session_id` may be
# null, and a null is reported rather than guessed at.

def _project_slug_dir(repo_path: Path) -> Path:
    slug = str(repo_path).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug


def _transcript_stems(repo_path: Path) -> set:
    proj = _project_slug_dir(repo_path)
    if not proj.is_dir():
        return set()
    try:
        return {f.stem for f in proj.glob("*.jsonl")}
    except OSError:
        return set()


def _session_tag(name: str) -> str:
    """The value passed to `claude --name`, which the transcript records."""
    return f"herder:{name}"


def _transcript_tag(path: Path):
    """Read the session's own recorded name out of its transcript, or None.

    Claude Code writes `--name` into two typed line kinds near the head of the
    file — `{"type":"agent-name","agentName":…}` and
    `{"type":"custom-title","customTitle":…}`. Reading those fields is exact:
    no timestamps, no ordering assumptions, no substring matching.
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh):
                if lineno > 80:      # the tag is written at session start
                    return None
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                for field in ("agentName", "customTitle"):
                    val = obj.get(field)
                    if isinstance(val, str) and val:
                        return val
    except OSError:
        return None
    return None


def _resolve_session_id(repo_path: Path, known_before, name: str = ""):
    """Find this agent's transcript by the name the session recorded for itself.

    Two earlier attempts were both wrong, and both failures were observed live:

      1. "newest transcript modified after the spawn" — an interactive session
         already open in the target repo is written constantly and is always
         newest, so a fresh agent was credited with 94,259,775 tokens that
         belonged to the session which spawned it.
      2. "newest transcript not present before the spawn" — fixes that, but three
         agents started seconds apart each claimed a sibling's transcript, so
         alpha's feed showed gamma's output.

    Matching on the recorded `--name` has neither failure mode, because the
    session states its own identity. `known_before` is still used to skip files
    that predate the spawn, which keeps the scan small.
    """
    known = set(known_before or ())
    proj = _project_slug_dir(repo_path)
    if not proj.is_dir():
        return None

    candidates = []
    try:
        for f in proj.glob("*.jsonl"):
            if f.stem in known:
                continue
            try:
                candidates.append((f.stat().st_mtime, f))
            except OSError:
                continue
    except OSError:
        return None

    if name:
        want = _session_tag(name)
        for _mtime, f in sorted(candidates, reverse=True):
            if _transcript_tag(f) == want:
                return f.stem
        # Named lookup failed: the session may not have written its tag yet.
        # Report nothing rather than fall back to a positional guess — that guess
        # is precisely what produced both bugs above.
        return None

    return None


def ledger_dir() -> Path:
    d = harness_root() / ".dashboard" / "herder"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_ledger(record: dict) -> None:
    try:
        (ledger_dir() / f"{record['name']}.json").write_text(
            json.dumps(record, indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        pass          # a lost ledger entry must never fail a working spawn


def read_ledger(name: str):
    try:
        entry = json.loads((ledger_dir() / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _ensure_session_id(entry)


def _ensure_session_id(entry: dict) -> dict:
    """Resolve the transcript id lazily, and persist it once found.

    At spawn time the session has usually not written its first transcript line
    yet, so resolving there returns None — measured, not assumed. Retry on every
    read until it appears, then cache it so the directory scan stops.
    """
    if not entry.get("cwd"):
        return entry

    known = entry.get("known_before")
    sid = entry.get("session_id")

    # Self-heal a wrong entry by checking the id against the transcript's own
    # recorded name. The ledger is a shared file, and any process holding older
    # code re-resolves on every poll and overwrites it — observed twice, once
    # attributing an interactive session's 94M tokens to a fresh agent, and once
    # pointing all three of three concurrent agents at a single sibling's
    # transcript. Validating rather than trusting makes the stored value
    # self-correcting no matter who wrote it.
    if sid and entry.get("name"):
        path = _project_slug_dir(Path(entry["cwd"])) / f"{sid}.jsonl"
        tag = _transcript_tag(path) if path.is_file() else None
        # A missing tag is not proof of a mismatch: the session may not have
        # written it yet. Only a tag that names a DIFFERENT agent is.
        if tag is not None and tag != _session_tag(entry["name"]):
            sid = None
            entry["session_id"] = None
    if sid and known is not None and sid in known:
        sid = None
        entry["session_id"] = None

    if sid:
        return entry
    if known is None:
        # Ledger written before the set-difference fix; its session cannot be
        # identified safely, and guessing is what produced a 94M-token
        # misattribution. Leave it null and say so rather than pick a candidate.
        return entry
    found = _resolve_session_id(Path(entry["cwd"]), known, entry.get("name", ""))
    if found:
        entry["session_id"] = found
        _write_ledger(entry)
    return entry


def agent_spend(name: str):
    """Token spend for one herder agent, read from its own transcript.

    Returns None when the session cannot be located — never 0, because "no
    transcript found" and "this session cost nothing" are different findings.
    """
    entry = read_ledger(name)
    if not entry or not entry.get("session_id"):
        return None
    proj = _project_slug_dir(Path(entry["cwd"]))
    path = proj / f"{entry['session_id']}.jsonl"
    if not path.is_file():
        return None

    fields = (("input", "input_tokens"), ("output", "output_tokens"),
              ("cache_read", "cache_read_input_tokens"),
              ("cache_create", "cache_creation_input_tokens"))
    weights = {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_create": 2.0}
    totals = dict.fromkeys(weights, 0)
    seen = set()
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            # Same requestId dedup the dashboard and token-forensics use; without
            # it a retried request is counted once per attempt.
            key = obj.get("requestId") or msg.get("id")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            for field, raw in fields:
                totals[field] += usage.get(raw, 0) or 0
    except OSError:
        return None

    return {
        "session_id": entry["session_id"],
        "tokens": sum(totals.values()),
        "weighted_cost": round(sum(totals[f] * weights[f] for f in weights), 1),
        **totals,
    }


def prompt_agent(name: str, text: str) -> dict:
    if not text.strip():
        raise HerderError("empty prompt")
    return _run(["agent", "prompt", name, text], timeout=30)


# ── Prompt-box completion ────────────────────────────────────────────────────
# The dashboard's prompt boxes complete `/skill` and `@path` the way Claude Code's
# own input does. Both lists are read off disk for the target repo, never kept in
# this file, and cached briefly so a burst of keystrokes is one disk walk.

COMPLETE_LIMIT = 50
_COMPLETE_TTL_S = 30.0
_complete_cache: dict = {}


def _frontmatter_description(md: Path) -> str:
    """`description:` from a markdown file's frontmatter, or "" when it has none.

    Line scanning only — no YAML dependency, no regex. Covers the plain and the
    folded (`>` / `|`) forms, which is all SKILL.md and command files use.
    """
    try:
        lines = md.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return ""
        if not line.startswith("description:"):
            continue
        value = line[len("description:"):].strip()
        if value in (">", "|", ">-", "|-"):
            folded = []
            for cont in lines[i + 1:]:
                if not cont.startswith((" ", "\t")):
                    break
                folded.append(cont.strip())
            value = " ".join(folded)
        return value.strip().strip('"').strip("'")
    return ""


def _collect_invocables(base: Path, prefix: str, source: str, out: dict) -> None:
    """Add every skill and command under `base` (a `.claude`-shaped dir) to `out`.

    `skills/<name>/SKILL.md` → `/name`; `commands/a/b.md` → `/a:b`, matching how
    Claude Code names nested commands (this repo's `kiro/` → `/kiro:spec-init`).
    First writer wins, so callers add sources in precedence order.
    """
    skills = base / "skills"
    if skills.is_dir():
        for d in sorted(skills.iterdir()):
            md = d / "SKILL.md"
            name = prefix + d.name
            if md.is_file() and name not in out:
                out[name] = {"name": name, "description": _frontmatter_description(md),
                             "source": source}
    commands = base / "commands"
    if commands.is_dir():
        for md in sorted(commands.rglob("*.md")):
            name = prefix + ":".join(md.relative_to(commands).with_suffix("").parts)
            if name not in out:
                out[name] = {"name": name, "description": _frontmatter_description(md),
                             "source": source}


def _plugin_roots() -> list:
    """(plugin_name, install_dir) for every plugin Claude Code has installed."""
    plugins = Path.home() / ".claude" / "plugins"
    roots = []
    manifest = plugins / "installed_plugins.json"
    if manifest.is_file():
        try:
            installed = json.loads(manifest.read_text()).get("plugins", {})
        except ValueError as exc:
            raise HerderError(f"unreadable {manifest}: {exc}") from exc
        for key, entries in installed.items():
            for entry in entries:
                path = Path(entry.get("installPath") or "")
                if path.is_dir():
                    roots.append((key.split("@", 1)[0], path))
    # claude.ai-synced plugins: synced/<org>/<plugin>/{skills,commands}.
    synced = plugins / "synced"
    if synced.is_dir():
        for org in synced.iterdir():
            if org.is_dir():
                roots += [(p.name, p) for p in org.iterdir() if p.is_dir()]
    return roots


def list_invocables(repo_path: Path) -> list:
    """Every `/name` Claude Code would offer inside `repo_path`.

    Precedence: project `.claude/`, then user `~/.claude/`, then plugins (always
    namespaced `plugin:name`, so they never collide with the first two).
    """
    out: dict = {}
    _collect_invocables(repo_path / ".claude", "", "project", out)
    _collect_invocables(Path.home() / ".claude", "", "user", out)
    for plugin, root in _plugin_roots():
        _collect_invocables(root, plugin + ":", "plugin", out)
    return list(out.values())


def list_repo_paths(repo_path: Path) -> list:
    """Tracked + untracked-not-ignored files, plus their directories (`dir/`)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "ls-files", "--cached", "--others",
             "--exclude-standard", "-z"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HerderError(f"git ls-files timed out in {repo_path}") from exc
    if proc.returncode != 0:
        raise HerderError(f"git ls-files failed in {repo_path}: {proc.stderr.strip()[:200]}")
    files = [f for f in proc.stdout.split("\0") if f]
    dirs = set()
    for f in files:
        parts = f.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]) + "/")
    return [{"name": d} for d in sorted(dirs)] + [{"name": f} for f in sorted(set(files))]


def _is_subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(ch in it for ch in needle)


def _rank(name: str, description: str, query: str):
    """Lower is better; None means no match. Mirrors fuzzy-finder ordering."""
    if not query:
        return 0
    n = name.lower()
    base = n.rstrip("/").rsplit("/", 1)[-1]
    if base.startswith(query) or n.startswith(query):
        return 0
    if _at_word_start(query, n):
        return 1
    if query in base:
        return 2
    if query in n:
        return 3
    if _is_subsequence(query, n):
        return 4
    if query in description.lower():
        return 5
    return None


def _at_word_start(query: str, name: str) -> bool:
    """True when `query` occurs right after a separator (`kiro:spec-…`, `a/b_c`)."""
    i = name.find(query)
    while i > 0:
        if name[i - 1] in "-_:/.":
            return True
        i = name.find(query, i + 1)
    return False


def complete(repo: str, kind: str, query: str) -> dict:
    """Ranked completions for the dashboard prompt box.

    kind="skill" → `/` candidates; kind="file" → `@` candidates. `query` is the
    text typed after the trigger character.
    """
    repo_path = Path(repo).expanduser()
    if not repo_path.is_dir():
        raise HerderError(f"not a directory: {repo}")
    if kind not in ("skill", "file"):
        raise HerderError(f"unknown completion kind {kind!r}")

    key = (kind, str(repo_path.resolve()))
    hit = _complete_cache.get(key)
    if hit and time.time() - hit[0] < _COMPLETE_TTL_S:
        items = hit[1]
    else:
        items = list_invocables(repo_path) if kind == "skill" else list_repo_paths(repo_path)
        _complete_cache[key] = (time.time(), items)

    q = query.lower()
    scored = []
    for item in items:
        r = _rank(item["name"], item.get("description", ""), q)
        if r is not None:
            # Shallow, short names first when match quality ties — the fuzzy-finder
            # tie-break, so `@read` offers README.md before some/deep/README.md.
            scored.append((r, item["name"].count("/"), len(item["name"]), item["name"], item))
    scored.sort(key=lambda s: s[:4])
    return {"kind": kind, "items": [s[4] for s in scored[:COMPLETE_LIMIT]],
            "total": len(scored)}


def read_agent(name: str) -> str:
    """Terminal contents for one agent.

    Unlike every other subcommand, `herdr agent read` emits the raw pane text on
    stdout rather than a JSON envelope, so this deliberately does not go through
    _run() — feeding terminal output to a JSON parser fails on the first escape
    sequence. Verified against 0.8.2.
    """
    try:
        proc = subprocess.run(
            [find_herdr(), "agent", "read", name],
            capture_output=True, text=True, timeout=20,
            env=_clean_env(), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HerderError(f"herdr agent read {name} timed out") from exc

    out = proc.stdout or ""
    if not out.strip():
        err = (proc.stderr or "").strip()
        # A JSON error envelope can still come back here (unknown agent, server
        # down), so surface it rather than returning a misleading empty pane.
        if err.startswith("{") or out.strip().startswith("{"):
            raise HerderError(err or out)
        raise HerderError(f"no output for agent {name!r}: {err[:200]}")
    return out


_RULE_CHARS = set("─━═-")
BLOCKING_SCREEN_MAX_LINES = 15


def _blocking_screen(pane_id: str) -> str:
    """The interactive prompt a just-started agent is stuck on, or "" if unreadable.

    Claude Code draws its startup dialogs (trust, new MCP server, …) under a
    horizontal rule, below the echoed launch command. Everything after the first
    rule line is the dialog; with no rule, the bottom of the pane is used. Plain
    line scanning — no regex. Best-effort by design: it only enriches an error
    that is already being raised, so a failed read must not replace that error.
    """
    try:
        proc = subprocess.run(
            [find_herdr(), "pane", "read", pane_id],
            capture_output=True, text=True, timeout=10,
            env=_clean_env(), check=False,
        )
    except (subprocess.TimeoutExpired, OSError, HerderError):
        return ""
    lines = (proc.stdout or "").splitlines()
    if proc.returncode != 0 or not lines:
        return ""

    def is_rule(line):
        s = line.strip()
        return len(s) >= 10 and set(s) <= _RULE_CHARS

    start = next((i + 1 for i, line in enumerate(lines) if is_rule(line)), None)
    body = lines[start:] if start is not None else lines[-BLOCKING_SCREEN_MAX_LINES:]
    # Drop leading/trailing blanks and collapse blank runs to one.
    kept = []
    for line in body:
        if line.strip() or (kept and kept[-1].strip()):
            kept.append(line.rstrip())
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept[:BLOCKING_SCREEN_MAX_LINES])


# Argument keys worth showing for a tool call, most-specific first. A tool is
# summarized by the first one it has — no guessing, no regex over the payload.
_TOOL_ARG_KEYS = (
    "command", "file_path", "path", "pattern", "query", "url",
    "prompt", "description", "name", "content",
)


# Chat bubbles render this text as markdown, so a clip mid-list or mid-fence
# breaks the layout as well as hiding the answer. Generous on purpose; only a
# runaway message should ever hit it.
CHAT_TEXT_LIMIT = 20000


def _clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _clip_chat(text):
    """Like `_clip`, but keeps newlines and indentation.

    `_clip` folds all whitespace to single spaces, which is right for one-line
    tool previews and wrong for chat: it flattens numbered lists, code fences
    and paragraphs into one run-on line before the dashboard can render them.
    """
    text = str(text).strip()
    if len(text) <= CHAT_TEXT_LIMIT:
        return text
    return text[:CHAT_TEXT_LIMIT - 1] + "…"


def _tool_summary(tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in _TOOL_ARG_KEYS:
        if tool_input.get(key):
            return _clip(tool_input[key], 160)
    return ""


def _result_preview(content) -> str:
    if isinstance(content, str):
        return _clip(content, 300)
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                parts.append(blk.get("text") or "")
            elif isinstance(blk, str):
                parts.append(blk)
        return _clip(" ".join(parts), 300)
    if isinstance(content, dict):
        return _clip(json.dumps(content), 300)
    return ""


def agent_stream(name: str, after: int = 0, limit: int = 60):
    """Structured activity feed for one agent, from its transcript.

    Reads the session's own JSONL rather than scraping the terminal pane: the
    pane is a rendered TUI (escape codes, redrawn boxes, a statusline) whose text
    would have to be pattern-matched back into meaning. The transcript already
    carries the events as data — reasoning, tool calls, tool results — so this
    reports what the agent is doing instead of what its terminal looks like.

    `after` is a line cursor, so polling returns only what is new.
    Returns {"events": [...], "cursor": int, "session_id": str} or None when the
    transcript is not locatable (never an empty feed, which would read as idle).
    """
    entry = read_ledger(name)
    if not entry or not entry.get("session_id"):
        return None
    path = _project_slug_dir(Path(entry["cwd"])) / f"{entry['session_id']}.jsonl"
    if not path.is_file():
        return None

    events = []
    tool_names = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    for lineno, line in enumerate(lines):
        if lineno < after:
            # Still track tool ids from skipped lines, or a result arriving after
            # the cursor would render as "?" with no tool name attached.
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            msg = obj.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), list):
                for blk in msg["content"]:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        tool_names[blk.get("id")] = blk.get("name") or "?"
            continue

        try:
            obj = json.loads(line)
        except ValueError:
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")

        # A plain human-typed prompt is stored as a bare string, not the
        # block-list shape assistant turns use — without this branch the
        # question itself never reaches the events list at all.
        if isinstance(content, str):
            text = _clip_chat(content)
            if text:
                events.append({"t": "text", "role": role, "text": text})
            continue

        if not isinstance(content, list):
            continue

        for blk in content:
            if not isinstance(blk, dict):
                continue
            btype = blk.get("type")
            if btype == "thinking":
                text = _clip(blk.get("thinking") or blk.get("text") or "", 400)
                if text:
                    events.append({"t": "thinking", "text": text})
            elif btype == "text":
                text = _clip_chat(blk.get("text") or "")
                if text:
                    events.append({"t": "text", "role": role, "text": text})
            elif btype == "tool_use":
                tname = blk.get("name") or "?"
                tool_names[blk.get("id")] = tname
                events.append({"t": "tool", "name": tname,
                               "summary": _tool_summary(blk.get("input"))})
            elif btype == "tool_result":
                events.append({
                    "t": "result",
                    "name": tool_names.get(blk.get("tool_use_id"), "?"),
                    "error": bool(blk.get("is_error")),
                    "preview": _result_preview(blk.get("content")),
                })

    return {
        "session_id": entry["session_id"],
        "cursor": len(lines),
        "events": events[-limit:],
        "truncated": len(events) > limit,
    }


def stop(workspace_id: str) -> dict:
    """Close a workspace, terminating the agent inside it."""
    if not workspace_id:
        raise HerderError("no workspace id")
    return _run(["workspace", "close", workspace_id], timeout=20)


def status() -> dict:
    """Summary for the dashboard header."""
    try:
        binary = find_herdr()
    except HerderError as exc:
        return {"installed": False, "running": False, "error": str(exc),
                "agents": 0, "workspaces": 0}
    running = server_running()
    return {
        "installed": True,
        "binary": binary,
        "running": running,
        "agents": len(list_agents()) if running else 0,
        "workspaces": len(list_workspaces()) if running else 0,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    sub.add_parser("list")

    sp = sub.add_parser("spawn")
    sp.add_argument("repo")
    sp.add_argument("label")
    sp.add_argument("--kind", default="claude", choices=AGENT_KINDS)
    sp.add_argument("--prompt", default="")
    sp.add_argument("--permission-mode", default="acceptEdits")
    sp.add_argument("--model", default="")

    rp = sub.add_parser("read")
    rp.add_argument("name")

    tp = sub.add_parser("stop")
    tp.add_argument("workspace_id")

    pp = sub.add_parser("prompt")
    pp.add_argument("name")
    pp.add_argument("text")

    args = ap.parse_args()
    try:
        if args.cmd == "status":
            print(json.dumps(status(), indent=2))
        elif args.cmd == "list":
            print(json.dumps(list_agents(), indent=2))
        elif args.cmd == "spawn":
            print(json.dumps(spawn(
                args.repo, args.label, kind=args.kind, prompt=args.prompt,
                permission_mode=args.permission_mode, model=args.model,
            ), indent=2))
        elif args.cmd == "read":
            print(read_agent(args.name))
        elif args.cmd == "stop":
            print(json.dumps(stop(args.workspace_id), indent=2))
        elif args.cmd == "prompt":
            print(json.dumps(prompt_agent(args.name, args.text), indent=2))
    except HerderError as exc:
        print(f"herder: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
