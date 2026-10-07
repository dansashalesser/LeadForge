#!/bin/bash
# Fires PreCompact — injects boundary-timing and state-preservation principles
# before every compaction so Claude applies them during summarization.

cat << 'RULES'
╔══ Compaction Discipline ═══════════════════════════════════════════╗
║  Compaction is about to occur. Apply these principles:            ║
╚═══════════════════════════════════════════════════════════════════╝

TIMING — compact at WORKFLOW BOUNDARIES, not arbitrary turn counts:
  ✓ Good triggers: end of phase, task completion, before context switch
  ✗ Poor triggers: fixed message count, reactive to size alone

PRESERVE in the compaction summary:
  ✓ Current working state and active batch context
  ✓ Cited facts and open questions
  ✓ Artifact paths (files created or modified)
  ✓ Unresolved concerns and phase-completion status
  ✓ Decisions made and their rationale

DO NOT over-compress:
  ✗ Never strip file paths, function names, or error messages
  ✗ Never collapse multi-step decisions into vague summaries
  ✗ Never lose specific values (numbers, identifiers) that will be needed

FIDELITY REQUIREMENTS — concrete, checkable additions to the rules above:
  ✓ Unanswered questions: mark each user question answered / partially / unanswered.
    Add a "Pending Questions" subheading listing every unanswered or partial one verbatim.
  ✓ Root causes vs ruled-out hypotheses: keep them separate. Record confirmed root causes
    with file:line. Keep ruled-out hypotheses too, so they don't get re-tried.
  ✓ File importance tiers: group files as critical (caused/fixed the issue), referenced
    (read for context), or mentioned (came up in discussion only) — not a flat list.
  ✓ Subagent/Task tool results are PRIMARY EVIDENCE, not compressible chatter — preserve
    a subagent's final report in full (file paths, findings, citations) wherever it applies.
    Subagent runs are expensive to redo.
  ✓ A-vs-B comparisons: when the user weighed option A vs option B, preserve both sides
    and the decision criteria — and which side won, if decided.

DOMAIN-AWARE STRATEGY — match compression style to content type:

  CODE (functions, classes, tests, configs):
    ✓ Chunk-level: keep function signatures, class names, return types, error lines
    ✓ Drop: implementation bodies that have already been acted on
    ✗ Never token-strip across a code boundary — broken syntax is worse than verbosity
    ✗ Never drop import lists or type annotations (callers depend on them)

  DOCUMENTATION / PROSE:
    ✓ Sentence-level: keep topic sentences and conclusions of each section
    ✓ Drop: supporting evidence, examples, and elaboration already incorporated
    ✗ Never drop section headers — they anchor the structure for future turns

  RETRIEVED DOCUMENTS (RAG results, search hits, fetched pages):
    ✓ Query-aware: filter to passages relevant to the LAST stated user intent
    ✓ Drop: passages that didn't contribute to the answer given
    ✗ Never drop source citations or URLs — needed for traceability

  CONVERSATION HISTORY (user/assistant turns):
    ✓ Keep: decisions, commitments, user corrections, unresolved questions
    ✓ Drop: affirmations, pleasantries, superseded plans, back-and-forth that resolved
    ✗ Never drop the user's exact wording when they corrected Claude's output

  TOOL OUTPUT (shell, file reads, API responses):
    ✓ Keep: error messages, key metrics, file paths, identifiers
    ✓ Drop: passing test output, verbose headers, repeated boilerplate
    ✗ Never drop non-zero exit codes or stack trace roots

METHOD — use anchored iterative summarization:
  Merge new content into existing summary sections rather than regenerating from scratch.
  Structure forces preservation: dedicated sections act as checklists.
  Rationale: full regeneration compounds LLM sampling drift — each pass moves content
  toward the model's prior, not ground truth (faulty memory / consolidation loop pattern).
RULES

# Deterministic handoff snapshot — never rely on the in-context summary alone.
python3 .claude/scripts/session/write_handoff.py --trigger precompact 2>/dev/null || true
