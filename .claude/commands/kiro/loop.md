desc: Launch a named agentic dev loop (ship-pr, self-review, de-sloppify, spec-first, build, coverage, e2e, pre-commit, fresh-clone, feedback-sweep). No arg = show picker.
allowed-tools: Read, Bash
argument-hint: [loop-slug]

# Loop Runner

Runs a named loop from the `loop-patterns` library. Generates and executes the kickoff prompt automatically.

## Parse Arguments

- If `$ARGUMENTS` is empty → show picker (Phase 1)
- If `$ARGUMENTS` matches a slug → run that loop directly (Phase 2)

## Phase 1: Picker (no argument)

Show this menu and ask the user to pick:

```
Available loops:

  ship-pr        Ship PR Until Green — implement, push, wait for CI, loop until green
  de-sloppify    De-Sloppify Pass — clean up recent changes (lint/conventions/dead code)
  spec-first     Spec-First Ship — implement every unchecked item in spec.md
  build          Build Until Green — fix compile/bundle errors until build exits 0
  coverage       Coverage Until Threshold — add tests until coverage ≥ 80%
  e2e            E2E Until Green — fix E2E failures until suite passes
  self-review    PR Self-Review — three senior-level review passes on current diff
  pre-commit     Pre-Commit Guard — run tests before committing; never commit red
  fresh-clone    Fresh-Clone Onboarding — verify setup docs from a clean disposable checkout
  feedback-sweep Recent-Feedback Sweep — turn one correction into a project-wide fix + guard

Which loop? (type the slug)
```

Once the user replies with a slug, continue to Phase 2.

## Phase 2: Generate & Launch Kickoff Prompt

Map slug → kickoff prompt and execute it directly:

### ship-pr
```
Start the "Ship PR Until Green" loop.

Goal: PR is open with all CI checks passing
Max iterations: 10
Between iterations run: gh pr checks
Exit when: all PR checks are success

Step 1: Implement the change, test locally, push, open PR, and fix CI until green.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

### de-sloppify
```
Start the "De-Sloppify Pass" loop.

Goal: recent changes clean, minimal, convention-aligned
Max iterations: 4
Between iterations run: npm run lint && npm test
Exit when: review finds no slop and checks pass

Step 1: Review the recent diff for debug code, dead code, naming issues, convention violations. Fix what you find, then run the check.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

### spec-first
```
Start the "Spec-First Ship" loop.

Goal: every requirement in spec.md is implemented and checked off
Max iterations: 15
Between iterations run: npm test
Exit when: spec.md has no unchecked requirements

Step 1: Read spec.md, identify the first unchecked requirement, implement it, check it off, run tests.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

### build
```
Start the "Build Until Green" loop.

Goal: production build succeeds with no compile or bundling errors
Max iterations: until done
Between iterations run: npm run build
Exit when: build exits 0

Step 1: Run the build. If it fails, fix the first error, then repeat until green.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

### coverage
```
Start the "Coverage Until Threshold" loop.

Goal: test coverage meets threshold (80%) without changing production behavior
Max iterations: until done
Between iterations run: npm test --coverage
Exit when: coverage threshold is met

Step 1: Run coverage report, identify the lowest-covered module, add focused tests for it.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

### e2e
```
Start the "E2E Until Green" loop.

Goal: end-to-end test suite passes
Max iterations: until done
Between iterations run: npm run test:e2e
Exit when: E2E exits 0

Step 1: Run E2E. Fix the first failing test (UI or integration), re-run to confirm, then continue.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

### self-review
```
Start the "PR Self-Review" loop.

Goal: three clean self-review passes with no critical findings
Max iterations: 3
Between iterations run: git diff main...HEAD
Exit when: three passes complete with no critical findings

Step 1: Read the full diff. Review for correctness bugs, missing tests, security issues, naming problems. Fix what you find.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

### pre-commit
```
Start the "Pre-Commit Guard" loop.

Goal: tests pass before committing — never commit with a red suite
Max iterations: until green
Between iterations run: npm test
Exit when: tests exit 0

Step 1: Run the test suite. If red, fix the failures before committing.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

### fresh-clone
```
Start the "Fresh-Clone Onboarding" loop.

Goal: a new dev reaches a running project using only the README/install docs — no hidden setup assumptions
Max iterations: 5
Between iterations run: in a fresh disposable temp dir, clone/copy the repo, follow ONLY the documented setup steps, then run the smoke check (build/test/start)
Exit when: a from-clean run succeeds with zero steps you had to improvise outside the docs

Step 1: Act as a first-time user in a clean temp dir. Follow the docs literally. The first time you must do something the docs don't say, STOP, fix the docs (not your environment) to cover it, then retry from a fresh dir.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

### feedback-sweep
```
Start the "Recent-Feedback Sweep" loop.

Goal: turn a single recent correction into a project-wide fix + regression guard
Max iterations: 4
Between iterations run: search the codebase for other instances of the same anti-pattern, then run the test suite
Exit when: zero remaining instances AND a test/lint rule guards against reintroduction

Step 1: State the correction as a falsifiable rule (what was wrong, what is right). Search the whole project for that class of mistake. Fix the highest-impact instance, add a regression guard (test or lint rule) for it, then re-search.

Self-pace this loop. After each iteration, run the check command, read the output, and only continue if the exit condition is not met. Stop when the exit condition passes or max iterations is reached. Give a short status update each pass.
```

## Notes

- Adapt `npm` commands for the project stack (see `~/.claude/skill-library/loop-patterns/SKILL.md` for equivalents: pytest, go test, cargo test)
- For loops using `spec.md`: confirm the spec path exists first
- After loop completes: run `/kiro:verify` to confirm the exit condition is genuinely met
