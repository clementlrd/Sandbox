#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
LIB="$HOME/.local/libexec/sandbox"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/sandbox"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/sandbox"

usage() {
  cat <<'HELP'
Usage: ./install.sh [--no-tools | --tools opencode,jq | --tools none]

Installs the sandbox CLI, Docker build resources, and installer scripts.
Interactively prompts for optional tools when stdin is a terminal.
In non-interactive contexts, skips optional tools unless --tools is provided.
HELP
}
CHOICE=''
while (( $# )); do
  case "$1" in
    --no-tools) CHOICE=none; shift ;;
    --tools)
      [[ $# -ge 2 ]] || { echo '--tools needs a comma-separated selection' >&2; exit 2; }
      CHOICE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

install -d -m 755 "$BIN" "$LIB" "$DATA" "$DATA/libexec" "$CONFIG/apps" \
  "$DATA/tools/bin" "$DATA/tool-installers" "$CONFIG/recipes"
install -m 755 "$ROOT/bin/sandbox" "$BIN/sandbox"
# Always install the generic Python environment manager into the shared tools mount.
install -m 755 "$ROOT/tools/sandbox-py" "$DATA/tools/bin/sandbox-py"
# These paths contain installer-managed program resources, not user state.
# Replace instead of overlaying, so deleted compatibility code cannot linger.
rm -rf -- "$LIB/sandboxlib" "$DATA/images" "$DATA/libexec/sandboxlib"
cp -a "$ROOT/libexec/sandboxlib" "$LIB/"
cp -a "$ROOT/images" "$DATA/"
cp -a "$ROOT/libexec/sandboxlib" "$DATA/libexec/"
for script in "$ROOT"/tools/install-*.sh; do
  [[ -f "$script" ]] || continue
  install -m 755 "$script" "$DATA/tool-installers/$(basename "$script")"
done
if [[ -f "$ROOT/.dockerignore" ]]; then
  install -m 644 "$ROOT/.dockerignore" "$DATA/.dockerignore"
else
  cat > "$DATA/.dockerignore" <<'DOCKERIGNORE'
.git
.gitignore
tests/
README.md
*.zip
**/__pycache__
DOCKERIGNORE
  chmod 644 "$DATA/.dockerignore"
  printf 'Note: project .dockerignore missing; installed generated default.\n' >&2
fi
# Existing config/denylist are never overwritten during an upgrade.
if [[ ! -e "$CONFIG/config.toml" ]]; then
  install -m 600 "$ROOT/examples/config.toml" "$CONFIG/config.toml"
fi
if [[ ! -e "$CONFIG/denylist.txt" ]]; then
  install -m 600 "$ROOT/examples/denylist.txt" "$CONFIG/denylist.txt"
fi
if [[ ! -e "$CONFIG/apps/opencode.jsonc" ]]; then
  install -m 600 "$ROOT/examples/opencode.jsonc" "$CONFIG/apps/opencode.jsonc"
fi

# Preserve user-maintained recipes on upgrade. Install only missing examples.
for recipe in "$ROOT"/recipes/*.sh; do
  [[ -f "$recipe" ]] || continue
  dest="$CONFIG/recipes/$(basename "$recipe")"
  if [[ ! -e "$dest" ]]; then
    install -m 700 "$recipe" "$dest"
  fi
done

if [[ -z "$CHOICE" && -t 0 ]]; then
  printf '\nOptional shared tools (installed on the host, mounted RO inside sandboxes):\n'
  printf '  1) OpenCode standalone CLI\n  2) jq\n'
  printf 'Enter numbers separated by spaces, "all", or "none" [none]: '
  IFS= read -r CHOICE || CHOICE=none
fi
CHOICE="${CHOICE:-none}"
# Support --tools opencode,jq, '1 2', and all/none.
CHOICE="${CHOICE//,/ }"
case "$CHOICE" in
  all) CHOICE='opencode jq' ;;
  none) CHOICE='' ;;
esac
selected=()
for item in $CHOICE; do
  case "$item" in
    1|opencode) item=opencode ;;
    2|jq) item=jq ;;
    *) echo "Unknown tool selection: $item (valid: opencode,jq,all,none)" >&2; exit 2 ;;
  esac
  if [[ ! " ${selected[*]:-} " == *" $item "* ]]; then
    selected+=("$item")
  fi
done
for tool in "${selected[@]}"; do
  "$DATA/tool-installers/install-${tool}.sh" "$DATA/tools"
done

cat <<EOF
Installed sandbox into:
  $BIN/sandbox
  $LIB/sandboxlib
  $DATA/images
  $DATA/tools/bin  (sandbox-py always installed; mounted read-only at /opt/tools/bin)
  $DATA/tool-installers
  $CONFIG/recipes (trusted host-side setup recipes)
  $CONFIG
Existing config, denylist, recipes, tools and sandboxes were preserved.
Existing config files must be migrated manually if they contain removed keys.
Run: sandbox runtimes
Ensure $BIN is in PATH.
EOF
