#!/usr/bin/env bash
# Transfer an explicitly chosen OpenCode provider from the authenticated
# host OpenCode installation to this sandbox. Never echoes credentials.
#
# Usage: SANDBOX_PROVIDER=anthropic sandbox setup NAME opencode-provider
#        SANDBOX_PROVIDER=openai sandbox setup NAME opencode-provider --force
set -euo pipefail

name="${1:?usage: opencode-provider.sh SANDBOX_NAME}"
: "${SANDBOX_CLI:?SANDBOX_CLI must be provided by sandbox setup}"
: "${SANDBOX_TOOLS_DIR:?SANDBOX_TOOLS_DIR must be provided by sandbox setup}"
: "${SANDBOX_PROVIDER:?Set SANDBOX_PROVIDER to a provider ID (e.g. openai or anthropic)}"

# Reject whitespace and shell metacharacters; never interpolate the ID
# into code passed to a shell. The ID is always passed as one argument.
if [[ ! "$SANDBOX_PROVIDER" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]]; then
  echo "Invalid SANDBOX_PROVIDER: expected a provider ID" >&2
  exit 2
fi

opencode="$SANDBOX_TOOLS_DIR/bin/opencode"
if [[ ! -x "$opencode" ]]; then
  echo "OpenCode binary missing at $opencode; install it first." >&2
  exit 1
fi

# Export on the host, pipe to a private temporary file in the container,
# then let OpenCode import into the sandbox's persistent app-data volume.
# Neither the token nor its JSON is written to the host filesystem.
"$opencode" auth export "$SANDBOX_PROVIDER" | \
  "$SANDBOX_CLI" exec "$name" --runtime "${SANDBOX_RUNTIME:-base}" -- /bin/sh -c '
    set -eu
    umask 077
    tmp=$(mktemp)
    trap "rm -f -- \"$tmp\"" EXIT
    cat > "$tmp"
    test -s "$tmp" || { echo "Credential export was empty" >&2; exit 1; }
    opencode auth import "$tmp"
  '
