# Impeccable — Frontend Design Quality Integration

> Design anti-pattern detection and visual quality enforcement for AI-generated UI code.

## What It Is

[Impeccable](https://github.com/pbakaus/impeccable) (25.6k ⭐) is a design quality system built specifically to counter the aesthetic and functional flaws AI coding assistants routinely produce. It provides:

- **27 deterministic anti-pattern rules** — machine-checkable violations (contrast failures, gradient text, glassmorphism, nested cards, etc.)
- **12 LLM critique rules** — subjective quality checks (typography hierarchy, spatial breathing, motion coherence)
- **7 design reference domains** — Typography, Color & Contrast, Spatial Design, Motion, Interaction, Responsive, UX Writing
- **CLI tool** — `impeccable detect <file>` for standalone scanning

## How It's Integrated into the Harness

| Integration | Location | Purpose |
|---|---|---|
| `impeccable-audit` skill | `~/.claude/skill-library/impeccable-audit/SKILL.md` | Load Impeccable's full rule set for design reviews |
| `frontend-anti-patterns.md` rule | `kiro/settings/rules/frontend-anti-patterns.md` | Deterministic enforcement in `/kiro:validate-design` and adversarial agent |
| `impeccable-detect-hook.sh` | `.claude/hooks/impeccable-detect-hook.sh` | Auto-scan frontend files on Write/Edit (requires CLI install) |

## Using the Skill

Invoke `/impeccable-audit` when:
- Reviewing a UI component before committing
- Writing a new frontend page or design system component
- Auditing existing UI for the "AI look"
- Checking accessibility compliance

```
/impeccable-audit  [component name or "focus: motion"]
```

The skill audits across all 7 domains and outputs a structured report with PASS / NEEDS WORK / BLOCK verdict.

## CLI Setup (for the hook)

The PostToolUse hook runs `impeccable detect` automatically after writing frontend files. It requires a one-time install, pinned to the version `install.sh` installs (`IMPECCABLE_VERSION`) — npm 10 has no min-release-age cooldown, so the pin is the only supply-chain guard here:

```bash
npm install -g impeccable@3.6.0
```

Verify: `impeccable --version`

Once installed, the hook silently scans `.tsx`, `.jsx`, `.css`, `.scss`, `.less`, `.vue`, `.svelte`, `.html` files on every Write/Edit and surfaces violations before the next action.

If the CLI is not installed, the hook exits silently — no noise, no blocking.

## Key Anti-Patterns Caught

These are the most common AI-generated UI fingerprints Impeccable flags:

| Pattern | Symptom | Fix |
|---|---|---|
| Gradient text | `background-clip: text` | Use solid color |
| Glassmorphism | `backdrop-filter: blur()` | Use solid surface |
| Colored left border | `border-left: 4px solid accent` | Use padding/spacing |
| Identical card grid | 3-col, same size, same structure | Vary layout |
| Nested cards | Card inside card | Flatten hierarchy |
| Pure white background | `#ffffff` | Use `#fafaf8` or warm off-white |
| Gray on color | Gray text on colored bg | Dark on light / light on dark |
| Stale easing | `ease-in`, `ease-out` | `cubic-bezier` or `ease-in-out` |

## Design Principles Reference

### Typography
- Body: 16px, 1.6 line-height, clean sans-serif
- Display: italic serif at true display size
- Max 2 font families per page

### Color
- 1 accent color, ≤10% of screen real estate
- No pure white backgrounds — warm off-white only
- WCAG AA minimum (4.5:1 for body, 3:1 for large)

### Surfaces
- Shadow alpha ≤ 0.15
- Border radius ≤ 8px on containers
- Flat surfaces over glassmorphism

### Motion
- Micro-interactions: 100–200ms
- Always use `cubic-bezier` or `ease-in-out`
- Respect `prefers-reduced-motion`

## Relationship to Existing Harness Quality Gates

Impeccable's rules slot **after** software design validation and **before** production readiness:

```
spec-requirements → spec-design → /kiro:validate-design → spec-impl
                                         ↑
                              includes frontend-anti-patterns.md
                              (Impeccable's deterministic rules)

UI components → Write/Edit → impeccable-detect-hook (auto)
                          → /impeccable-audit (on-demand)
```

The `frontend-anti-patterns.md` rule file is referenced by `/kiro:validate-design` for any spec that includes UI deliverables.

## Source & License

- GitHub: https://github.com/pbakaus/impeccable
- License: Apache 2.0
- Integrated: 2026-05-07
