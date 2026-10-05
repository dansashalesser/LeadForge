# Skill Hierarchy — two tiers + domain routers

The harness ships 589 skills, 559 of them named in `scripts/setup/skill-library.txt`. Listing every one in every prompt cost ~13k tokens per
API call, so skills are split into two install tiers, with 14 "master" skills that route
to the rest. This is the single reference for where a skill goes and how to register one.

## The two tiers

| Tier | Install path | Listed in every prompt? | For |
|---|---|---|---|
| **Listed** | `~/.claude/skills/<name>/` | yes | the 14 masters + a small pinned set |
| **Library** | `~/.claude/skill-library/<name>/` | no | everything else (559), reached on demand |

Repo source for **both** tiers stays in `skills/<name>/`. The split happens at install:
`scripts/setup/sync-skills.sh` (called by `install.sh` and `update.sh`) reads
`scripts/setup/skill-library.txt` and routes each skill to one tier. It is the single
namer of the split — don't copy skills into `~/.claude` by hand.

## The 14 masters (domain routers)

`backend-dev · frontend-dev · languages · data-and-db · ai-ml-agents · devops-infra ·
observability-incident · security · code-quality · cloud-sdks · product-growth ·
integrations · docs-knowledge · harness-meta`

A master holds no technique. Its body is a table of its domain's sub-skills — `name | one-line
| path` — plus an instruction to read the ones that fit (as many as the task needs). An agent
sees the 14 master descriptions, opens the relevant master(s), then **reads the sub-skill
SKILL.md at its path** with the Read tool. Library sub-skills are reached this way, not via the
Skill tool.

## What stays Listed (pinned)

A skill must be Listed only if something loads it **by name through the Skill tool** —
`Skill("x")` / `Skill(skill="x")` — because that tool resolves only Listed skills. As of this
writing: the 8 hard-invoked (`auditing-spec-choices`, `cheap-model-delegation`,
`document-parsing`, `issue-triage-routing`, `secure-agent-design`, `skill-eval-gate`,
`skill-extraction`, `tool-failure-memory`), the 3 routine-invoked (`session-quality`,
`keep-rate`, `skill-curator`), and `lean-ctx` + `gitnexus` as always-on guidance. Everything
else is Library.

## Adding a new skill (default: Library)

1. Write it to `skills/<name>/SKILL.md` (repo source, both tiers live here).
2. Pick its master domain. Add one row to `skills/<master>/SKILL.md`'s table:
   `` | `<name>` | <one-line what+when> | `~/.claude/skill-library/<name>/SKILL.md` | ``
3. Add `<name>` on its own line to `scripts/setup/skill-library.txt`.
4. `bash scripts/setup/sync-skills.sh "$PWD"` to install it to the library.

**Exception — the skill must be ambiently discoverable** (a hook/command will `Skill("<name>")`
it, or you want it as a `/`-command): do NOT add it to the manifest. It installs Listed. Keep
the Listed set small; prefer Library + a master row unless a hard `Skill()` call requires otherwise.

If a caller needs a Library skill, point it at the path — `Read ~/.claude/skill-library/<name>/SKILL.md`
— never `Skill("<name>")`, and never text like "invoke the `<name>` skill": the agent reads that
as a Skill-tool call, finds nothing listed, and reports the skill as not installed.

**Enforced, not just documented** (added 2026-10-04, after moving `proof-collaborative-review`
to the library silently broke all three spec approval gates):

- `scripts/utils/check-skill-tiers.py` (pre-commit) fails when a commands/agents/hooks/kiro/
  scripts/templates line names a Library skill by bare name, or a skill calls `Skill("<lib>")`.
  So moving a skill into `skill-library.txt` fails the commit until each caller gets the path.
  Human-facing mentions (docstrings) go in `scripts/utils/skill-tier-allow.txt`.
- `hooks/claude/skill-library-resolver.sh` (PreToolUse `Skill`) catches what the check can't
  see, like soft "related skill" mentions inside skills. It turns a `Skill("<lib>")` call into
  "read this path".
- `session-start-hook.sh` prints a `[SKILL-LIBRARY]` line, so an agent looks in the library
  before reporting a skill as missing.

## Editing / deleting

- Edit any skill (either tier) through `scripts/routines/skill-write.sh` — it writes repo
  source **and** the correct installed copy (it checks the manifest for the tier).
- Delete through `scripts/routines/skill-delete.sh` — removes both copies and the manifest line.
  Also remove the skill's row from its master table by hand (a left-behind row is a dangling path).

## Why

Measured per-call skill-listing cost: ~13.3k (all listed) → ~5.0k (29 listed). The masters
preserve discovery — an agent still finds any sub-skill by routing through its domain — while
the Library skills cost nothing until read. See the master bodies for the live routing tables.

_Last synced: 2026-10-01_
