# settings.json notes

JSON allows no comments — Claude Code refuses to parse `.claude/settings.json`
if anything follows the closing brace. Keep notes here instead.

## OPTIONAL: ktx data-context MCP server

Adds ktx semantic layer tools (`ktx_query`, `ktx_schema`, `ktx_metrics`) so
Claude Code agents query approved metric definitions instead of guessing SQL.

Prerequisites: `pip install ktx` or `npm install -g ktx`, then `ktx init` in the
project root, then `ktx build` to ingest sources.

Merge into the root object of `settings.json` alongside the existing keys:

```json
"mcpServers": {
  "ktx": {
    "command": "ktx",
    "args": ["serve", "--stdio"],
    "env": {}
  }
}
```

- Skill: `~/.claude/skills/ktx-data-context/SKILL.md`
- Docs: `docs/skills/ktx-data-context/README.md`
- Repo: https://github.com/Kaelio/ktx
