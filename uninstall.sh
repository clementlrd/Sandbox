#!/usr/bin/env bash
# Safely remove the installed sandbox CLI. Never delete sandboxes by default.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ./uninstall.sh [--purge-data] [--yes]

Without options: remove only installed sandbox CLI, helper code, Docker build
recipes and the sandbox-py helper. KEEP config, denylist, trusted recipes,
shared external tools (OpenCode/jq), sandbox repositories, Docker volumes,
images and build cache.

--purge-data: additionally delete host-side configuration, shared tools and
             state. Requires zero saved sandbox instances and explicit
             confirmation. Use 'sandbox destroy NAME --yes' for each sandbox
             BEFORE uninstalling if you want its Docker volumes removed.
--yes:        allow non-interactive --purge-data (destructive).
USAGE
}

PURGE=0
YES=0
while (( $# )); do
  case "$1" in
    --purge-data) PURGE=1; shift ;;
    --yes) YES=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

BIN="$HOME/.local/bin"
LIB="$HOME/.local/libexec/sandbox"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/sandbox"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/sandbox"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/sandbox"

# Always refuse deleting user data if a named sandbox still exists. This also
# prevents accidentally leaving named Docker volumes orphaned during a purge.
if (( PURGE )); then
  if [[ -d "$STATE/instances" ]] &&
       [[ -n "$(find "$STATE/instances" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "Refusing --purge-data: saved sandboxes exist under $STATE/instances" >&2
    echo "Import work, then run 'sandbox destroy NAME --yes' for each one first." >&2
    exit 1
  fi
  if (( ! YES )); then
    [[ -t 0 ]] || { echo 'Use --yes or run interactively for --purge-data' >&2; exit 2; }
    printf 'Permanently delete sandbox config, recipes, tools, and state? Type DELETE: '
    IFS= read -r response || response=''
    [[ "$response" == DELETE ]] || { echo 'Cancelled.'; exit 1; }
  fi
  for path in "$DATA" "$CONFIG" "$STATE"; do
    # Defense against dangerous XDG values and symlink targets.
    [[ "$path" == /* && "$path" != / && "$path" != "$HOME" && ! -L "$path" ]] || {
      echo "Unsafe purge path: $path" >&2; exit 1;
    }
  done
fi

rm -f -- "$BIN/sandbox" "$DATA/tools/bin/sandbox-py" "$DATA/.dockerignore"
rm -rf -- "$LIB/sandboxlib" "$DATA/libexec/sandboxlib" \
          "$DATA/images" "$DATA/tool-installers"
rmdir -- "$LIB" "$DATA/libexec" "$DATA/tools/bin" "$DATA/tools" "$DATA" 2>/dev/null || true

if (( PURGE )); then
  rm -rf -- "$DATA" "$CONFIG" "$STATE"
  echo 'Deleted local sandbox configuration, recipes, tools, and state.'
fi

cat <<'DONE'
Removed sandbox-installed executables and build resources.
Docker images and build cache were NOT removed.
Docker volumes were NOT removed by this script.
Original Git repositories were NOT modified.
DONE
if (( ! PURGE )); then
  echo "Preserved: $CONFIG, $STATE, and remaining files under $DATA"
fi
