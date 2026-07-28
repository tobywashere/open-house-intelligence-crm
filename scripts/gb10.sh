#!/usr/bin/env bash
# Compat shim — this script was renamed to scripts/serve.sh.
exec "$(dirname "$0")/serve.sh" "$@"
