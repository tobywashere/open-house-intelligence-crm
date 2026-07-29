#!/usr/bin/env bash
# Minimal dependency-free .env loader shared by dev.sh and serve.sh.
# Existing exported/shell variables win, so `AGENT_MODE=mock bash
# scripts/serve.sh` still overrides a repository default.

load_repo_env() {
  local env_file="${1:-.env}"
  [ -f "$env_file" ] || return 0

  local line key value first last
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      ''|'#'*) continue ;;
      *=*) ;;
      *) continue ;;
    esac

    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      *[!A-Za-z0-9_]*|'') continue ;;
    esac
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue

    # An explicit command/environment value has higher precedence.
    [ -n "${!key+x}" ] && continue

    if [ "${#value}" -ge 2 ]; then
      first="${value:0:1}"
      last="${value: -1}"
      if { [ "$first" = '"' ] && [ "$last" = '"' ]; } ||
         { [ "$first" = "'" ] && [ "$last" = "'" ]; }; then
        value="${value:1:${#value}-2}"
      fi
    fi

    printf -v "$key" '%s' "$value"
    export "$key"
  done < "$env_file"
}
