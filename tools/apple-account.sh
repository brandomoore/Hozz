#!/usr/bin/env bash
# Read-only, sanitized App Store Connect readiness evidence. Never provisions.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  exec python3 tools/lib/apple_account.py validate "$@"
fi
python3 tools/lib/apple_account.py validate "$@"
exec tools/with-apple-build-lease.sh hozz/apple-account-readiness -- \
  python3 tools/lib/apple_account.py run "$@"
