#!/usr/bin/env bash
# Repackage an existing notarized beta; never rebuild, install, or launch the app.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PYTHON="${HOZZ_DMG_PYTHON:-python3}"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  exec "$PYTHON" tools/lib/mac_dmg.py validate "$@"
fi
"$PYTHON" tools/lib/mac_dmg.py validate "$@"
source tools/lib/apple-build-lease.sh
acquire_apple_build_shared_lease "hozz/mac-dmg"
install_apple_build_lease_traps
"$PYTHON" tools/lib/mac_dmg.py run "$@"
