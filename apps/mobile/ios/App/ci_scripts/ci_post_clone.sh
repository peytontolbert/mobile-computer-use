#!/bin/sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOBILE_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "Xcode Cloud: preparing Capacitor web assets in $MOBILE_ROOT"
cd "$MOBILE_ROOT"

if ! command -v node >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "Xcode Cloud: installing Node with Homebrew"
    brew install node
  else
    echo "error: Node.js is required to build the Capacitor app." >&2
    exit 1
  fi
fi

node --version
npm --version
npm ci
npm run check
npx cap sync ios
