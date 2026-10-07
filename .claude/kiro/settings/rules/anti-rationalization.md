# Anti-Rationalization Framework

## Purpose

Agents tend to rationalize shortcuts when under pressure (time, complexity, ambiguity). This framework catalogs common rationalizations by workflow phase and provides factual counterarguments. Agents must check this framework when they detect hesitation or shortcut temptation.

## How to Use

When an agent considers skipping a step, deferring quality work, or taking a shortcut, consult the relevant phase table below. If the agent's reasoning matches a rationalization pattern, follow the Reality column instead.

## Phase-Specific Rationalizations

### Requirements Phase

| Rationalization | Reality |
|---|---|
| "The user knows what they want" | Users describe solutions, not problems. Requirements exist to surface assumptions before code is written. |
| "This is too simple for formal requirements" | Simple features become complex at boundaries. Requirements catch this early. |
| "We can clarify during implementation" | Ambiguity discovered during coding costs 10x more to fix than ambiguity caught in requirements. |
| "The description is clear enough" | Clear to whom? Requirements make shared understanding explicit and testable. |

### Design Phase

| Rationalization | Reality |
|---|---|
| "We can figure it out during implementation" | Design gaps become bugs. Architecture decisions made under implementation pressure are worse. |
| "This pattern worked before, no need to research" | Context matters. The same pattern in a different system can fail for reasons not obvious without investigation. |
| "Skip the diagrams, the code will be self-documenting" | Diagrams expose integration issues that code reviews miss. They take minutes; the bugs they prevent take hours. |
| "Let's just start coding and refactor later" | Refactoring without a design target produces local improvements but architectural drift. |

### Task Breakdown Phase

| Rationalization | Reality |
|---|---|
| "This is too small to break down" | Unbounded tasks expand to fill available time. Even small work benefits from explicit scope. |
| "I'll just do it all in one task" | Compound tasks hide complexity. When something goes wrong, you can't tell which part failed. |
| "Breaking it down is overhead" | 5 minutes of task breakdown saves hours of scope creep and rework. |
| "The design already covers the breakdown" | Design says WHAT; tasks say HOW MUCH and IN WHAT ORDER. They serve different purposes. |

### Implementation Phase

| Rationalization | Reality |
|---|---|
| "I'll write the tests after the code works" | Tests written after code confirm what you built, not what you should have built. TDD catches design issues. |
| "This is too simple to test" | Simple code in complex systems breaks at integration points. The test isn't for the code — it's for the contract. |
| "I know this works, I just tested it manually" | Manual testing proves it works now, on your machine, with your data. Automated tests prove it keeps working. |
| "Let me just get this working first" | "Working first, clean later" is how technical debt is born. The refactor never comes. |
| "This edge case won't happen in production" | It will. Edge cases that "won't happen" are the #1 source of production incidents. |

### Review Phase

| Rationalization | Reality |
|---|---|
| "The tests pass, so it's fine" | Tests verify expected behavior. Reviews catch unexpected behavior, security issues, and maintainability problems. |
| "It's a small change, no review needed" | Small changes to critical paths cause large outages. Size doesn't correlate with risk. |
| "I wrote it, I can review it" | Research shows generators cannot effectively critique their own work. Fresh eyes find what familiarity hides. |

## Detection Signals

An agent may be rationalizing when it:
- Uses "just" or "quickly" to minimize a step ("let me just skip...")
- Frames process compliance as "overhead" or "unnecessary"
- Assumes future self will do the deferred work
- Appeals to time pressure to justify quality shortcuts
- Claims certainty about outcomes without evidence
