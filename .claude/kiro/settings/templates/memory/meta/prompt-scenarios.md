# Prompt Scenarios — Expected Outcomes for Agent Regression Testing

Each scenario defines an input context and the expected agent output, with an alignment floor. Used by `/kiro:harness-test regression` to verify prompt changes don't regress agent quality.

## How Scenarios Are Created

1. **From real usage**: After a successful agent invocation, capture the input/output as a scenario
2. **From failures**: After diagnosing a prompt issue, create a scenario that would have caught it
3. **From edge cases**: Add scenarios for known tricky situations (ambiguous inputs, boundary conditions)

## Scenario Format

```markdown
## {agent-name}
### Scenario: {short-descriptive-name}
- **Input**: [description of the input context — what files, what task, what state]
- **Expected outcome**: [the correct conclusion: GO/NO-GO, PASS/FAIL, or key content expected]
- **Expected finding**: [specific finding that must appear in the output]
- **Alignment floor**: [minimum acceptable alignment score, 1-5]
- **Added**: [YYYY-MM-DD] — [why this scenario was added]
```

## Anti-Overfitting Rule

Scenarios test *general capabilities*, not memorized answers. The agent should arrive at the expected outcome through correct reasoning, not keyword matching. If an instruction change only helps by matching scenario keywords, it has overfit.

---

<!-- Add scenarios below as they are identified from real usage and diagnoses -->
