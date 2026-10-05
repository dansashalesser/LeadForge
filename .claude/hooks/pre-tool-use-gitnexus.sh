#!/bin/bash
# PreToolUse hook: Enrich file reads/edits with GitNexus 360-degree context
# When Claude reads or edits a file, inject dependency/caller/process context
# from the GitNexus knowledge graph so agents understand blast radius.
#
# Reads JSON from stdin: { "tool_name": "Read", "tool_input": { "file_path": "..." } }
# Outputs context to stdout (injected as hook feedback into the conversation).
# Exits 0 to allow the tool call to proceed.

# Fast bail: skip if GitNexus not indexed
if [ ! -d ".gitnexus" ]; then
  exit 0
fi

# Fast bail: skip if gitnexus CLI not available
if ! command -v gitnexus >/dev/null 2>&1 && ! npx gitnexus --version >/dev/null 2>&1; then
  exit 0
fi

# Read the tool call JSON from stdin
INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)

# Only enrich Read and Edit calls on source files
case "$TOOL_NAME" in
  Read|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

# Skip non-source files (docs, configs, generated)
case "$FILE_PATH" in
  *.md|*.json|*.yaml|*.yml|*.toml|*.lock|*.log) exit 0 ;;
  *node_modules/*|*.git/*|*.gitnexus/*|*__pycache__/*|*.venv/*) exit 0 ;;
esac

# Skip if file path is empty
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Extract just the filename for the query
FILENAME=$(basename "$FILE_PATH")

# Query GitNexus for context on this file (timeout 3s to avoid blocking)
CONTEXT=$(timeout 3 npx gitnexus query --repo . --query "$FILENAME" --limit 5 2>/dev/null)

if [ -n "$CONTEXT" ] && [ "$CONTEXT" != "null" ] && [ "$CONTEXT" != "[]" ]; then
  echo ""
  echo "[GitNexus context for $FILENAME]"
  echo "$CONTEXT"
  echo "[/GitNexus context]"
fi

exit 0
