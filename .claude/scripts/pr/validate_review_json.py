#!/usr/bin/env python3
"""Validates review.json against the schema documented in
skills/gitnexus-pr-review/SKILL.md's "Structured Output Contract" section.

Usage: validate_review_json.py <path-to-review.json> [pr_number]
Exit 0 and silent on success. Exit 1 with one error per line on failure —
never partial-fixes the file, only reports what's wrong so the caller (a
skill invocation) can correct it and re-run.

When pr_number is given, also runs a line-anchor sanity check: each comment's
path/line/side must point at a location that actually exists in the PR's
current diff. A generation pass and the posting step can straddle a force-push
or an amended commit; without this check a comment anchored to a line that no
longer exists in the diff either gets silently dropped by GitHub or, worse,
lands on the wrong line. Requires `gh`; fails loud (not skipped) if the diff
can't be fetched, since a comment can't be trusted to post correctly without it.
"""
import json
import re
import subprocess
import sys

SEVERITY_PREFIXES = ("CRITICAL:", "IMPORTANT:", "SUGGESTION:", "NIT:")
VERDICTS = ("APPROVE", "REJECT")
RECOMMENDATIONS = ("APPROVE", "REQUEST CHANGES", "NEEDS DISCUSSION")
SIDES = ("LEFT", "RIGHT")

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")


def fetch_pr_diff(pr_number):
    """Returns the PR's unified diff text via `gh`, or raises RuntimeError."""
    try:
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number), "--patch"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"could not run `gh pr diff {pr_number}`: {e}") from e
    if result.returncode != 0:
        raise RuntimeError(
            f"`gh pr diff {pr_number}` exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


def parse_diff_anchors(diff_text):
    """Maps (path, side) -> set of valid line numbers from a unified diff.

    RIGHT (new file) anchors are context + added lines, numbered by the new-file
    counter. LEFT (old file) anchors are context + removed lines, numbered by the
    old-file counter — matching the [OLD:n]/[NEW:n] annotation convention that
    gitnexus-pr-review's Output Contract requires comments to be derived from.
    """
    anchors = {}
    current_path = None
    old_line = new_line = None

    for line in diff_text.splitlines():
        m = DIFF_FILE_RE.match(line)
        if m:
            current_path = m.group(1)
            continue
        m = HUNK_HEADER_RE.match(line)
        if m:
            old_line, new_line = int(m.group(1)), int(m.group(2))
            continue
        if current_path is None or old_line is None:
            continue
        key_right = (current_path, "RIGHT")
        key_left = (current_path, "LEFT")
        if line.startswith("+") and not line.startswith("+++"):
            anchors.setdefault(key_right, set()).add(new_line)
            new_line += 1
        elif line.startswith("-") and not line.startswith("---"):
            anchors.setdefault(key_left, set()).add(old_line)
            old_line += 1
        elif line.startswith(" "):
            anchors.setdefault(key_right, set()).add(new_line)
            anchors.setdefault(key_left, set()).add(old_line)
            old_line += 1
            new_line += 1
        # Lines like "\ No newline at end of file" advance neither counter.

    return anchors


def check_anchors(comments, anchors):
    errors = []
    for i, c in enumerate(comments):
        path, side, line = c.get("path"), c.get("side"), c.get("line")
        if (path, side) not in anchors or line not in anchors[(path, side)]:
            errors.append(
                f"comments[{i}]: {path}:{line} ({side}) is not present in the "
                f"current PR diff — stale or hallucinated anchor, drop or re-derive it"
            )
        start_path = c.get("path")
        start_side, start_line = c.get("start_side"), c.get("start_line")
        if (start_path, start_side) not in anchors or start_line not in anchors[(start_path, start_side)]:
            errors.append(
                f"comments[{i}]: start {start_path}:{start_line} ({start_side}) is not "
                f"present in the current PR diff — stale or hallucinated anchor"
            )
    return errors


def validate(data):
    errors = []

    if data.get("verdict") not in VERDICTS:
        errors.append(f"verdict must be one of {VERDICTS}, got {data.get('verdict')!r}")

    body = data.get("body")
    if not isinstance(body, dict):
        errors.append("body must be an object")
    else:
        if not isinstance(body.get("overview"), str) or not body.get("overview"):
            errors.append("body.overview must be a non-empty string")
        if not isinstance(body.get("concerns"), list):
            errors.append("body.concerns must be an array")
        if not isinstance(body.get("issue_count"), int):
            errors.append("body.issue_count must be an integer")
        if body.get("recommendation") not in RECOMMENDATIONS:
            errors.append(
                f"body.recommendation must be one of {RECOMMENDATIONS}, "
                f"got {body.get('recommendation')!r}"
            )

    comments = data.get("comments")
    if not isinstance(comments, list):
        errors.append("comments must be an array")
    else:
        for i, c in enumerate(comments):
            if not isinstance(c, dict):
                errors.append(f"comments[{i}] must be an object")
                continue
            if not isinstance(c.get("path"), str) or not c.get("path"):
                errors.append(f"comments[{i}].path must be a non-empty string")
            if not isinstance(c.get("line"), int):
                errors.append(f"comments[{i}].line must be an integer")
            if c.get("side") not in SIDES:
                errors.append(f"comments[{i}].side must be one of {SIDES}")
            if not isinstance(c.get("start_line"), int):
                errors.append(f"comments[{i}].start_line must be an integer")
            if c.get("start_side") not in SIDES:
                errors.append(f"comments[{i}].start_side must be one of {SIDES}")
            body_text = c.get("body")
            if not isinstance(body_text, str) or not body_text.startswith(SEVERITY_PREFIXES):
                errors.append(
                    f"comments[{i}].body must start with one of {SEVERITY_PREFIXES}"
                )

    return errors


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: validate_review_json.py <path-to-review.json> [pr_number]", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    pr_number = sys.argv[2] if len(sys.argv) == 3 else None
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read/parse {path}: {e}", file=sys.stderr)
        sys.exit(1)

    errors = validate(data)

    if not errors and pr_number is not None and isinstance(data.get("comments"), list):
        try:
            anchors = parse_diff_anchors(fetch_pr_diff(pr_number))
        except RuntimeError as e:
            print(f"error: anchor check: {e}", file=sys.stderr)
            sys.exit(1)
        errors.extend(check_anchors(data["comments"], anchors))

    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
