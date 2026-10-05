# Privacy Filter — PII Detection & Scanning

> Local ML-based PII detection and redaction for the SDD harness, powered by [OpenAI Privacy Filter (OPF)](https://github.com/openai/privacy-filter). Runs entirely on-premises — no data leaves your machine.

## What It Is

OPF is a 1.5B-parameter bidirectional transformer (50M active via sparse MoE) that classifies tokens as PII and masks them. It detects 8 categories of sensitive data with configurable precision-recall tradeoffs, and outputs either plain redacted text or structured JSON with span details.

The harness integration adds:
1. **`privacy-filter` skill** — Guided workflow for scanning files, interpreting results, and redacting before commits or sharing
2. **`scan-pii.sh`** — A standalone bash scanner that can gate commits on high-severity findings

## PII Categories

| Category | Examples | Commit Severity |
|---|---|---|
| `secret` | API keys, tokens, passwords | 🔴 Blocks commit |
| `account_number` | Credit cards, bank account numbers | 🔴 Blocks commit |
| `private_email` | Email addresses | 🟡 Warning |
| `private_phone_number` | Phone numbers | 🟡 Warning |
| `private_person` | Personal names | 🟡 Warning |
| `private_address` | Street addresses | 🟡 Warning |
| `private_date` | Dates of birth, event dates | 🟢 Low |
| `private_url` | URLs with personal path components | 🟢 Low |

## Setup

### 1. Install OPF

OPF is **not on PyPI** — it installs from OpenAI's GitHub source. Use `uv tool` so the `opf` CLI lands on your PATH in an isolated env:

```bash
# preferred — isolated CLI on PATH:
uv tool install --python 3.13 git+https://github.com/openai/privacy-filter.git
# or pipx:
pipx install --python python3.13 git+https://github.com/openai/privacy-filter.git
# or plain pip (into the active/user env):
pip install git+https://github.com/openai/privacy-filter.git
```

Pin to Python 3.13 — torch lacks wheels for the newest CPython, and the system default may be Apple's 3.9. Model weights (~2.8GB) download automatically on first run to `~/.opf/privacy_filter`. Set `OPF_CHECKPOINT` to use a custom path.

### 2. Verify install

```bash
opf "test@example.com"
# → <private_email>
```

### 3. (Optional) Wire as a git pre-commit hook

To automatically block commits that contain secrets or account numbers, add to your project's `.git/hooks/pre-commit`:

```bash
#!/bin/bash
bash "$(git rev-parse --show-toplevel)/.claude/hooks/scan-pii.sh" --staged
```

Then make it executable:
```bash
chmod +x .git/hooks/pre-commit
```

In a normal project the slot is free: the harness's own guards (`hooks/git/pre-commit` — the hardcoded-path check, and since 2026-09-03 the embedded-Python regex check alongside it) are deliberately never propagated downstream, and if `install.sh` / `update.sh` find a pre-commit they don't recognise they leave it alone.

**In the harness repo itself**, `.git/hooks/pre-commit` is owned by those guards and is rewritten on every `install.sh` / `update.sh` run, so a `scan-pii.sh` line added there is lost at the next update. Add it to `hooks/git/pre-commit` in the source tree instead. That file runs each guard only if its script is present and blocks the commit if either fails, printing both verdicts rather than stopping at the first — an added `scan-pii.sh` call should follow the same shape.

---

## Usage

### CLI — Quick scan

```bash
# Scan a file
opf -f path/to/file.txt

# Scan a string
opf "Contact dan@example.com or call 555-1234"

# Structured JSON output with span details
opf -f file.txt --format json

# Redact to clean file
opf -f file.txt --output-mode redacted > file_clean.txt
```

### Harness scanner script

```bash
# Scan staged files (use before git commit)
bash .claude/hooks/scan-pii.sh --staged

# Scan a specific file
bash .claude/hooks/scan-pii.sh path/to/file.txt

# Scan a directory
bash .claude/hooks/scan-pii.sh .
```

The script exits `0` for clean or low-severity findings, `1` for high-severity (`secret` or `account_number`), and `2` if OPF is not installed.

### Skill

Invoke the skill from within a Claude Code session:

```
/privacy-filter   ← (if exposed as a slash command)
```

Or let it load automatically when asked to scan for PII. It guides through install → scan → interpret → redact.

---

## Architecture

```
┌─ Developer workflow ─────────────────────────────────────────────────┐
│                                                                        │
│  git add / stage files                                                 │
│    ↓                                                                   │
│  .git/hooks/pre-commit (optional)                                      │
│    → scan-pii.sh --staged                                             │
│    → OPF: tokenize → infer → CRF decode → span extraction             │
│    → exit 1 if secret/account_number found (blocks commit)            │
│                                                                        │
│  /kiro:ship review                                                     │
│    → scan-pii.sh on changed files as part of readiness check          │
│                                                                        │
│  Ad-hoc: opf -f <file> --format json                                  │
│    → structured output with span labels, positions, text              │
│    → redact with --output-mode redacted                               │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─ OPF model pipeline ────────────────────────────────────────────────┐
│  tiktoken tokenizer → 8-layer bidirectional transformer              │
│  → sparse MoE FFN (128 experts, top-4) → token classifier            │
│  → Viterbi CRF decoding → char-level span extraction                 │
└─────────────────────────────────────────────────────────────────────┘
```

### Graceful degradation

`scan-pii.sh` exits `2` (not `1`) when OPF is missing, so it never silently passes — but it also doesn't break workflows where OPF hasn't been installed. Wire your pre-commit hook accordingly:

```bash
# In .git/hooks/pre-commit — treat "not installed" as a warning, not a block:
bash .claude/hooks/scan-pii.sh --staged
STATUS=$?
if [[ $STATUS -eq 2 ]]; then
  echo "OPF not installed — PII scan skipped."
  exit 0
fi
exit $STATUS
```

---

## Output Modes

| Flag | Behavior | Use case |
|---|---|---|
| `--output-mode typed` | Replaces PII with `<category_label>` | Review, audit logging |
| `--output-mode redacted` | Replaces all PII with `<REDACTED>` | Sharing externally |
| `--format json` | Full JSON with spans, positions, summary | Programmatic processing |
| `--format text` | Plain text output (default) | Human review |

---

## Python API

```python
from opf import redact, OPF

# One-liner (uses cached default model)
clean = redact("dan@example.com called from 555-1234")
# → "<private_email> called from <private_phone_number>"

# Full control
model = OPF(
    output_mode="typed",     # "typed" or "redacted"
    decode_mode="viterbi",   # "viterbi" (constrained) or "argmax" (faster)
    trim_whitespace=True,
)
result = model.redact("my API key is sk-proj-abc123")
print(result.redacted_text)       # "my API key is <secret>"
print(result.detected_spans)      # [Span(label="secret", start=14, end=27, ...)]
print(result.summary.labels)      # {"secret": 1}
```

---

## Limitations

OPF is a **detection aid, not an anonymization or compliance guarantee**:

- May miss uncommon names, novel API key formats, or out-of-distribution text
- Accuracy degrades on non-English text
- Placeholder `<REDACTED>` is not format-preserving
- High-sensitivity deployments (medical, legal, financial, government) require additional human review safeguards
- Static label policies require model retraining for custom categories

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `opf: command not found` | Not installed | `uv tool install --python 3.13 git+https://github.com/openai/privacy-filter.git` |
| Slow first run | Downloading model weights (~2.8GB) | Expected — subsequent runs use cache |
| `OPF_CHECKPOINT` not found | Custom path misconfigured | Check env var points to valid checkpoint dir |
| False positives on code identifiers | Variable names resembling PII | Use `--decode-mode argmax` for lower recall |
| `scan-pii.sh` exits 2 | OPF not installed | Install OPF; script intentionally soft-fails |
| Pre-commit hook blocks valid data | Over-redaction | Inspect JSON output; whitelist or use `--decode-mode argmax` |

---

## Device & Performance

| Setting | Flag | Notes |
|---|---|---|
| CPU (default on non-GPU) | `--device cpu` | Slower; context window defaults to 4096 |
| CUDA (GPU) | `--device cuda` | Fast; full 128k context window |
| Torch compile | `OPF_TORCH_COMPILE=1 opf ...` | ~20% faster after warmup |

For CI pipelines on CPU-only runners, expect ~2–5s per file after model load.

---

## Related

- [`secrets-management` skill](~/.claude/skill-library/secrets-management/) — Vault, AWS Secrets Manager, HashiCorp patterns
- [`gdpr-data-handling` skill](~/.claude/skill-library/gdpr-data-handling/) — GDPR consent, data subject rights, privacy-by-design
- [`sast-configuration` skill](~/.claude/skill-library/sast-configuration/) — Static analysis with Semgrep, CodeQL
- [OpenAI Privacy Filter repo](https://github.com/openai/privacy-filter) — Source, model card, fine-tuning guide

_Last synced: 2026-09-03_
