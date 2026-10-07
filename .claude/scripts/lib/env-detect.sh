#!/bin/bash
# Single source of truth for host-environment detection, sourced by
# daily-orchestrator.sh. Not a dispatch/rerouting layer — an A/B test
# confirmed native-Windows-side dispatch does NOT fix /mnt/c cross-fs
# slowness (likely Defender/AV scanning, not the WSL 9p bridge itself),
# so this only surfaces the risk in logs rather than pretend to solve it.

detect_host_env() {
  HOST_OS="$(uname -s)"                     # Linux | Darwin | MINGW*/CYGWIN*
  IS_WSL=false
  if [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
  fi
}

# "cross-fs" = repo path lives on a different OS's filesystem than the one
# actually executing — the one situation proven to be a real perf risk.
# Native Linux, native macOS, and WSL-native paths under WSL are all "native".
classify_repo_fs() {
  local repo="$1"
  if [ "$IS_WSL" = true ] && [[ "$repo" == /mnt/* ]]; then
    echo "cross-fs"
  else
    echo "native"
  fi
}
