---
description: Launch GitNexus Web UI to visually explore codebase connections
allowed-tools: Read, Bash, Glob
argument-hint: [--port PORT]
---

# GitNexus Visual Explorer

Launch the GitNexus Web UI to browse code connections, call chains, and process flows in a browser.

## Prerequisites Check

1. Check if `gitnexus` is installed:
   ```bash
   command -v gitnexus >/dev/null 2>&1 || npx gitnexus --version >/dev/null 2>&1
   ```

2. If not installed, tell the user:
   ```
   GitNexus is not installed. Run /kiro:gitnexus-setup to install and configure it.
   ```
   Then stop.

3. Check if the current repo is indexed:
   ```bash
   test -d .gitnexus/
   ```

4. If not indexed, offer to index now:
   ```
   This repository hasn't been indexed by GitNexus yet.
   Indexing now (this may take a minute for large repos)...
   ```
   Then run:
   ```bash
   npx gitnexus analyze --skip-embeddings
   ```

## Parse Arguments

- `--port PORT`: Custom port (default: 4567)

If `$ARGUMENTS` contains `--port`, extract the port number. Otherwise use 4567.

## Launch Web UI

1. Check if the port is already in use:
   ```bash
   lsof -i :PORT 2>/dev/null | grep LISTEN
   ```

2. If port is in use, inform the user and suggest `--port NEXT_PORT`.

3. Start the GitNexus HTTP server in the background:
   ```bash
   npx gitnexus serve --port PORT &
   ```

4. Report to the user:
   ```
   GitNexus Web UI is running at http://localhost:PORT

   Features available:
   - Symbol browser — search functions, classes, methods
   - Call chain tracing — follow execution flows
   - Dependency graph — WebGL-powered interactive visualization
   - Process flows — see which symbols participate in which processes
   - Community clusters — color-coded functional groups

   The server runs in the background. To stop it:
     kill $(lsof -t -i :PORT)

   Tip: Use this alongside /kiro:debug or /kiro:spec-design to understand
   code structure before making changes.
   ```
