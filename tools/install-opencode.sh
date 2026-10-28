#!/usr/bin/env bash
# Install the official OpenCode 2 standalone Linux glibc baseline executable.
set -euo pipefail
TOOLS="${1:-${XDG_DATA_HOME:-$HOME/.local/share}/sandbox/tools}"
VERSION="${OPENCODE_VERSION:-latest}"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'OpenCode installer currently supports Linux x86_64 only.' >&2
  exit 1
fi
if [[ ! "$VERSION" =~ ^(latest|[0-9]+\.[0-9]+\.[0-9]+)$ ]]; then
  echo 'OPENCODE_VERSION must be latest or a pinned X.Y.Z version.' >&2
  exit 1
fi
command -v curl >/dev/null || { echo 'curl is required' >&2; exit 1; }
command -v tar >/dev/null || { echo 'tar is required' >&2; exit 1; }
mkdir -p "$TOOLS/bin"
TMP="$(mktemp -d)"
STAGED=''
cleanup() { rm -rf -- "$TMP"; [[ -z "$STAGED" ]] || rm -f -- "$STAGED"; }
trap cleanup EXIT
URL="https://opencode.ai/update/api/${VERSION}/cli/opencode/opencode-linux-x64-baseline.tar.gz"
echo "Downloading OpenCode ${VERSION} from the official release endpoint..."
curl --fail --location --show-error --silent --retry 2 "$URL" -o "$TMP/opencode.tar.gz"
# Optional pinned SHA256: OPENCODE_SHA256=<64 hex digits> ./tools/install-opencode.sh
if [[ -n "${OPENCODE_SHA256:-}" ]]; then
  printf '%s  %s\n' "$OPENCODE_SHA256" "$TMP/opencode.tar.gz" | sha256sum --check --status || {
    echo 'OpenCode checksum mismatch; not installing.' >&2
    exit 1
  }
else
  echo 'Warning: no release checksum supplied (set OPENCODE_SHA256 for verification).' >&2
fi
# Extract only the binary to avoid unpacking other paths from the tarball.
tar -xOzf "$TMP/opencode.tar.gz" opencode > "$TMP/opencode"
[[ -s "$TMP/opencode" ]] || { echo 'OpenCode archive missing executable' >&2; exit 1; }
STAGED="$(mktemp "$TOOLS/bin/.opencode.XXXXXXXX")"
install -m 755 "$TMP/opencode" "$STAGED"
mv -f -- "$STAGED" "$TOOLS/bin/opencode"
STAGED=''
echo "Installed: $TOOLS/bin/opencode"
