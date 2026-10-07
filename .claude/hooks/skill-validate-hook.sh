#!/bin/bash
# Skill quality gate: validates frontmatter before writing skill files.
# Hard-blocks (exit 2) on missing/mismatched name or description.
# Warns (exit 0) on vague description phrasing.

set -euo pipefail

EVENT=$(cat)

TMPFILE=$(mktemp /tmp/skill-validate-XXXXXX.py)
trap 'rm -f "$TMPFILE"' EXIT

cat > "$TMPFILE" << 'PYTHON_END'
import hashlib, json, sys, re, pathlib

try:
    e = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)

# Only validate Write operations
if e.get('tool_name', '') != 'Write':
    sys.exit(0)

inp = e.get('tool_input', {})
file_path = inp.get('file_path', inp.get('path', ''))
content = inp.get('content', '')

if not file_path or not content:
    sys.exit(0)

skills_dir = pathlib.Path.home() / '.claude' / 'skills'
p = pathlib.Path(file_path)

# ── Provenance scan ───────────────────────────────────────────────────────
# A skill that tells an agent to load its instructions from a URL is a
# supply-chain vector: the remote file can change after you reviewed it, and
# nothing in the frontmatter check below can see that.
#
# Substring tests only — no regex. Pattern-matching text is banned repo-wide,
# and a hand-rolled URL matcher is exactly the almost-right parser that ban
# exists to prevent.
ADOPT_VERBS   = ('set up ', 'setup ', 'install', 'read ', 'fetch ', 'load ', 'follow ')
INSTALL_VERBS = ('curl ', 'wget ', '| sh', '|sh', '| bash', '|bash', 'npx ')
REMOTE_DOC_SUFFIXES = ('.md', '.txt', '.json', '.yaml', '.yml')


def first_url(line):
    """First http(s) token on the line, stripped of surrounding punctuation."""
    spaced = line.replace('(', ' ').replace(')', ' ').replace('[', ' ').replace(']', ' ')
    for word in spaced.split():
        if word.startswith('http://') or word.startswith('https://'):
            return word.strip('.,;:`"\'<>')
    return ''


def provenance_findings(text):
    found = []
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if 'http://' not in low and 'https://' not in low:
            continue
        url = first_url(line)
        if not url:
            continue
        url_low = url.lower()
        if any(url_low.endswith(s) for s in REMOTE_DOC_SUFFIXES) and \
                any(v in low for v in ADOPT_VERBS):
            found.append(
                f'remote instruction source: {url} — this skill directs an agent to '
                'load instructions from a URL. The file can change after you review it. '
                'Vendor the content into the repo, or pin a specific commit/tag.'
            )
        elif any(v in low for v in INSTALL_VERBS):
            found.append(
                f'remote install: {url} — fine if you vetted it, but the skill carries no '
                'record of who published it or what was reviewed. Note the source and the '
                'version you checked.'
            )
    return found


# ── Eval-verdict staleness ────────────────────────────────────────────────
# A PASS verdict from skill-eval-gate is a statement about one specific set of
# instructions. Every later edit invalidates it, and nothing re-runs the gate on
# its own, so an augmented skill keeps shipping a verdict it no longer earned.
# skill-eval-gate Phase 6 records the sha256 of the SKILL.md it measured;
# comparing that to the incoming content is the only way to notice the drift.
#
# Hash, not date: a skill edited an hour after its eval has a same-day verdict
# that means nothing.
def eval_verdict_findings(path_obj, text):
    if path_obj.name != 'SKILL.md':
        return []

    verdict_file = path_obj.parent / 'eval-verdict.json'

    if not verdict_file.exists():
        # A skill being created for the first time has not reached the gate yet —
        # warning there would fire on every new skill and train the warning out.
        # Only an EXISTING skill missing a verdict is a finding.
        if not path_obj.exists():
            return []
        return [
            'no eval-verdict.json beside this SKILL.md — this skill has no recorded '
            'PASS from skill-eval-gate, so its instructions have never been measured '
            'against a no-skill baseline. Run Skill("skill-eval-gate") before finalizing.'
        ]

    try:
        recorded = json.loads(verdict_file.read_text())
    except (OSError, ValueError):
        return [
            f'eval-verdict.json at {verdict_file} could not be read — treat this skill '
            'as unevaluated rather than assuming it passed.'
        ]

    want = recorded.get('skill_md_sha256', '')
    if not want:
        return [
            f'eval-verdict.json at {verdict_file} records no skill_md_sha256, so there is '
            'no way to tell which instructions it describes. Re-run Skill("skill-eval-gate").'
        ]

    if want != hashlib.sha256(text.encode('utf-8')).hexdigest():
        return [
            f'SKILL.md changed since its {recorded.get("verdict", "?")} verdict of '
            f'{recorded.get("date", "unknown date")} — that result describes different '
            'instructions. Re-run Skill("skill-eval-gate") and rewrite eval-verdict.json, '
            'or state plainly that this edit ships unmeasured.'
        ]

    return []


prov_warnings = provenance_findings(content) if p.name == 'SKILL.md' else []
prov_warnings = prov_warnings + eval_verdict_findings(p, content)

try:
    rel = p.relative_to(skills_dir)
    parts = rel.parts
except ValueError:
    # Not under ~/.claude/skills — frontmatter rules do not apply, but a SKILL.md
    # written anywhere still carries the same provenance risk.
    if prov_warnings:
        print('╔══ Skill Provenance — WARNING ════════════════════════════════════╗')
        print('╚══════════════════════════════════════════════════════════════════╝')
        for w in prov_warnings:
            print(f'  △  {w}')
        print()
    sys.exit(0)

# Match: ~/.claude/skills/<name>/SKILL.md  or  ~/.claude/skills/<name> (flat)
if len(parts) == 2 and parts[1] == 'SKILL.md':
    expected_slug = parts[0]
elif len(parts) == 1:
    expected_slug = parts[0]
else:
    sys.exit(0)

errors = []
warnings = list(prov_warnings)

if not content.strip().startswith('---'):
    errors.append('Missing YAML frontmatter — file must start with ---')
else:
    fm_parts = content.split('---', 2)
    if len(fm_parts) < 3:
        errors.append('Malformed frontmatter — missing closing ---')
    else:
        fm = fm_parts[1]

        # Validate name
        name_m = re.search(r'^name:\s*(.+)$', fm, re.MULTILINE)
        if not name_m:
            errors.append("Missing 'name:' field in frontmatter")
        else:
            name_val = name_m.group(1).strip().strip('"\'')
            if not re.match(r'^[a-z][a-z0-9-]*$', name_val):
                errors.append(f"name '{name_val}' must be kebab-case (lowercase letters and hyphens only)")
            elif name_val != expected_slug:
                errors.append(f"name '{name_val}' does not match file path slug '{expected_slug}'")

        # Validate description (single-line or block scalar >)
        desc_val = None
        inline_m = re.search(r'^description:\s*(.+)$', fm, re.MULTILINE)
        block_m = re.search(r'^description:\s*>\n((?:[ \t]+.+\n?)+)', fm, re.MULTILINE)

        if inline_m:
            desc_val = inline_m.group(1).strip().strip('"\'')
        elif block_m:
            desc_val = ' '.join(block_m.group(1).split()).strip()

        if desc_val is None:
            errors.append("Missing 'description:' field in frontmatter")
        elif len(desc_val) < 25:
            errors.append(
                f"description too short ({len(desc_val)} chars) — "
                "include trigger phrases so Claude knows when to activate this skill"
            )
        else:
            vague_starters = ['a skill that', 'this skill', 'skill for', 'use this skill', 'provides']
            for v in vague_starters:
                if desc_val.lower().startswith(v):
                    warnings.append(
                        f'description starts with "{v}" — '
                        'lead with trigger phrases or concrete use cases instead'
                    )
                    break

if not errors and not warnings:
    sys.exit(0)

if errors:
    print('╔══ Skill Quality Gate — BLOCKED ══════════════════════════════════╗')
    print('║  Fix these errors before writing the skill file.                ║')
    print('╚══════════════════════════════════════════════════════════════════╝')
    print()
    print('  Errors (must fix):')
    for err in errors:
        print(f'    ✗  {err}')
    if warnings:
        print()
        print('  Warnings (should also fix):')
        for w in warnings:
            print(f'    △  {w}')
    print()
    print('  Required frontmatter format:')
    print('    ---')
    print(f'    name: {expected_slug}')
    print('    description: <30+ chars — start with WHEN to use, not WHAT it is>')
    print('    ---')
    print()
    print('  Do NOT proceed with this write. Fix the content and retry.')
    sys.exit(2)
else:
    print('╔══ Skill Quality Gate — WARNING ══════════════════════════════════╗')
    print('╚══════════════════════════════════════════════════════════════════╝')
    for w in warnings:
        print(f'  △  {w}')
    print()
    print('  Proceed is allowed, but vague descriptions reduce trigger accuracy.')
    sys.exit(0)
PYTHON_END

echo "$EVENT" | python3 "$TMPFILE"
