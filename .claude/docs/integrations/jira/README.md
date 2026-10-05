# Jira Integration — Ticket Solving + Automatic Commenting

> Detailed reference for the Jira solve workflow and auto-comment hook system.

## What It Is

A full ticket-to-code pipeline. `/kiro:jira-solve TICKET-ID` fetches a Jira ticket, analyzes it, searches the codebase for relevant files, classifies the issue type, and routes it to the correct solve workflow — debugging for bugs, SDD spec pipeline for features, or direct implementation for tasks. It posts progress comments to Jira automatically.

On top of that, a hook pair auto-posts a summary comment to Jira every time you `git push`.

## How It Works

### The Solve Pipeline

```
/kiro:jira-solve ZORAAI-1234
        │
        ▼
┌─ Step 1: Fetch ──────────────────────────────┐
│  jira_client.py fetch ZORAAI-1234             │
│  → summary, description, acceptance criteria, │
│    linked issues, type, priority, assignee     │
└───────────────────────────────────────────────┘
        │
        ▼
┌─ Step 2: Analyze (jira-solve-agent) ──────────┐
│  → Convert to structured problem statement     │
│  → Extract keywords from title/desc/labels     │
│  → Search codebase (Glob + Grep)               │
│  → Rank top 5-10 relevant files                │
│  → Classify: bug | feature | task              │
└───────────────────────────────────────────────┘
        │
        ▼
┌─ Step 3: Post "Analysis Started" to Jira ─────┐
│  Comment with: type detected, workflow chosen,  │
│  relevant files identified                      │
└───────────────────────────────────────────────┘
        │
        ▼
┌─ Step 4: Route to Solve Workflow ─────────────┐
│                                                │
│  BUG / DEFECT:                                 │
│    → Route to /kiro:debug (6-step triage)      │
│    → Reproduce → Localize → Reduce → Fix       │
│    → Guard (regression test) → Verify           │
│                                                │
│  STORY / FEATURE / EPIC:                       │
│    → Build spec description from Jira context  │
│    → /kiro:spec-quick "<description>"          │
│    → Full SDD pipeline with human review gates │
│                                                │
│  TASK / SUB-TASK / CHORE:                      │
│    → Build implementation plan                 │
│    → Present plan for user approval            │
│    → Implement atomically, test after each     │
└───────────────────────────────────────────────┘
        │
        ▼
┌─ Step 5: Post "Analysis Complete" to Jira ────┐
│  Comment with: workflow used, outcome summary,  │
│  files changed                                  │
└───────────────────────────────────────────────┘
```

### Auto-Comment on Push (Hooks)

Separate from the solve pipeline, a hook pair posts a summary comment on every `git push`:

```
┌─ UserPromptSubmit hook (global ~/.claude/settings.json) ─┐
│  User types: /kiro:jira-solve ZORAAI-1234                │
│  → jira_capture_ticket.py extracts "ZORAAI-1234"         │
│  → writes to ~/.claude/state/active_jira_ticket           │
│  (async, never blocks Claude)                             │
└──────────────────────────────────────────────────────────┘

┌─ PostToolUse Bash hook (project .claude/settings.json) ──┐
│  Claude runs: git push                                    │
│  → hook detects "git push" in command                     │
│  → checks ~/.claude/state/active_jira_ticket exists       │
│  → jira_push_comment.py builds and posts comment          │
│  → deletes state file (single-fire, no double-post)       │
└──────────────────────────────────────────────────────────┘
```

The push comment includes:
- **Branch name** and commit count
- **Approach summary** — extracted from the most recently modified `.md` file in `docs/` that mentions the ticket
- **Files changed** — from `git diff --name-only origin/main..HEAD`
- **Commit messages** — from `git log origin/main..HEAD`

Format: Jira wiki markup (not Markdown), so it renders correctly in Jira's UI.

## Scripts

| Script | Purpose |
|---|---|
| `scripts/integrations/jira/jira_client.py` | Stdlib-only Jira REST API client. Supports fetch ticket, post comment, search. No external dependencies. |
| `scripts/integrations/jira/jira_capture_ticket.py` | Reads Claude's stdin JSON, extracts ticket ID from prompt text via regex, writes state file. |
| `scripts/integrations/jira/jira_push_comment.py` | Reads git log/diff, finds relevant docs, assembles wiki-markup comment, posts via `jira_client.py`. |

All scripts are stdlib-only Python — no `pip install` needed.

## Use Cases

1. **Bug fixing** — `/kiro:jira-solve BUG-123` fetches the bug, routes through `/kiro:debug` 6-step triage (reproduce → localize → reduce → fix → guard → verify)
2. **Feature development** — `/kiro:jira-solve STORY-456` pulls acceptance criteria from Jira and feeds them into the full SDD spec pipeline
3. **Task execution** — `/kiro:jira-solve TASK-789` builds an implementation plan from the ticket description, asks for approval, then implements
4. **Audit trail** — Jira comments are posted automatically at analysis start and completion, plus on every `git push`
5. **Async teams** — Team members see exactly what Claude did, why, and which files changed — directly in the ticket
6. **Dry-run triage** — `/kiro:jira-solve TICKET-123 --dry-run` fetches and displays the ticket without triggering any workflow

## How to Use

### Full solve workflow
```
/kiro:jira-solve ZORAAI-1234     # fetch, analyze, route to correct workflow
```
Claude fetches the ticket, posts an "analysis started" comment, executes the solve workflow (with human review gates for features), then posts a completion comment.

### Dry run (triage only)
```
/kiro:jira-solve ZORAAI-1234 --dry-run    # fetch and display, no side effects
```

### Auto-comment on push
After a `jira-solve` session, every `git push` posts a summary comment to the ticket. The state is single-fire — deleted after posting. To re-arm, run `/kiro:jira-solve` again.

## Credentials

Create `~/.env.jira`:

**PAT authentication (Jira Data Center):**
```
JIRA_URL=https://your-jira-instance.example.com
JIRA_PAT=your-personal-access-token
```

**Basic auth (Jira Cloud):**
```
JIRA_URL=https://your-jira-instance.example.com
JIRA_USERNAME=your.email@example.com
JIRA_API_TOKEN=your-api-token
```

## Setup

### 1. Global hook (ticket capture)

Add to `~/.claude/settings.json`:
```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/repo/.claude/scripts/jira_capture_ticket.py 2>/dev/null || true",
            "async": true
          }
        ]
      }
    ]
  }
}
```

### 2. Project hook (push comment)

Add to `.claude/settings.json` (merge with existing `PostToolUse` entries):
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.command' | grep -q '^git push' && python3 /path/to/repo/.claude/scripts/jira_push_comment.py /path/to/repo 2>/dev/null || true"
          }
        ]
      }
    ]
  }
}
```

### 3. Create credentials file

```bash
touch ~/.env.jira
chmod 600 ~/.env.jira
# edit with your Jira URL and PAT/token
```

### 4. Create state directory

```bash
mkdir -p ~/.claude/state
```

See `SDD-SETUP-GUIDE.md` → "Jira Integration" for the full walkthrough.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| No comment posted on push | State file missing (capture hook didn't fire) | Check `~/.claude/settings.json` has the `UserPromptSubmit` hook |
| Comment posted but empty approach | No `.md` file in `docs/` mentions the ticket ID | Create a doc or just push — the comment will still include branch/commits/files |
| Auth error (401) | Bad credentials | Verify `~/.env.jira` values; test with `curl -H "Authorization: Bearer $JIRA_PAT" $JIRA_URL/rest/api/2/myself` |
| Double-post | State file not cleaned up | Check `jira_push_comment.py` has delete logic; verify file permissions on `~/.claude/state/` |
