#!/usr/bin/env bash
# detect_base_and_create.test.sh — exercise the auto-create path in a throwaway
# git tree with `gh` stubbed, so the Evidence placeholder and the PR-number
# read-back are proved rather than assumed.
#
# Run: bash scripts/pr/detect_base_and_create.test.sh
#
# The stub records what the script sent to `gh api -X PATCH`, which is the only
# way to see the composed body without opening a real PR.

set -u

__here="$(cd -P "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SCRIPT="$__here/detect_base_and_create.sh"

PASS=0
FAIL=0

check() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*)
      printf '  ok    %-46s\n' "$label"
      PASS=$((PASS + 1)) ;;
    *)
      printf '  FAIL  %-46s missing: %s\n' "$label" "$needle"
      FAIL=$((FAIL + 1)) ;;
  esac
}

check_absent() {
  local label="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*)
      printf '  FAIL  %-46s unexpectedly present: %s\n' "$label" "$needle"
      FAIL=$((FAIL + 1)) ;;
    *)
      printf '  ok    %-46s\n' "$label"
      PASS=$((PASS + 1)) ;;
  esac
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ---- stub gh -------------------------------------------------------------
STUB="$WORK/bin"
mkdir -p "$STUB"
cat > "$STUB/gh" <<'GHEOF'
#!/usr/bin/env bash
# Minimal gh stub. State lives in $GH_STATE so `pr list` can answer "no PR yet"
# before create and "PR 42" after, which is what the script depends on.
set -u
ARGS="$*"
case "$ARGS" in
  "pr list"*)
    [ -f "$GH_STATE/created" ] && echo 42
    exit 0 ;;
  "pr create"*)
    touch "$GH_STATE/created"
    echo "$ARGS" > "$GH_STATE/create-args"
    echo "https://github.com/o/r/pull/42"
    exit 0 ;;
  "pr view"*)
    echo "Filled from commit messages."
    exit 0 ;;
  "api -X PATCH"*)
    for a in "$@"; do
      case "$a" in
        body=@*) cp "${a#body=@}" "$GH_STATE/patched-body" ;;
      esac
    done
    exit 0 ;;
esac
exit 0
GHEOF
chmod +x "$STUB/gh"

# ---- throwaway repo ------------------------------------------------------
REPO="$WORK/repo"
mkdir -p "$REPO"
git -C "$REPO" init -q -b master
git -C "$REPO" config user.email t@t.t
git -C "$REPO" config user.name t
echo base > "$REPO/f.txt"
git -C "$REPO" add -A
git -C "$REPO" commit -qm "base"
git -C "$REPO" checkout -qb feature
echo change > "$REPO/f.txt"
git -C "$REPO" commit -qam "feature work"

export GH_STATE="$WORK/state"
mkdir -p "$GH_STATE"

OUT="$(cd "$REPO" && PATH="$STUB:$PATH" bash "$SCRIPT" 2>&1)"
RC=$?

echo "== script run =="
check "exits 0 (never fails the caller)" "rc=$RC" "rc=0"
check "reports the PR number"            "$OUT" "PR #42"
check "detected base branch"             "$OUT" "master"
check "announces the placeholder"        "$OUT" "placeholder '## Evidence' section"
check "tells the agent to replace it"    "$OUT" "before undrafting"
check_absent "no read-back warning"      "$OUT" "could not be read back"
check_absent "no PATCH warning"          "$OUT" "add it by hand"

echo
echo "== composed PR body =="
BODY="$(cat "$GH_STATE/patched-body" 2>/dev/null || echo "<<NO PATCH SENT>>")"
check "preserves the --fill body"        "$BODY" "Filled from commit messages."
check "carries the Evidence marker"      "$BODY" "## Evidence"
check "states it was not captured"       "$BODY" "Not captured"
check "says why (opened on push)"        "$BODY" "opened automatically on push"
check "points at the create-pr skill"    "$BODY" "Attach Runtime Evidence"

echo
echo "== created as a draft =="
CREATE_ARGS="$(cat "$GH_STATE/create-args" 2>/dev/null || echo "")"
check "draft flag passed"                "$CREATE_ARGS" "--draft"
check "base passed explicitly"           "$CREATE_ARGS" "--base master"

echo
echo "== idempotency: re-run with a PR already open =="
OUT2="$(cd "$REPO" && PATH="$STUB:$PATH" bash "$SCRIPT" 2>&1)"
check "second run is a no-op"            "$OUT2" "already open against its base"
check_absent "no duplicate Evidence add" "$OUT2" "placeholder '## Evidence' section"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
