#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
LIB="$HOME/.local/libexec/sandbox"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/sandbox"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/sandbox"
install -d -m 755 "$BIN" "$LIB" "$DATA" "$DATA/libexec" "$CONFIG/apps"
install -m 755 "$ROOT/bin/sandbox" "$BIN/sandbox"
cp -a "$ROOT/libexec/sandboxlib" "$LIB/"
cp -a "$ROOT/images" "$DATA/"
install -m 644 "$ROOT/.dockerignore" "$DATA/.dockerignore"
cp -a "$ROOT/libexec/sandboxlib" "$DATA/libexec/"
# Never overwrite existing user settings or private denylist.
if [[ ! -e "$CONFIG/config.toml" ]]; then
  install -m 600 "$ROOT/examples/config.toml" "$CONFIG/config.toml"
fi
if [[ ! -e "$CONFIG/denylist.txt" ]]; then
  install -m 600 "$ROOT/examples/denylist.txt" "$CONFIG/denylist.txt"
fi
if [[ ! -e "$CONFIG/apps/opencode.jsonc" ]]; then
  install -m 600 "$ROOT/examples/opencode.jsonc" "$CONFIG/apps/opencode.jsonc"
fi
# Launcher finds installed library at ~/.local/libexec/sandbox/sandboxlib.
# Avoid modifying PATH, rootless Docker, or other host settings.
cat <<EOF
Installed sandbox into:
  $BIN/sandbox
  $LIB/sandboxlib
  $DATA/images
  $CONFIG
Ensure $BIN is in PATH.
EOF
