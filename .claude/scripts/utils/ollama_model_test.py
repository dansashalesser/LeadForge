#!/usr/bin/env python3
"""Run the same prompt against a local Ollama model and save the outputs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, TextIO

OLLAMA_HOST = "http://localhost:11434"
OUTPUT_ROOT = Path("ollama-runs")
TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0
TEMPERATURE_DEFAULT = 0.8
INVALID_TEMPERATURE = object()

# Fields dropped from the saved Ollama metadata: ``context`` is a large array of
# token IDs only useful for resuming a conversation, and ``response`` duplicates
# the answer already written above it as the run output.
SAVED_METADATA_OMIT_KEYS = frozenset({"context", "response"})


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    print("Ollama model tester")
    print("===================")
    print()

    if args.runner is not None:
        if args.model is None:
            print(
                "--model is required when --runner is set "
                "(no model discovery for custom runners).",
                file=sys.stderr,
            )
            return 1
        model = args.model
    else:
        try:
            models = list_ollama_models()
        except RuntimeError as exc:
            print(f"Could not list Ollama models: {exc}", file=sys.stderr)
            print("Make sure Ollama is running, then try again.", file=sys.stderr)
            return 1

        if not models:
            print("No local Ollama models found. Install one with `ollama pull <model>`.")
            return 1

        model = resolve_model(args.model, models)
        if model is None:
            return 1

    prompt = resolve_prompt(args.prompt_file)
    if prompt is None:
        return 1
    if not prompt.strip():
        print("No prompt entered. Exiting.")
        return 1

    runs = resolve_runs(args.runs)
    if runs is None:
        return 1

    temperature = resolve_temperature(args.temperature)
    if temperature is INVALID_TEMPERATURE:
        return 1
    options: dict[str, Any] = {}
    if temperature is not None:
        options["temperature"] = temperature

    stream = resolve_stream(args.stream)

    run_dir = create_prompt_run_dir(prompt)
    prompt_path = run_dir / "prompt.md"
    metadata_path = run_dir / "metadata.json"
    output_path = unique_model_output_path(run_dir, model)

    started_at = now_local()
    if not prompt_path.exists():
        write_prompt_file(prompt_path, prompt, started_at)

    print()
    print(f"Saving results to: {run_dir}")
    print()

    results: list[dict[str, Any]] = []
    for index in range(1, runs + 1):
        print(f"Run {index}/{runs} using {model}...")
        if args.runner is not None:
            result = run_via_custom_runner(args.runner, model, prompt, run_dir)
        else:
            result = generate_once(model, prompt, options, stream=stream)
        results.append(result)
        status = "ok" if result["ok"] else "error"
        elapsed = result["elapsed_seconds"]
        print(f"  {status} in {elapsed:.2f}s")

    finished_at = now_local()
    write_model_output_file(output_path, model, prompt, results, options, stream, started_at)
    append_metadata_run(
        metadata_path,
        {
            "model": model,
            "runs_requested": runs,
            "runs_completed": len(results),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "options": options,
            "stream": stream,
            "files": {
                "model_output": output_path.name,
            },
        },
        prompt,
        prompt_path.name,
    )

    if args.checker is not None:
        grade = run_checker(args.checker, model, run_dir)
        write_grades_file(run_dir / "grades.json", model, grade)
        verdict = "PASS" if grade.get("pass") else "FAIL"
        print()
        print(f"Checker verdict: {verdict} (score={grade.get('score')}) {grade.get('notes', '')}".rstrip())

    print()
    print(f"Done. Wrote {output_path}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the same prompt against a local Ollama model and save the outputs."
    )
    parser.add_argument(
        "--model",
        help="Name of the local Ollama model to use. If omitted, choose interactively.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        help="Number of generations to run. If omitted, enter this interactively.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        help=(
            f"Generation temperature ({TEMPERATURE_MIN:.1f} to {TEMPERATURE_MAX:.1f}). "
            "If omitted, choose interactively or press Enter for Ollama's default."
        ),
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help=(
            "Path to a UTF-8 text file containing the prompt. "
            "If omitted, enter the prompt interactively."
        ),
    )
    parser.add_argument(
        "--runner",
        type=str,
        help=(
            "Path to an executable wrapping a non-Ollama CLI model/agent. Called once per "
            "run with the prompt on stdin and OMT_MODEL/OMT_PROMPT/OMT_RUN_DIR set in its "
            "environment; stdout becomes the recorded response. Requires --model. Omit to "
            "use the built-in Ollama path."
        ),
    )
    parser.add_argument(
        "--checker",
        type=str,
        help=(
            "Path to an executable run once after all generations complete, with "
            "OMT_RUN_DIR/OMT_MODEL set. Its stdout must be JSON: "
            '{"pass": bool, "score": number, "notes": str}. Result is appended to '
            "grades.json in the run directory."
        ),
    )
    stream_group = parser.add_mutually_exclusive_group()
    stream_group.add_argument(
        "--stream",
        dest="stream",
        action="store_true",
        help="Stream responses from /api/generate.",
    )
    stream_group.add_argument(
        "--no-stream",
        dest="stream",
        action="store_false",
        help="Do not stream responses from /api/generate.",
    )
    parser.set_defaults(stream=None)
    return parser.parse_args(argv)


def resolve_model(requested_model: str | None, models: list[str]) -> str | None:
    if requested_model is None:
        return choose_model(models)

    if requested_model in models:
        return requested_model

    print(f"Requested model is not installed locally: {requested_model}", file=sys.stderr)
    print("Available local models:", file=sys.stderr)
    for model in models:
        print(f"  - {model}", file=sys.stderr)
    return None


def resolve_prompt(prompt_file: Path | None) -> str | None:
    if prompt_file is None:
        return read_multiline_prompt()

    try:
        return prompt_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"Could not read prompt file {prompt_file}: {exc}", file=sys.stderr)
        return None


def resolve_runs(requested_runs: int | None) -> int | None:
    if requested_runs is None:
        return ask_positive_int("How many times should this prompt be run? ")

    if requested_runs > 0:
        return requested_runs

    print("--runs must be a positive whole number.", file=sys.stderr)
    return None


def resolve_temperature(requested_temperature: float | None) -> float | object | None:
    if requested_temperature is None:
        return ask_optional_temperature()

    if (
        math.isfinite(requested_temperature)
        and TEMPERATURE_MIN <= requested_temperature <= TEMPERATURE_MAX
    ):
        return requested_temperature

    print(
        "--temperature must be a finite number in the expected range "
        f"({TEMPERATURE_MIN:.1f} to {TEMPERATURE_MAX:.1f}).",
        file=sys.stderr,
    )
    return INVALID_TEMPERATURE


def resolve_stream(requested_stream: bool | None) -> bool:
    if requested_stream is None:
        return ask_yes_no("Stream responses from /api/generate?", default=False)
    return requested_stream


def read_multiline_prompt() -> str:
    print("Enter the prompt. Put /done on its own line when finished.")
    print()
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line.strip() == "/done":
                return "\n".join(lines).strip()
            lines.append(line)
    except KeyboardInterrupt:
        print()
        return ""
    except EOFError:
        return "\n".join(lines).strip()


def list_ollama_models() -> list[str]:
    payload = ollama_get_json("/api/tags")
    models = payload.get("models", [])
    names = [item.get("name") for item in models if item.get("name")]
    return sorted(names, key=str.lower)


def choose_model(models: list[str]) -> str:
    print()
    print("Available local models:")
    for index, model in enumerate(models, start=1):
        print(f"{index}. {model}")
    print()

    while True:
        answer = input("Which model should be used? Enter a number: ").strip()
        if not answer.isdigit():
            print("Enter a model number from the list.")
            continue

        choice = int(answer)
        if 1 <= choice <= len(models):
            return models[choice - 1]

        print("Enter a model number from the list.")


def ask_positive_int(question: str) -> int:
    while True:
        answer = input(question).strip()
        if answer.isdigit() and int(answer) > 0:
            return int(answer)
        print("Enter a positive whole number.")


def ask_optional_float(question: str) -> float | None:
    while True:
        answer = input(question).strip()
        if not answer:
            return None
        try:
            value = float(answer)
        except ValueError:
            print("Enter a number, or press Enter for the default.")
            continue

        if math.isfinite(value):
            return value

        print("Enter a finite number, or press Enter for the default.")


def ask_yes_no(question: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"{question} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False

        print("Enter y or n.")


def ask_optional_temperature() -> float | None:
    question = (
        f"Temperature to use ({TEMPERATURE_MIN:.1f} to {TEMPERATURE_MAX:.1f}), "
        f"or press Enter for Ollama default ({TEMPERATURE_DEFAULT:.1f}): "
    )

    while True:
        temperature = ask_optional_float(question)
        if temperature is None:
            return None
        if TEMPERATURE_MIN <= temperature <= TEMPERATURE_MAX:
            return temperature

        print(
            "Temperature is outside the expected range "
            f"({TEMPERATURE_MIN:.1f} to {TEMPERATURE_MAX:.1f}). Please try again."
        )


def create_prompt_run_dir(prompt: str) -> Path:
    slug = prompt_slug(prompt)
    digest = prompt_hash(prompt)[:8]
    run_dir = OUTPUT_ROOT / f"{slug}_{digest}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _runs_of(value: str, keep) -> list[str]:
    """Split `value` into maximal runs of characters satisfying `keep`."""
    runs: list[str] = []
    current: list[str] = []
    for ch in value:
        if keep(ch):
            current.append(ch)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return runs


def _is_alnum_ascii(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def prompt_slug(prompt: str, max_words: int = 6) -> str:
    words = _runs_of(prompt.lower(), _is_alnum_ascii)
    if not words:
        return "prompt"
    return "-".join(words[:max_words])[:80].strip("-") or "prompt"


def prompt_preview(prompt: str, max_chars: int = 160) -> str:
    compact = " ".join(prompt.split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


_SAFE_NAME_EXTRA = frozenset("._-")


def safe_name(value: str) -> str:
    """Collapse every run of unsafe characters into a single '-'."""
    out: list[str] = []
    previous_was_replacement = False
    for ch in value:
        if _is_alnum_ascii(ch) or ch in _SAFE_NAME_EXTRA:
            out.append(ch)
            previous_was_replacement = False
        elif not previous_was_replacement:
            out.append("-")
            previous_was_replacement = True
    return "".join(out).strip("-") or "model"


def unique_model_output_path(run_dir: Path, model: str) -> Path:
    base_name = safe_name(model)
    path = run_dir / f"{base_name}.md"
    if not path.exists():
        return path

    suffix = 2
    while True:
        path = run_dir / f"{base_name}-{suffix}.md"
        if not path.exists():
            return path
        suffix += 1


def generate_once(
    model: str,
    prompt: str,
    options: dict[str, Any],
    stream: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    generated_at = now_local()

    request_payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": stream,
    }
    if options:
        request_payload["options"] = options

    try:
        if stream:
            response_payload = ollama_post_stream_json("/api/generate", request_payload)
            print()
        else:
            response_payload = ollama_post_json("/api/generate", request_payload)
        elapsed = time.monotonic() - started
        return {
            "ok": True,
            "generated_at": generated_at.isoformat(),
            "elapsed_seconds": elapsed,
            "response": response_payload.get("response", ""),
            "raw": response_payload,
        }
    except RuntimeError as exc:
        if stream:
            print()
        elapsed = time.monotonic() - started
        return {
            "ok": False,
            "generated_at": generated_at.isoformat(),
            "elapsed_seconds": elapsed,
            "error": str(exc),
        }


def run_via_custom_runner(
    runner: str,
    model: str,
    prompt: str,
    run_dir: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    generated_at = now_local()

    env = os.environ.copy()
    env["OMT_MODEL"] = model
    env["OMT_PROMPT"] = prompt
    env["OMT_RUN_DIR"] = str(run_dir)

    try:
        completed = subprocess.run(
            [runner],
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
        )
    except OSError as exc:
        elapsed = time.monotonic() - started
        return {
            "ok": False,
            "generated_at": generated_at.isoformat(),
            "elapsed_seconds": elapsed,
            "error": f"could not run --runner {runner}: {exc}",
        }
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        return {
            "ok": False,
            "generated_at": generated_at.isoformat(),
            "elapsed_seconds": elapsed,
            "error": f"--runner {runner} timed out: {exc}",
        }

    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        return {
            "ok": False,
            "generated_at": generated_at.isoformat(),
            "elapsed_seconds": elapsed,
            "error": (
                f"--runner {runner} exited {completed.returncode}: "
                f"{completed.stderr.strip()}"
            ),
        }

    return {
        "ok": True,
        "generated_at": generated_at.isoformat(),
        "elapsed_seconds": elapsed,
        "response": completed.stdout,
        "raw": {"runner": runner, "returncode": completed.returncode},
    }


def run_checker(checker: str, model: str, run_dir: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["OMT_MODEL"] = model
    env["OMT_RUN_DIR"] = str(run_dir)

    try:
        completed = subprocess.run(
            [checker],
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"pass": False, "score": 0.0, "notes": f"could not run --checker {checker}: {exc}"}

    try:
        grade = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "pass": False,
            "score": 0.0,
            "notes": f"--checker {checker} did not print valid JSON: {exc}",
        }

    if not isinstance(grade, dict) or "pass" not in grade:
        return {
            "pass": False,
            "score": 0.0,
            "notes": f"--checker {checker} JSON missing required 'pass' key",
        }

    return grade


def write_grades_file(path: Path, model: str, grade: dict[str, Any]) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    else:
        existing = {}

    grades = existing.get("grades")
    if not isinstance(grades, list):
        grades = []

    grades.append(
        {
            "model": model,
            "graded_at": now_local().isoformat(),
            "pass": grade.get("pass"),
            "score": grade.get("score"),
            "notes": grade.get("notes"),
        }
    )

    path.write_text(json.dumps({"grades": grades}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ollama_get_json(path: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{OLLAMA_HOST}{path}", method="GET")
    return open_json_request(request)


def ollama_post_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_HOST}{path}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    return open_json_request(request)


def ollama_post_stream_json(
    path: str,
    payload: dict[str, Any],
    response_output: TextIO | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_HOST}{path}",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    if response_output is None:
        response_output = sys.stdout

    response_buffer = io.StringIO()
    final_chunk: dict[str, Any] | None = None
    saw_chunk = False
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            for line_number, raw_line in enumerate(response, start=1):
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"invalid streaming JSON response on line {line_number}: {line[:200]}"
                    ) from exc

                if not isinstance(chunk, dict):
                    raise RuntimeError(f"streaming JSON line {line_number} was not an object")

                saw_chunk = True
                response_text = chunk.get("response")
                if isinstance(response_text, str) and response_text:
                    response_buffer.write(response_text)
                    response_output.write(response_text)
                    response_output.flush()

                final_chunk = chunk
                if chunk.get("done"):
                    break
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    except TimeoutError as exc:
        raise RuntimeError("request timed out") from exc

    if not saw_chunk or final_chunk is None:
        raise RuntimeError("empty streaming response")

    stream_payload = dict(final_chunk)
    stream_payload["response"] = response_buffer.getvalue()
    return stream_payload


def open_json_request(request: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    except TimeoutError as exc:
        raise RuntimeError("request timed out") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response: {body[:200]}") from exc


def write_prompt_file(path: Path, prompt: str, started_at: dt.datetime) -> None:
    path.write_text(
        "\n".join(
            [
                "# Prompt",
                "",
                f"Date: {started_at.isoformat()}",
                f"Prompt hash: `{prompt_hash(prompt)}`",
                "",
                markdown_fence_block(prompt, "text"),
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_model_output_file(
    path: Path,
    model: str,
    prompt: str,
    results: list[dict[str, Any]],
    options: dict[str, Any],
    stream: bool,
    started_at: dt.datetime,
) -> None:
    lines = [
        "# Ollama Model Test",
        "",
        f"Model: `{model}`",
        f"Date: {started_at.isoformat()}",
        f"Prompt hash: `{prompt_hash(prompt)}`",
        f"Runs: {len(results)}",
        f"Options: `{json.dumps(options, sort_keys=True)}`",
        f"Streaming: `{stream}`",
        "",
        "## Prompt",
        "",
        markdown_fence_block(prompt, "text"),
        "",
    ]

    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                f"## Run {index}",
                "",
                f"Generated at: {result['generated_at']}",
                f"Elapsed seconds: {result['elapsed_seconds']:.2f}",
                f"Status: {'ok' if result['ok'] else 'error'}",
                "",
            ]
        )
        if result["ok"]:
            metadata_json = json.dumps(saved_metadata(result["raw"]), indent=2, sort_keys=True)
            lines.extend(
                [
                    result["response"].rstrip(),
                    "",
                    "### Ollama metadata",
                    "",
                    markdown_fence_block(metadata_json, "json"),
                    "",
                ]
            )
        else:
            lines.extend(["```text", result["error"], "```", ""])

    path.write_text("\n".join(lines), encoding="utf-8")


def markdown_fence_block(value: str, language: str = "") -> str:
    longest_backtick_run = max((len(run) for run in _runs_of(value, "`".__eq__)), default=0)
    fence = "`" * max(3, longest_backtick_run + 1)
    suffix = language if language else ""
    return f"{fence}{suffix}\n{value}\n{fence}"


def saved_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key not in SAVED_METADATA_OMIT_KEYS}


def append_metadata_run(
    path: Path,
    run_payload: dict[str, Any],
    prompt: str,
    prompt_file_name: str,
) -> None:
    if path.exists():
        try:
            metadata = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            metadata = {}
    else:
        metadata = {}

    runs = metadata.get("runs")
    if not isinstance(runs, list):
        previous_run = legacy_metadata_as_run(metadata)
        runs = [previous_run] if previous_run else []

    metadata = {
        "prompt_hash": prompt_hash(prompt),
        "prompt_preview": prompt_preview(prompt),
        "files": {
            "prompt": prompt_file_name,
        },
        "runs": runs,
    }
    metadata["runs"].append(run_payload)

    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def legacy_metadata_as_run(metadata: dict[str, Any]) -> dict[str, Any] | None:
    required_keys = [
        "model",
        "runs_requested",
        "runs_completed",
        "started_at",
        "finished_at",
        "options",
    ]
    if not all(key in metadata for key in required_keys):
        return None

    files = metadata.get("files")
    model_output = files.get("model_output") if isinstance(files, dict) else None
    run_payload = {
        "model": metadata["model"],
        "runs_requested": metadata["runs_requested"],
        "runs_completed": metadata["runs_completed"],
        "started_at": metadata["started_at"],
        "finished_at": metadata["finished_at"],
        "options": metadata["options"],
        "files": {},
    }
    if model_output:
        run_payload["files"]["model_output"] = model_output
    return run_payload


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


if __name__ == "__main__":
    raise SystemExit(main())
