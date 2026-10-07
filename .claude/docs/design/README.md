# Design & Code Quality Reference

Reference documentation for frontend quality integrations in the SDD harness — visual design quality and code-level quality.

## Visual Design Quality

| Integration | Source | Description |
|---|---|---|
| [Impeccable](impeccable/impeccable.md) | [github.com/pbakaus/impeccable](https://github.com/pbakaus/impeccable) | 27 deterministic anti-pattern rules + 7-domain visual design quality system. Catches AI design fingerprints in frontend code (gradient text, glassmorphism, nested cards, contrast failures, stale easing). |

## Code Quality (Skills)

| Skill | Source | Description |
|---|---|---|
| `frontend-code-quality` | [bendc/frontend-guidelines](https://github.com/bendc/frontend-guidelines) | Per-file HTML/CSS/JS code quality rules: semantic HTML, CSS structural patterns (selectors, specificity, units, animations), and JS idioms (pure functions, array methods, composition, const-first). |
| `frontend-performance` | [How is Linear So Fast](https://performance.dev/how-is-linear-so-fast-a-technical-breakdown) | Architecture-level performance decisions: local-first vs server-driven, optimistic updates, bundle/load strategy, service worker, reactivity, animation timing rules. |

These two skills are paired — invoke both when doing frontend work:
- `frontend-performance` → architecture and data-flow decisions
- `frontend-code-quality` → per-file HTML/CSS/JS patterns

## How Frontend Quality Fits in the Workflow

```
spec-requirements → spec-design → /kiro:validate-design → spec-impl
                                         ↑
                              frontend-anti-patterns.md rules
                              (Impeccable's deterministic rules)

UI components → Write/Edit → impeccable-detect-hook (auto-scan)
                          → /impeccable-audit skill (on-demand visual review)
                          → frontend-code-quality skill (on-demand code review)
                          → frontend-performance skill (architecture review)
```

- **`/kiro:validate-design`** — software architecture review; references `frontend-anti-patterns.md` for UI deliverables
- **`/impeccable-audit`** skill — full 7-domain visual audit with PASS/NEEDS WORK/BLOCK verdict
- **`impeccable-detect-hook.sh`** — PostToolUse auto-scan on frontend file writes (requires `npm install -g impeccable@3.6.0`)
- **`frontend-code-quality`** skill — HTML/CSS/JS code quality checklist (invoke during code review)
- **`frontend-performance`** skill — architecture and performance checklist (invoke at design time)
