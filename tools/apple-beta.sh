#!/usr/bin/env bash
# Never install, launch, or upload implicitly. Keep the lease across the whole lane.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  exec python3 tools/lib/apple_beta.py validate "$@"
fi
python3 tools/lib/apple_beta.py validate "$@"
source tools/lib/apple-build-lease.sh
acquire_apple_build_shared_lease "hozz/apple-beta"
install_apple_build_lease_traps
python3 tools/lib/apple_beta.py run "$@"
