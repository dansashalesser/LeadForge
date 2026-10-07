# Local LLM Evaluation (OMT)

Ollama Model Tester — zero-dependency CLI for running prompts against local Ollama models and saving every response to disk.

**Source:** https://github.com/ulyssestenn/omt  
**Script:** `.claude/scripts/ollama_model_test.py`  
**Skill:** `local-llm-eval`

---

## What It Does

- Runs a prompt N times against any locally-installed Ollama model
- Saves all responses as Markdown with token counts, timing, and temperature
- Keys output folders on prompt SHA256 hash — multi-model runs for the same prompt land in the same folder, enabling side-by-side comparison
- Zero external dependencies (Python stdlib only)
- `--runner` generalizes generation to any CLI-wrapped model/agent (not just Ollama)
- `--checker` adds an automated pass/fail verdict (`grades.json`), on top of the human-eyeballing markdown output

---

## Quick Start

```bash
# Interactive — picks model and prompt from menu
python3 .claude/scripts/ollama_model_test.py

# Fully scripted
python3 .claude/scripts/ollama_model_test.py \
  --model llama3.1:8b \
  --prompt-file prompt.txt \
  --runs 3 \
  --temperature 0.7 \
  --no-stream
```

---

## Flags

| Flag | Description |
|---|---|
| `--model NAME` | Ollama model name (must be installed locally) |
| `--runs N` | Number of generations (default: ask interactively) |
| `--temperature T` | 0.0–2.0; omit for Ollama default (0.8) |
| `--prompt-file PATH` | UTF-8 text file containing the prompt |
| `--stream` / `--no-stream` | Stream live vs. wait for full response |
| `--runner PATH` | Executable wrapping a non-Ollama CLI model/agent. Called once per run with the prompt on stdin and `OMT_MODEL`/`OMT_PROMPT`/`OMT_RUN_DIR` set in its environment; stdout becomes the recorded response. Requires `--model`. Omit for the built-in Ollama path. |
| `--checker PATH` | Executable run once after all generations for a model complete, with `OMT_RUN_DIR`/`OMT_MODEL` set. stdout must be JSON `{"pass": bool, "score": number, "notes": str}`. Result is appended to `grades.json`. |

---

## Output Structure

```
ollama-runs/
  <prompt-slug>_<8-char-hash>/
    prompt.md        # full prompt + hash + creation timestamp
    metadata.json    # all run history for this prompt (all models)
    llama3.1-8b.md   # responses + Ollama metadata per run
    gemma3-4b.md     # (one file per model; re-runs append to same file)
```

The folder name is derived from the first 6 words of the prompt + 8 chars of its SHA256 hash. Running the same prompt against a different model drops output into the **same folder**.

### metadata.json schema

```json
{
  "prompt_hash": "<sha256>",
  "prompt_preview": "<first 160 chars>",
  "files": { "prompt": "prompt.md" },
  "runs": [
    {
      "model": "llama3.1:8b",
      "runs_requested": 3,
      "runs_completed": 3,
      "started_at": "2026-06-07T...",
      "finished_at": "2026-06-07T...",
      "options": { "temperature": 0.7 },
      "stream": false,
      "files": { "model_output": "llama3.1-8b.md" }
    }
  ]
}
```

---

### grades.json schema

Written only when `--checker` is passed. Lives alongside `metadata.json` in the same run folder.

```json
{
  "grades": [
    {
      "model": "llama3.1:8b",
      "graded_at": "2026-08-04T...",
      "pass": true,
      "score": 0.9,
      "notes": "matched expected structure"
    }
  ]
}
```

---

## Common Patterns

### Model comparison
```bash
PROMPT_FILE=prompt.txt
for model in llama3.1:8b gemma3:4b mistral:7b; do
  python3 .claude/scripts/ollama_model_test.py \
    --model "$model" --prompt-file "$PROMPT_FILE" \
    --runs 1 --temperature 0.0 --no-stream
done
# All outputs land in same ollama-runs/<hash>/ folder
```

### Variance / stability audit
```bash
python3 .claude/scripts/ollama_model_test.py \
  --model llama3.1:8b --prompt-file prompt.txt \
  --runs 10 --temperature 0.8 --no-stream
```

### Temperature sweep
```bash
for temp in 0.0 0.3 0.7 1.0; do
  python3 .claude/scripts/ollama_model_test.py \
    --model llama3.1:8b --prompt-file prompt.txt \
    --runs 1 --temperature "$temp" --no-stream
done
# Each temperature → separate run entry in metadata.json
```

---

## Requirements

- Python 3.7+
- [Ollama](https://ollama.com) running at `http://localhost:11434`
- At least one model pulled: `ollama pull llama3.1:8b`

Check Ollama is running:
```bash
curl -s http://localhost:11434/api/tags
```

---

## Related

- Skill: `local-llm-eval` — invoke via Claude Code for guided evaluation workflows
- Skill: `llm-evaluation` — abstract evaluation patterns (metrics, rubrics)
- Skill: `prompt-engineer` — prompt design and iteration strategies
