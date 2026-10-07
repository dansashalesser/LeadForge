# Karpathy's 4 Rules

Distilled from Andrej Karpathy's original CLAUDE.md guidance. These four rules address the most common failure modes in AI-assisted coding. Apply them universally, without exception.

## The Rules

**1. Ask, don't assume.**
If anything is unclear — intent, architecture, requirements, edge cases — ask before writing a single line. Silent assumptions compound: one wrong assumption leads to code that looks correct but solves the wrong problem.

**2. Simplest solution first.**
Always implement the simplest thing that could work. Do not add abstractions, generalization, or flexibility that weren't explicitly requested. Over-engineering is harder to review, harder to revert, and harder to extend correctly later.

**3. Don't touch unrelated code.**
If a file or function is not directly part of the current task, do not modify it — even if it looks like it could be improved. Scope creep in AI-assisted code is invisible until review. Flag improvements; don't make them.

**4. Flag uncertainty explicitly.**
If you are not confident about an approach or technical detail, say so before proceeding. Confidence without certainty causes more damage than admitting a gap. One uncertain line of code can cascade into hours of debugging.

## Failure Modes These Address

| Rule | What goes wrong without it |
|------|---------------------------|
| Ask, don't assume | Implement the wrong thing with high confidence |
| Simplest first | Introduce abstractions that break existing architecture |
| Don't touch unrelated | Refactor code the user didn't ask to touch |
| Flag uncertainty | Hallucinate APIs, syntax, or behavior that compiles but fails at runtime |
