#!/usr/bin/env bash
# Prepare lab fixtures and five pending tickets; never approves remediation.
set -euo pipefail
exec bash "$(dirname "$0")/e2e_remediation.sh" "$@" --prepare-demo
