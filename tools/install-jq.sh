#!/usr/bin/env bash
# Download the pinned official jq executable, verifying the published SHA256.
set -euo pipefail
TOOLS="${1:-${XDG_DATA_HOME:-$HOME/.local/share}/sandbox/tools}"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo 'jq installer currently supports Linux x86_64 only.' >&2
  exit 1
fi
command -v curl >/dev/null || { echo 'curl is required' >&2; exit 1; }
mkdir -p "$TOOLS/bin"
VERSION=1.8.2
SHA256=b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f
TMP="$(mktemp -d)"
STAGED=''
cleanup() { rm -rf -- "$TMP"; [[ -z "$STAGED" ]] || rm -f -- "$STAGED"; }
trap cleanup EXIT
URL="https://github.com/jqlang/jq/releases/download/jq-${VERSION}/jq-linux-amd64"
echo "Downloading jq ${VERSION}..."
curl --fail --location --show-error --silent --retry 2 "$URL" -o "$TMP/jq"
printf '%s  %s\n' "$SHA256" "$TMP/jq" | sha256sum --check --status || {
  echo 'jq checksum mismatch; not installing.' >&2
  exit 1
}
STAGED="$(mktemp "$TOOLS/bin/.jq.XXXXXXXX")"
install -m 755 "$TMP/jq" "$STAGED"
mv -f -- "$STAGED" "$TOOLS/bin/jq"
STAGED=''
echo "Installed: $TOOLS/bin/jq"
