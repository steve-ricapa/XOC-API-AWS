#!/usr/bin/env bash
# Usage: bash scripts/e2e_remediation.sh --env-file ~/.xoc/test-tenant.env
set -euo pipefail
if [[ "${1:-}" != --env-file || -z "${2:-}" ]]; then
  echo "Uso: bash scripts/e2e_remediation.sh --env-file ~/.xoc/test-tenant.env [opciones]"
  exit 2
fi
set -a
source "$2"
set +a
shift 2
exec python3 "$(dirname "$0")/e2e_remediation.py" "$@"
