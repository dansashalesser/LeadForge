---
description: Analyze a webpage, post, or idea and plan how to implement useful parts in this repo — no redundancies
allowed-tools: Read, Glob, Grep, Bash, Agent, WebFetch, WebSearch, TodoWrite
argument-hint: <url, pasted content, or idea>
---

# Adapt to Repo

<background_information>
- **Mission**: Take an external source (URL, blog post, article, technique, idea) and produce a concrete, repo-specific implementation plan — but only for items that genuinely add value
- **Success Criteria**:
  - Source content is fully parsed into discrete actionable items
  - Repo's current state is mapped against those items
  - Every item is classified: REDUNDANT, ENHANCE, NEW & VALUABLE, or NEW but NOT USEFUL
  - Only ENHANCE and NEW & VALUABLE items get implementation plans
  - No duplicate work, no tool-chasing, no premature architecture
</background_information>

<instructions>

## Parse Arguments
- Source input: `$ARGUMENTS`

## Validation
- If `$ARGUMENTS` is empty, ask user: "What URL, article, or idea do you want to evaluate for this repo?"

## Invoke Skill

Read `~/.claude/skill-library/adapt-to-repo/SKILL.md` and follow its execution protocol
exactly (the skill lives in the on-demand library, reached by path — not the Skill tool).
Treat `$ARGUMENTS` as its input.

Follow every phase in the skill:

1. **Ingest the Source** — fetch URL or parse pasted content, extract every discrete technique/pattern/tool/practice
2. **Map the Repo** — read project root, scan structure, grep for existing patterns relevant to the source
3. **Gap Analysis** — classify each item as REDUNDANT / ENHANCE / NEW & VALUABLE / NEW but NOT USEFUL
4. **Implementation Plan** — only for ENHANCE and NEW & VALUABLE, with specific files, steps, effort, risk
5. **Present Results** — structured output with counts and clear next steps

## Key Rules
- Default stance is **skeptical**. "Cool" is not "useful."
- If the repo already does something equivalently, it's REDUNDANT — skip it
- Enhancement must be more than cosmetic — real improvement only
- Consider maintenance cost vs. value
- Never recommend swapping equivalent tools
- Adapt ideas to this repo's conventions and scale, don't cargo-cult

## Output
Present the full gap analysis and implementation plan per the skill's Phase 5 format.

### Next Steps Guidance

**If user approves items from the plan**:
- Implement directly, or start a spec: `/kiro:spec-init <item-description>`

**If user wants to evaluate a different source**:
- Re-run: `/kiro:adapt-to-repo <new-url-or-idea>`

</instructions>
