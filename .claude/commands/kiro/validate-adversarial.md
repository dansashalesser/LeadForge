---
description: Three-pass adversarial validation with asymmetric scoring — higher confidence than standard validation
allowed-tools: Read, Task, Glob, Grep
---

# Kiro Validate Adversarial — High-Confidence Review

## Usage

```
/kiro:validate-adversarial {feature} [design|impl]
```

- `feature`: Name of the spec feature to validate
- `design` (default) or `impl`: What to validate

## Determine Target

If no target specified, auto-detect:
- If `specs/{feature}/tasks.md` has `[x]` completed tasks → validate `impl`
- Otherwise → validate `design`

## Load Context

Read spec files to pass to the agent:
- `specs/{feature}/spec.json`
- `specs/{feature}/requirements.md`
- `specs/{feature}/design.md`
- `specs/{feature}/tasks.md` (if validating impl)

## Invoke Subagent

Delegate to the adversarial validation agent:

```
Task(
  subagent_type="validate-adversarial-agent",
  description="Adversarial validation of {feature} {target}",
  prompt="""
Validate the {target} for feature "{feature}".

Spec directory: specs/{feature}/
Target: {target}
Steering: .claude/steering/*.md

Execute the three-pass adversarial protocol:
1. Neutral assessment (5-7 findings)
2. Adversarial refutation (evidence-based)
3. Judge synthesis with +1/-2 scoring

Report GO / CONDITIONAL GO / NO-GO with net score.
"""
)
```

## Display Result

Show the adversarial validation report including:
- Scoring table with all findings
- Net score and decision
- Surviving concerns (if any)
- Confirmed strengths

## Append Trace

After receiving the agent result, append to `.claude/memory/trace.log`:
```
YYYY-MM-DD HH:MM | validate-adversarial | opus | {outcome} | medium
```

## Notes

- Use this for high-stakes validations where standard validate-design/validate-impl isn't sufficient
- The three-pass approach eliminates confirmation bias
- Asymmetric scoring means concerns carry double weight — false negatives are costlier than false positives
- This is a Tier 1 (Opus) agent due to the judgment required
