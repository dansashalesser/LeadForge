#!/bin/bash
# Global daily maintenance orchestrator.
# Reads $SDD_HARNESS/projects.txt and dispatches each repo's
# .claude/scripts/orchestration/daily-runner.sh. Per-repo failures are isolated and logged;
# the loop never aborts midway.
#
# Usage:
#   daily-orchestrator.sh             — run all repos
#   daily-orchestrator.sh --dry-run   — print what would happen
#   daily-orchestrator.sh --repo PATH — run only the given repo

set -u

# Self-locate the harness root (no hardcoded paths — see scripts/lib/resolve-harness-dir.sh)
__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$__here/../lib/resolve-harness-dir.sh"
. "$__here/../lib/env-detect.sh"
PROJECTS_FILE="$HARNESS_DIR/projects.txt"
LOG_FILE="$HARNESS_DIR/logs/orchestrator.log"
ERR_LOG="$HARNESS_DIR/logs/orchestrator-errors.log"
mkdir -p "$(dirname "$LOG_FILE")"
detect_host_env

DRY_RUN=false
SINGLE_REPO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --repo)
      [ $# -lt 2 ] && { echo "--repo requires a path argument" >&2; echo "$(date -Iseconds) orchestrator: --repo requires a path argument" >> "$ERR_LOG"; exit 2; }
      shift
      SINGLE_REPO="$1"
      ;;
    *) echo "unknown arg: $1" >&2; echo "$(date -Iseconds) orchestrator: unknown arg: $1" >> "$ERR_LOG"; exit 2 ;;
  esac
  shift
done

# --- Fail-loud instrumentation ---
# A run that dies before the repo loop (bad env, resolve-harness-dir.sh failure,
# missing projects.txt) must never look identical to a zero-work success. Every
# real invocation logs a start line and, via the EXIT trap, an end line — so a
# silent zero-byte run becomes structurally visible (either both lines appear,
# or the crash happened before the trap installed, which the ERR_LOG catches
# below).
PROCESSED=0
if [ "$DRY_RUN" = false ]; then
  MODE="${SINGLE_REPO:+repo:$SINGLE_REPO}"
  MODE="${MODE:-fleet}"
  echo "$(date -Iseconds) orchestrator: run started (mode=$MODE)" >> "$LOG_FILE"
  trap '__ec=$?; echo "$(date -Iseconds) orchestrator: run finished exit=$__ec repos=$PROCESSED" >> "$LOG_FILE"' EXIT
fi

# --- Harness-level periodic task: sync the harness into every registered project ---
# Nothing else ever runs update.sh. stop-hook.sh only prints "Run: update.sh" and
# then waits for a human to do it, so a harness fix could sit unapplied in a project
# indefinitely — that is how a settings.json broken by an old template survived for
# months in an installed repo. Syncing here, before the per-repo runners, means every
# registered project picks up harness changes without anyone typing anything.
# Runs once per calendar day. Opt out with SDD_SKIP_HARNESS_SYNC=1.
SYNC_STATE="$HARNESS_DIR/.last-harness-sync"

if [ "$DRY_RUN" = true ]; then
  echo "[would-sync] $HARNESS_DIR/update.sh ${SINGLE_REPO:-(all registered projects)}"
elif [ "${SDD_SKIP_HARNESS_SYNC:-0}" != "1" ] && [ -f "$HARNESS_DIR/update.sh" ]; then
  # Day-string compare (portable; avoids GNU-only `date -d`), same guard shape as run_one.
  sync_last_day="$(cut -dT -f1 "$SYNC_STATE" 2>/dev/null || echo "")"
  if [ "$sync_last_day" != "$(date +%Y-%m-%d)" ]; then
    ts="$(date -Iseconds)"
    # Never run a half-written update.sh across the whole fleet — a syntax error here
    # would touch every registered repo. Parse first, sync second.
    if ! bash -n "$HARNESS_DIR/update.sh" 2>/dev/null; then
      echo "$ts harness: update.sh has a syntax error — sync skipped" >> "$LOG_FILE"
      echo "$ts harness: update.sh failed bash -n, fleet sync skipped" >> "$ERR_LOG"
    else
      sync_start=$(date +%s)
      sync_errbuf=$(mktemp)
      bash "$HARNESS_DIR/update.sh" ${SINGLE_REPO:+"$SINGLE_REPO"} > /dev/null 2>"$sync_errbuf"
      sync_exit=$?
      echo "$ts harness: update.sh sync exit=$sync_exit duration=$(($(date +%s) - sync_start))s target=${SINGLE_REPO:-fleet}" >> "$LOG_FILE"
      if [ "$sync_exit" -eq 0 ]; then
        echo "$ts" > "$SYNC_STATE"
      elif [ -s "$sync_errbuf" ]; then
        { echo "--- $ts harness update.sh sync (exit=$sync_exit) ---"; cat "$sync_errbuf"; } >> "$ERR_LOG"
      fi
      rm -f "$sync_errbuf"
    fi
  fi
fi

run_one() {
  local repo="$1"
  local ts="$(date -Iseconds)"

  [ "$DRY_RUN" = false ] && PROCESSED=$((PROCESSED + 1))

  if [ ! -d "$repo/.claude" ]; then
    if [ "$DRY_RUN" = true ]; then
      echo "[orphan] $repo"
    else
      echo "$ts [orphan] $repo — not installed" >> "$LOG_FILE"
    fi
    return 0
  fi

  local fs_class="$(classify_repo_fs "$repo")"
  if [ "$fs_class" = "cross-fs" ]; then
    if [ "$DRY_RUN" = true ]; then
      echo "[cross-fs] $repo"
    else
      echo "$ts orchestrator: WARNING $repo is cross-filesystem (perf risk, confirmed not fixable via dispatch — migrate to a native path)" >> "$LOG_FILE"
    fi
  fi

  local state="$repo/.claude/memory/.last-routine-run"
  local last="never"
  [ -f "$state" ] && last="$(cat "$state")"

  if [ "$DRY_RUN" = true ]; then
    echo "[would-run] $repo (last=$last)"
    return 0
  fi

  # Only call daily-runner.sh if it hasn't run today — avoids "exit 0 · 0s" audit
  # clutter when session-start hook already fired it earlier. Sub-runners below still
  # execute on their own schedules regardless.
  local today="$(date +%Y-%m-%d)"
  local last_day="$(cut -dT -f1 "$state" 2>/dev/null || echo "")"
  if [ "$last_day" != "$today" ]; then
    local start=$(date +%s)
    local dm_errbuf; dm_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/orchestration/daily-runner.sh) > /dev/null 2>"$dm_errbuf"
    local exit_code=$?
    local duration=$(($(date +%s) - start))
    echo "$ts $repo exit=$exit_code duration=${duration}s" >> "$LOG_FILE"
    if [ "$exit_code" -ne 0 ] && [ -s "$dm_errbuf" ]; then
      { echo "--- $ts $repo daily-maintenance (exit=$exit_code) ---"; cat "$dm_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$dm_errbuf"
  fi

  # Macro-eval sweep — self-paces to ~twice a week via its own MIN_GAP_DAYS guard,
  # so calling it daily is cheap (it no-ops between runs). Failure-isolated; the
  # sweep's own Step-0 preflight handles an unreachable Raindrop MCP. Opt out with
  # SDD_SKIP_MACRO_EVAL=1.
  if [ "${SDD_SKIP_MACRO_EVAL:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/macro-eval-runner.sh" ]; then
    local me_start=$(date +%s)
    local me_errbuf; me_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/macro-eval-runner.sh) > /dev/null 2>"$me_errbuf"
    local me_exit=$?
    echo "$ts $repo macro-eval exit=$me_exit duration=$(($(date +%s) - me_start))s" >> "$LOG_FILE"
    if [ "$me_exit" -ne 0 ] && [ -s "$me_errbuf" ]; then
      { echo "--- $ts $repo macro-eval (exit=$me_exit) ---"; cat "$me_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$me_errbuf"
  fi

  # Skill-curator sweep — self-paces to weekly (MIN_GAP_DAYS=7). Harness-only;
  # non-harness repos exit 0 immediately. Opt out with SDD_SKIP_SKILL_CURATOR=1.
  if [ "${SDD_SKIP_SKILL_CURATOR:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/skill-curator-runner.sh" ]; then
    local sc_start=$(date +%s)
    local sc_errbuf; sc_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/skill-curator-runner.sh) > /dev/null 2>"$sc_errbuf"
    local sc_exit=$?
    echo "$ts $repo skill-curator exit=$sc_exit duration=$(($(date +%s) - sc_start))s" >> "$LOG_FILE"
    if [ "$sc_exit" -ne 0 ] && [ -s "$sc_errbuf" ]; then
      { echo "--- $ts $repo skill-curator (exit=$sc_exit) ---"; cat "$sc_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$sc_errbuf"
  fi

  # Harness-health sweep — self-paces to bi-weekly (MIN_GAP_DAYS=13). Harness-only;
  # non-harness repos exit 0 immediately. Opt out with SDD_SKIP_HARNESS_HEALTH=1.
  if [ "${SDD_SKIP_HARNESS_HEALTH:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/harness-health-runner.sh" ]; then
    local hh_start=$(date +%s)
    local hh_errbuf; hh_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/harness-health-runner.sh) > /dev/null 2>"$hh_errbuf"
    local hh_exit=$?
    echo "$ts $repo harness-health exit=$hh_exit duration=$(($(date +%s) - hh_start))s" >> "$LOG_FILE"
    if [ "$hh_exit" -ne 0 ] && [ -s "$hh_errbuf" ]; then
      { echo "--- $ts $repo harness-health (exit=$hh_exit) ---"; cat "$hh_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$hh_errbuf"
  fi

  # Tool-failure review — promotes recurring Bash/MCP failures into memory so the
  # system learns from its mistakes. Self-paces to ~twice a week (MIN_GAP_DAYS=3)
  # and no-ops unless the ledger has a promotable entry, so calling it daily is
  # cheap. Applies to every repo. Opt out with SDD_SKIP_TOOL_FAILURE_REVIEW=1.
  if [ "${SDD_SKIP_TOOL_FAILURE_REVIEW:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/tool-failure-review-runner.sh" ]; then
    local tf_start=$(date +%s)
    local tf_errbuf; tf_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/tool-failure-review-runner.sh) > /dev/null 2>"$tf_errbuf"
    local tf_exit=$?
    echo "$ts $repo tool-failure-review exit=$tf_exit duration=$(($(date +%s) - tf_start))s" >> "$LOG_FILE"
    if [ "$tf_exit" -ne 0 ] && [ -s "$tf_errbuf" ]; then
      { echo "--- $ts $repo tool-failure-review (exit=$tf_exit) ---"; cat "$tf_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$tf_errbuf"
  fi

  # Code-review learning sweep — compares pr-babysit's logged reviews against real
  # human review activity on merged PRs, promotes low-risk findings into memory, and
  # reports higher-risk methodology changes for human approval. Cheaply no-ops unless
  # there's a merged+logged PR not yet processed; self-paces to weekly (MIN_GAP_DAYS=7)
  # once there is. Applies to any repo. Opt out with SDD_SKIP_CODE_REVIEW_LEARNING=1.
  if [ "${SDD_SKIP_CODE_REVIEW_LEARNING:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/code-review-learning-runner.sh" ]; then
    local crl_start=$(date +%s)
    local crl_errbuf; crl_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/code-review-learning-runner.sh) > /dev/null 2>"$crl_errbuf"
    local crl_exit=$?
    echo "$ts $repo code-review-learning exit=$crl_exit duration=$(($(date +%s) - crl_start))s" >> "$LOG_FILE"
    if [ "$crl_exit" -ne 0 ] && [ -s "$crl_errbuf" ]; then
      { echo "--- $ts $repo code-review-learning (exit=$crl_exit) ---"; cat "$crl_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$crl_errbuf"
  fi

  # Security report — daily static security scan of recent git changes. Writes a
  # safety report to .claude/reports/security/<date>-security-report.md. Self-paces
  # to daily (MIN_GAP_DAYS=1). Applies to every repo.
  # Opt out with SDD_SKIP_SECURITY_REPORT=1.
  if [ "${SDD_SKIP_SECURITY_REPORT:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/security-report-runner.sh" ]; then
    local sr_start=$(date +%s)
    local sr_errbuf; sr_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/security-report-runner.sh) > /dev/null 2>"$sr_errbuf"
    local sr_exit=$?
    echo "$ts $repo security-report exit=$sr_exit duration=$(($(date +%s) - sr_start))s" >> "$LOG_FILE"
    if [ "$sr_exit" -ne 0 ] && [ -s "$sr_errbuf" ]; then
      { echo "--- $ts $repo security-report (exit=$sr_exit) ---"; cat "$sr_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$sr_errbuf"
  fi

  # Startup-payload audit — deterministic (no LLM). Measures the fixed per-session
  # token tax (CLAUDE.md + @imports + .claude/rules + auto-loaded MEMORY.md) that
  # RTK/lean-ctx/Headroom don't cover, and writes a JSON the dashboard's Context Health
  # tab reads. Self-paces to daily via its own state-file guard, so calling it daily is
  # cheap. Applies to every repo. Opt out with SDD_SKIP_STARTUP_AUDIT=1.
  if [ "${SDD_SKIP_STARTUP_AUDIT:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/startup-payload-audit.sh" ]; then
    local sp_start=$(date +%s)
    local sp_errbuf; sp_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/startup-payload-audit.sh) > /dev/null 2>"$sp_errbuf"
    local sp_exit=$?
    echo "$ts $repo startup-payload exit=$sp_exit duration=$(($(date +%s) - sp_start))s" >> "$LOG_FILE"
    if [ "$sp_exit" -ne 0 ] && [ -s "$sp_errbuf" ]; then
      { echo "--- $ts $repo startup-payload (exit=$sp_exit) ---"; cat "$sp_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$sp_errbuf"
  fi

  # RTK net-effect — deterministic (no LLM). Measures Bash rerun rate and Read
  # reread rate from transcripts, writing a JSON the dashboard's RTK layer note
  # reads — a global signal alongside RTK's own local-savings number, not a
  # replacement for it. Self-paces to daily via its own state-file guard, so
  # calling it daily is cheap. Applies to every repo. Opt out with
  # SDD_SKIP_RTK_NET_EFFECT=1.
  if [ "${SDD_SKIP_RTK_NET_EFFECT:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/rtk-net-effect-runner.sh" ]; then
    local rne_start=$(date +%s)
    local rne_errbuf; rne_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/rtk-net-effect-runner.sh) > /dev/null 2>"$rne_errbuf"
    local rne_exit=$?
    echo "$ts $repo rtk-net-effect exit=$rne_exit duration=$(($(date +%s) - rne_start))s" >> "$LOG_FILE"
    if [ "$rne_exit" -ne 0 ] && [ -s "$rne_errbuf" ]; then
      { echo "--- $ts $repo rtk-net-effect (exit=$rne_exit) ---"; cat "$rne_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$rne_errbuf"
  fi

  # Risk-zone reseed — refreshes .claude/steering/risk-zones.md from git churn +
  # test-file presence + gitnexus impact. Consumed by risk-zone-edit-gate-hook.sh
  # and pr-risk-tier-hook.sh. Self-paces to weekly (MIN_GAP_DAYS=7) since risk
  # zones drift slowly. Applies to every repo. Opt out with SDD_SKIP_RISK_ZONE=1.
  if [ "${SDD_SKIP_RISK_ZONE:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/risk-zone-reseed-runner.sh" ]; then
    local rz_start=$(date +%s)
    local rz_errbuf; rz_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/risk-zone-reseed-runner.sh) > /dev/null 2>"$rz_errbuf"
    local rz_exit=$?
    echo "$ts $repo risk-zone-reseed exit=$rz_exit duration=$(($(date +%s) - rz_start))s" >> "$LOG_FILE"
    if [ "$rz_exit" -ne 0 ] && [ -s "$rz_errbuf" ]; then
      { echo "--- $ts $repo risk-zone-reseed (exit=$rz_exit) ---"; cat "$rz_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$rz_errbuf"
  fi

  # Hook/MCP-config self-audit — sweeps the repo's own .claude/hooks/ and
  # settings.json/.mcp.json for secrets (via scan-pii.sh), network-exfil
  # patterns, and over-broad permission grants. Deterministic (no LLM call).
  # Self-paces to weekly (MIN_GAP_DAYS=7). Applies to every repo. Opt out with
  # SDD_SKIP_HOOK_CONFIG_AUDIT=1.
  if [ "${SDD_SKIP_HOOK_CONFIG_AUDIT:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/hook-config-audit-runner.sh" ]; then
    local hca_start=$(date +%s)
    local hca_errbuf; hca_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/hook-config-audit-runner.sh) > /dev/null 2>"$hca_errbuf"
    local hca_exit=$?
    echo "$ts $repo hook-config-audit exit=$hca_exit duration=$(($(date +%s) - hca_start))s" >> "$LOG_FILE"
    if [ "$hca_exit" -ne 0 ] && [ -s "$hca_errbuf" ]; then
      { echo "--- $ts $repo hook-config-audit (exit=$hca_exit) ---"; cat "$hca_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$hca_errbuf"
  fi

  # Daily briefing — synthesizes a prioritized status digest from
  # .claude/memory/manager/{projects,people}.md plus connected live sources. Self-paces
  # to daily (MIN_GAP_DAYS=1); the runner's own deterministic guard no-ops for free
  # (no LLM call) until the ledger is bootstrapped interactively via /kiro:daily-briefing.
  # Applies to every repo. Opt out with SDD_SKIP_DAILY_BRIEFING=1.
  if [ "${SDD_SKIP_DAILY_BRIEFING:-0}" != "1" ] && [ -f "$repo/.claude/scripts/routines/daily-briefing-runner.sh" ]; then
    local db_start=$(date +%s)
    local db_errbuf; db_errbuf=$(mktemp)
    (cd "$repo" && bash .claude/scripts/routines/daily-briefing-runner.sh) > /dev/null 2>"$db_errbuf"
    local db_exit=$?
    echo "$ts $repo daily-briefing exit=$db_exit duration=$(($(date +%s) - db_start))s" >> "$LOG_FILE"
    if [ "$db_exit" -ne 0 ] && [ -s "$db_errbuf" ]; then
      { echo "--- $ts $repo daily-briefing (exit=$db_exit) ---"; cat "$db_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$db_errbuf"
  fi
}

if [ -n "$SINGLE_REPO" ]; then
  run_one "$SINGLE_REPO"
else
  if [ ! -f "$PROJECTS_FILE" ]; then
    echo "projects.txt missing at $PROJECTS_FILE" >&2
    echo "$(date -Iseconds) orchestrator: projects.txt missing at $PROJECTS_FILE" >> "$ERR_LOG"
    exit 1
  fi
  while IFS= read -r repo || [ -n "$repo" ]; do
    [ -z "$repo" ] && continue
    [ "${repo:0:1}" = "#" ] && continue   # allow comments
    run_one "$repo"
  done < "$PROJECTS_FILE"

  # Roster completeness — a harness-installed repo that never made it into
  # projects.txt gets zero routines and shows up nowhere, because the dashboard only
  # renders repos it is told about. Absence is invisible unless something looks for
  # it, so look for it on every fleet run. Never fatal: this is a report, not a gate.
  REG_CHECK="$HARNESS_DIR/scripts/utils/check-fleet-registration.sh"
  if [ "$DRY_RUN" = false ] && [ -f "$REG_CHECK" ]; then
    reg_errbuf="$(mktemp)"
    if ! bash "$REG_CHECK" --quiet > "$reg_errbuf" 2>&1; then
      echo "$(date -Iseconds) orchestrator: unregistered harness repo(s) found" >> "$LOG_FILE"
      { echo "--- $(date -Iseconds) fleet registration ---"; cat "$reg_errbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$reg_errbuf"
  fi
fi

# --- Harness-level periodic task: repo drift review ---
# Gated on elapsed days since the last *successful* run (like skill-curator /
# security-report), not calendar day-of-week. A day-of-week gate can only ever
# fire on Wednesday — if the machine is asleep/logged-out through every trigger
# window that day, the whole week is silently lost. An elapsed-days gate is
# self-healing: whichever day the orchestrator next actually runs, if due, it runs.
DRIFT_STATE="$HARNESS_DIR/.last-drift-review"
DRIFT_GAP_DAYS="${DRIFT_REVIEW_GAP_DAYS:-7}"

if [ "$DRY_RUN" = false ]; then
  DRIFT_DUE=true
  if [ -s "$DRIFT_STATE" ]; then
    # `date -d` is GNU-only: on macOS it always failed, LAST_DRIFT_EPOCH fell back to 0,
    # and the gate below was skipped — so this "weekly" review fired on EVERY run, one
    # full `claude --print` session each time. Python3 does the epoch math portably
    # (same approach as the CLAUDE.md review gate in session-start-hook.sh).
    DRIFT_GAP="$(python3 -c "
import datetime, sys
raw = open('$DRIFT_STATE').read().strip()[:10]
print((datetime.date.today() - datetime.date.fromisoformat(raw)).days)
" 2>/dev/null || echo "")"
    if [ -n "$DRIFT_GAP" ] && [ "$DRIFT_GAP" -lt "$DRIFT_GAP_DAYS" ] 2>/dev/null; then
      DRIFT_DUE=false
    fi
  fi
  if [ "$DRIFT_DUE" = true ]; then
    ts="$(date -Iseconds)"
    echo "$ts harness: starting drift review" >> "$LOG_FILE"
    drift_errbuf=$(mktemp)
    drift_outbuf=$(mktemp)
    echo "Read ~/.claude/skill-library/repo-drift-review/SKILL.md and follow it to sweep the SDD harness for drift. Auto-fix what you can. Write the summary to $HARNESS_DIR/reports/drift-review-report.md" | \
      claude --print --output-format text --permission-mode bypassPermissions > "$drift_outbuf" 2>"$drift_errbuf"
    drift_exit=$?
    echo "$ts harness: drift review exit=$drift_exit" >> "$LOG_FILE"
    if [ "$drift_exit" -eq 0 ]; then
      echo "$ts" > "$DRIFT_STATE"
    elif [ -s "$drift_errbuf" ] || [ -s "$drift_outbuf" ]; then
      { echo "--- $ts harness drift-review (exit=$drift_exit) ---"; cat "$drift_errbuf" "$drift_outbuf"; } >> "$ERR_LOG"
    fi
    rm -f "$drift_errbuf" "$drift_outbuf"
  fi
fi
