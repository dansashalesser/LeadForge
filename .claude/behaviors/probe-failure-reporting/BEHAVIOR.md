---
name: probe-failure-reporting
description: When a measurement probe (detector, metric calculator, linter, test, or query) fails or times out, report the failure explicitly rather than converting it to a zero or silence.
---

## Intent
When a probe fails, the outcome is unknown — not zero, not success. Reporting unknown as zero silently corrupts downstream scoring and decision-making. The correct response is to emit a suppress-scoring sentinel (e.g., a `[detector-down]` observation, an `undefined` metric value, or an explicit error status) so that missing data remains distinguishable from negative data.

## Evidence
- **2026-10-05 [detector-down]**: Session signal detection timed out after 120s; no suppression sentinel was written. This silence cascaded: both the 2026-10-04 and 2026-10-05 [judge] observations reported "all agreed, spread=0.0" despite trace.log showing 2-3 runs with status=error. Errored probes defaulted to 0.0 and were counted as agreement.

- **2026-10-05 [keep-rate]**: Cohort was empty (0 commits older than 7 days). Correct behavior: report `Keep Rate = undefined`. Incorrect would be: report `Keep Rate = 0%`, which would anchor the first real measurement low. The observations correctly chose `undefined`.

- **2026-10-04 [keep-rate]**: Same pattern: "No metric written to metrics.jsonl — recording 0% on an empty cohort would drag the first real reading down." Demonstrates the template: empty input → suppress metric, not zero metric.

- **2026-10-05 [seed-target:systematic-debugging]**: jq command failed (exit 1) with incomplete output. The response was silent (no observation written); the impact on downstream scoring is unauditable.

- **2026-10-05 [insight, friction]**: "A failed probe must emit a suppress-scoring sentinel, never a value." Explicit guidance confirming the desired conduct.

## Decision
When a probe (detector, metric calculator, test suite, jq query, linter, or any other measurement tool) returns non-zero or times out:
1. **Never** convert the failure to a zero, success, or agreement marker
2. **Always** emit a suppress-scoring sentinel: a `[detector-down]` or `[probe-error]` observation, `undefined` metric value, or explicit error status
3. Include the probe name, failure mode (timeout/exit/error), and any partial output (if recovery is possible)

## Execution
- Check exit codes and timeouts before aggregating results
- If any probe in a cohort fails, mark the entire cohort's result as suppressed/undefined rather than zero
- Write sentinel to ledger (observation) or output (metrics.jsonl) before proceeding
- Document which probes failed and why (enables human retriage and prevents silent corruption)

## Recovery
- Once the probe is fixed, re-run it and update the suppressed record
- Do not backfill historical zeros or false agreements
- If partial results were captured (e.g., jq output truncated), preserve them for manual review

## Failure modes
- **Scope creep**: silently treating "no data" as "zero" or "agreement"
- **Cascading silence**: a suppressed signal at layer N propagates as zero or silence at layer N+1, corrupting all downstream decisions
- **Unauditable recovery**: missing the original failure record, so a later fix cannot be traced back to what it repaired
