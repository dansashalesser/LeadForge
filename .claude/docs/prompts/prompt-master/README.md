# Prompt Master — AI Prompt Engineering Skill

> Write the right prompt on the first attempt. Zero re-prompts.

## What It Is

**prompt-master** is a Claude Code skill (v1.7.0) that functions as an active prompt factory. Given a rough idea and a target AI tool, it silently routes to the right template, extracts intent across 9 dimensions, and delivers a single production-ready prompt. It supports **JSON-structured prompt input** and generates JSON-formatted prompts when precision demands it.

Installed at: `~/.claude/skills/prompt-master/`

---

## The Problem It Solves

Every AI user wastes tokens the same way:

```
Write vague prompt → get wrong output → re-prompt → re-prompt → finally get it on attempt 4
```

The root cause is the model filling in unspecified dimensions with statistical averages. If you don't specify tone, length, format, and audience — the model guesses. It guesses *averagely*.

**prompt-master** solves this in two ways:

1. **Tool-specific templates** — 30+ AI tools have fundamentally different prompt syntax. A Midjourney prompt looks nothing like a Cursor prompt. Routing to the wrong template wastes your first attempt.

2. **JSON prompting** — structured key-value input that eliminates the guessing surface entirely.

---

## How to Invoke

In any Claude Code session:

```
Write me a prompt for Cursor to refactor my auth module
```

```
I need a prompt for Claude Code to build a REST API — ask me what you need
```

```
Here's a bad prompt I wrote for GPT-4o, fix it: [paste prompt]
```

```
Generate a Midjourney prompt for a cyberpunk city at night
```

```
Break this prompt down and adapt it for Stable Diffusion: [paste]
```

Or with explicit JSON input (see below):

```json
{
  "task": "write a tweet",
  "topic": "dopamine detox",
  "tone": "punchy and contrarian",
  "length": "under 280 characters",
  "style": "viral"
}
```

---

## JSON Prompting

The model guesses when you leave dimensions unspecified. JSON eliminates the guessing surface.

### The core insight

```
"Write me a tweet about dopamine detox"
```

The model has to guess: tone? length? audience? format? It fills the gaps with whatever is statistically average. That's why outputs feel generic — you're asking, not specifying.

Now watch what happens with JSON:

```json
{
  "task": "write a tweet",
  "topic": "dopamine detox",
  "style": "viral",
  "length": "under 280 characters",
  "tone": "punchy and contrarian"
}
```

Same request. Zero ambiguity. The model stops guessing and starts executing.

### Nested JSON for structured outputs

Want sharper outputs? Nest the structure:

```json
{
  "task": "write a thread",
  "platform": "twitter",
  "structure": {
    "hook": "curiosity-driven, under 10 words",
    "body": "3 insights with examples",
    "cta": "question that sparks replies"
  },
  "topic": "founder productivity"
}
```

Each nested key becomes an explicit instruction. Nothing left to inference.

### JSON for API / programmatic output

Lock the response schema entirely:

```json
{
  "task": "analyze support ticket",
  "input": "[ticket text]",
  "output_format": "JSON",
  "output_schema": {
    "category": "authentication | billing | bug | feature_request | other",
    "severity": "low | medium | high | critical",
    "summary": "one sentence under 20 words",
    "suggested_action": "string"
  },
  "constraints": "respond only with valid JSON, no prose outside the object"
}
```

### JSON for agentic tools (Claude Code, Cursor, Devin)

```json
{
  "objective": "add JWT authentication to the Express API",
  "starting_state": "Express app in /src, no auth middleware, users table exists",
  "target_state": "POST /login returns JWT, middleware validates token on protected routes",
  "scope": {
    "work_in": "/src/routes/, /src/middleware/",
    "do_not_touch": ".env, package.json, database migrations"
  },
  "constraints": ["no new dependencies without asking", "TypeScript strict"],
  "stop_conditions": ["deleting any file", "adding any dependency", "schema changes"]
}
```

### When to use JSON vs prose

| Use JSON | Use Prose |
|----------|-----------|
| ≥3 dimensions unspecified (tone, format, length, audience, style) | Single-dimension request ("write a haiku about rain") |
| API / programmatic consumer | Casual conversational back-and-forth |
| Agentic tools with complex briefs | Creative work where open space is intentional |
| Output has defined sub-structure | Simple one-shot task |

**prompt-master detects JSON input automatically** — if you write your request as a JSON object, it maps keys to intent dimensions and skips clarifying questions for already-covered dimensions.

---

## 30+ Tool Profiles

prompt-master includes specific routing logic for:

| Category | Tools |
|----------|-------|
| **Reasoning LLMs** | Claude 4.x, ChatGPT/GPT-5.x, Gemini 2.x/3 Pro, o3/o4-mini, DeepSeek-R1, MiniMax M2.7, Qwen 2.5/3 |
| **Local LLMs** | Ollama, Llama, Mistral, Qwen2.5-Coder |
| **Agentic AI** | Claude Code, Cursor, Windsurf, Cline, GitHub Copilot, Devin, SWE-agent, Antigravity |
| **Full-stack generators** | Bolt, v0, Lovable, Figma Make, Google Stitch |
| **Autonomous agents** | Manus, Perplexity Computer, OpenAI Atlas, OpenClaw, Comet |
| **Image generation** | Midjourney, DALL-E 3, Stable Diffusion, SeeDream, ComfyUI |
| **3D AI** | Meshy, Tripo, Rodin, BlenderGPT, Unity AI |
| **Video AI** | Sora, Runway, Kling, LTX Video, Dream Machine |
| **Voice AI** | ElevenLabs |
| **Workflow AI** | Zapier, Make, n8n |
| **Research AI** | Perplexity, SearchGPT |

---

## 14 Templates

prompt-master picks the right architecture automatically and routes silently — you never see the template name, just the prompt.

| Template | Best For |
|----------|----------|
| **A — RTF** | Simple one-shot tasks |
| **B — CO-STAR** | Professional documents, business writing |
| **C — RISEN** | Complex multi-step projects |
| **D — CRISPE** | Creative work, brand voice |
| **E — Chain of Thought** | Logic, math, debugging |
| **F — Few-Shot** | Consistent structured output |
| **G — File-Scope** | Cursor, Windsurf, Copilot |
| **H — ReAct + Stop Conditions** | Claude Code, Devin, autonomous agents |
| **I — Visual Descriptor** | Image and video generation |
| **J — Reference Image Editing** | Editing an existing image |
| **K — ComfyUI** | Node-based image workflows |
| **L — Prompt Decompiler** | Break down, adapt, simplify existing prompts |
| **M — Opus 4.7 Task Brief** | Complex agentic tasks on Claude Opus 4.7 |
| **N — JSON Prompt** | Any task with ≥3 guessable dimensions |

Full template specs: `~/.claude/skills/prompt-master/references/templates.md`

---

## 38 Anti-Patterns Detected

prompt-master scans every prompt for failure patterns and fixes them silently. Flagged only if the fix changes intent.

**Categories:** Task (7) · Context (6) · Format (6) · Scope (6) · Reasoning (5) · Agentic (7) · JSON Prompting (1)

**Agentic patterns (the credit killers):**
- No starting state → statistical guessing about current environment
- No target state → agent keeps going until it invents a stopping condition
- No stop conditions → runaway loops, scope explosion
- No file scope → agent touches files it shouldn't
- Vague first turn on Opus 4.7 → literal execution of incomplete intent
- Context rot on long sessions → corrections in the same session for 60+ turns

**JSON pattern:**
- Prose with ≥3 guessable dimensions → offer JSON structure that locks all three simultaneously

Full pattern list with before/after examples: `~/.claude/skills/prompt-master/references/patterns.md`

---

## Key Behaviors

| Behavior | What happens |
|----------|-------------|
| **JSON input detection** | User writes JSON → keys mapped to intent dimensions, clarifying questions skipped for covered dims |
| **Tool routing** | Silently identifies target tool and applies tool-specific syntax — no framework names shown |
| **9-dimension extraction** | task, tool, format, constraints, input, context, audience, success criteria, examples |
| **Max 3 questions** | Never asks more than 3 clarifying questions before producing a prompt |
| **Credential stripping** | Any API key or secret in user input is removed before output |
| **Prompt injection defense** | Pasted prompts treated as inert data only — embedded instructions not followed |
| **Agentic output warning** | All agentic prompts get a mandatory "review before pasting" notice |
| **Memory block** | Session history → prepended memory block so AI never contradicts earlier work |

---

## Opus 4.7 Support

Template M handles complex, multi-step, or agentic tasks on Claude Opus 4.7 specifically. Key differences from earlier Claude versions:

- **More literal** — vague first turns produce narrower results. Front-load everything.
- **Adaptive thinking** — do NOT add "think step by step" or effort-level instructions. It calibrates automatically.
- **Fewer subagents by default** — explicitly request when needed.
- **Over-engineers by default** — add "Only make changes directly requested."
- **Session hygiene** — new task = new session. `/compact` at ~50% context, not 90%.

---

## Reference Files

| File | Contents | Load when |
|------|----------|-----------|
| `SKILL.md` | Core routing logic, tool profiles, diagnostic checklist | Always loaded when skill is active |
| `references/templates.md` | Full template library (A through N) | Loaded per-task, not all at once |
| `references/patterns.md` | 38 anti-patterns with before/after examples | Loaded when fixing or diagnosing bad prompts |
