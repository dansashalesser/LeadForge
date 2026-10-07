---
description: Launch readiness check — coordinates verification, production validation, and rollout planning
allowed-tools: Read, Agent, Glob, SlashCommand
argument-hint: [feature-name]
---

# Ship / Launch Readiness

## Parse Arguments
- Feature name: `$ARGUMENTS` (optional — if omitted, checks general project readiness)

## Execution Steps

### Step 0: Clean the Diff

Run `/simplify` to reduce noise before verification and review:
```
/simplify
```

If `/simplify` is not available, skip and proceed to Step 1.

### Step 1: Run Verification Pipeline

Execute the full pre-PR verification first:
```
/kiro:verify pre-pr
```

If verification fails, stop and report failures. Ship readiness requires passing verification.

### Step 2: Run Production Validation

Delegate to the existing production validation:

```
Agent(
  subagent_type="validate-production-agent",
  description="Production readiness scan",
  prompt="""
  {if feature_name}: Feature: {feature_name}. Scan specs/{feature_name}/ for context and all changed files.
  {else}: Scan recent changes via git diff origin/main..HEAD.

  Run the full production readiness checklist and return the structured report.
  """
)
```

### Step 3: Invoke Ship Agent for Rollout Planning

```
Agent(
  subagent_type="ship-agent",
  description="Rollout and launch planning",
  prompt="""
  Feature: {$ARGUMENTS or 'general release'}

  Load `.claude/steering/tech.md` for deployment context.
  Read the production validation report from Step 2.
  Generate a rollout plan with decision thresholds and rollback procedure.
  """
)
```

## Display Result

Combine all three reports into a launch readiness summary.

### Next Steps Guidance

**If READY (all green)**:
- Proceed with deployment following the rollout plan
- Monitor decision thresholds during staged rollout

**If NOT READY (blockers found)**:
- Fix critical issues from verification or production validation
- Re-run `/kiro:ship` after fixes

**If NEEDS HUMAN ATTESTATION**:
- Review the human checklist items (H1-H9 from production validation)
- Acknowledge each item before proceeding
