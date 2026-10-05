# Daily Security Scan — TODAY_PLACEHOLDER

You are performing the daily automated security scan for this repository.
Run silently and efficiently. Do not ask questions. Write the report and exit.

## Step 0: Preflight

Check that this is a code repository with source files to scan:
- If `.claude/memory/` does not exist, write a one-line SKIPPED note and exit 0.
- If there are no source files (`.py`, `.ts`, `.js`, `.go`, `.java`, `.rb`, `.rs`), write a one-line SKIPPED note and exit 0.

## Step 1: Scope — What Changed

Run: `git log --since="25 hours ago" --name-only --pretty=format:"%H %s" -- '*.py' '*.ts' '*.js' '*.go' '*.java' '*.rb' '*.rs' '*.json' '*.yaml' '*.yml' '*.env*' '*.toml'`

If no files changed in the last 25h, fall back to: `git diff HEAD~1 --name-only`

Collect the list of changed files. This is your scan scope — focus analysis here.
If the repo has no git history, scan all source files (limit to top 20 by risk heuristic).

## Step 2: Security Scan

For each file in scope, read it and check for:

**Critical patterns (flag immediately):**
- Hardcoded secrets: API keys, passwords, tokens in source (not env vars)
- SQL string concatenation with unescaped user input
- `eval()`, `exec()`, `os.system()`, `subprocess.call(shell=True)` with user-controlled input
- Path traversal: user input used in `open()`, `Path()`, or file operations without sanitization
- Deserialization of untrusted data: `pickle.loads()`, `yaml.load()` (not `safe_load`), `json.loads()` on raw user bytes
- Disabled security controls: `verify=False` in HTTP calls, `DEBUG=True` in production config

**High patterns:**
- Missing auth/authz checks on endpoints that modify data
- JWT/token validation gaps: missing signature check, `alg: none` acceptance
- SSRF: user-controlled URLs passed to HTTP clients
- User input reaching log statements without sanitization (log injection)
- Insecure direct object references (IDOR): entity IDs from user input without ownership check

**Medium patterns:**
- Missing rate limiting on auth endpoints
- Overly broad CORS (`*` allow-origin on credentialed endpoints)
- Sensitive data in error messages or stack traces exposed to client
- Dependency files changed: note if `requirements.txt`, `package.json`, `go.mod` changed (flag for manual `npm audit` / `pip-audit`)

## Step 3: Write Report

Write the report to `.claude/reports/security/TODAY_PLACEHOLDER-security-report.md`:

```markdown
# Daily Security Report — TODAY_PLACEHOLDER

## Summary
- Files scanned: N (from git changes in last 24h)
- Findings: N critical, N high, N medium
- Status: CLEAN | ISSUES FOUND

## Findings

### [CRITICAL] <title>
**File:** `path/to/file.py:42`
**Pattern:** <what was found>
**Risk:** <what an attacker can do>
**Fix:** <one-line fix recommendation>

### [HIGH] <title>
...

### [MEDIUM] <title>
...

## Dependency Changes
<!-- Only if dependency files were in scope -->
- `requirements.txt` changed — run `pip-audit` to check for new CVEs
- `package.json` changed — run `npm audit`

## Clean Files
Files scanned with no issues: <comma-separated list>

## Notes
<!-- Any context that helps interpret the findings -->
```

If no issues found, write:
```markdown
# Daily Security Report — TODAY_PLACEHOLDER

## Summary
- Files scanned: N
- Findings: 0
- Status: CLEAN

## Files Scanned
<list>
```

## Step 4: Done

Output exactly one line to stdout: `security-report: TODAY_PLACEHOLDER complete — N findings (C critical, H high, M medium)`

Do not output anything else to stdout. The report is in the file.
