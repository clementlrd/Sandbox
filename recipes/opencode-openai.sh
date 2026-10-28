#!/usr/bin/env bash
# Transfer the already-authenticated host OpenCode OpenAI credentials
# into one sandbox's persistent OpenCode data. Never prints the secret.
set -euo pipefail

name="${1:?usage: opencode-openai.sh SANDBOX_NAME}"
: "${SANDBOX_CLI:?SANDBOX_CLI must be provided by sandbox setup}"
: "${SANDBOX_TOOLS_DIR:?SANDBOX_TOOLS_DIR must be provided by sandbox setup}"

opencode="$SANDBOX_TOOLS_DIR/bin/opencode"
if [[ ! -x "$opencode" ]]; then
  echo "OpenCode binary is missing at $opencode; install it first." >&2
  exit 1
fi

# Pipe secrets via stdin; avoid saving them on the host or passing them as args.
# --interactive (but no --tty for piped stdin) is managed by sandbox exec.
"$opencode" auth export openai | "$SANDBOX_CLI" exec "$name" --runtime "${SANDBOX_RUNTIME:-base}" -- /bin/sh -c '
  set -eu
  umask 077
  tmp=$(mktemp)
  trap '\''rm -f -- "$tmp"'\'' EXIT
  cat > "$tmp"
  test -s "$tmp" || { echo "Credential export was empty" >&2; exit 1; }
  opencode auth import "$tmp"
'
