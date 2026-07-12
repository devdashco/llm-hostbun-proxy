# gateway-route.sh — route local Claude Code through the llm.hostbun.cc gateway,
# FAIL-OPEN. Source this from your shell rc INSTEAD of a hard-coded
# `export ANTHROPIC_BASE_URL=…`.
#
# Why: the gateway (llm.hostbun.cc) gives us routing + per-consumer account lock +
# full usage tracking. But making it the base URL unconditionally means a gateway
# hiccup breaks EVERY local claude. Fail-open fixes that: if the gateway answers,
# route through it; if it doesn't, leave ANTHROPIC_BASE_URL unset so claude talks
# to api.anthropic.com directly and keeps working (we just lose tracking for that
# window). The check result is cached briefly so we don't curl on every new shell.
#
# Same file works on every box (pmac dev clone, pbox/wmac deploy clone); each box's
# account + identity ride in ~/.claude/settings.json env.ANTHROPIC_CUSTOM_HEADERS
# (X-Consumer / X-Lane / X-Account), written by cccc. This only decides base URL.

_cctl_gateway_route() {
  local url="https://llm.hostbun.cc" cache="$HOME/.claude/.cctl-gw" ttl=45 now state="" ts st
  now=$(date +%s 2>/dev/null) || return 0
  if [ -r "$cache" ]; then
    IFS='	' read -r ts st < "$cache" 2>/dev/null
    [ -n "$ts" ] && [ "$((now - ts))" -lt "$ttl" ] && state="$st"
  fi
  if [ -z "$state" ]; then
    if command -v curl >/dev/null 2>&1 && curl -s -m 2 -o /dev/null "$url/admin" 2>/dev/null; then
      state=up
    else
      state=down
    fi
    printf '%s\t%s\n' "$now" "$state" > "$cache" 2>/dev/null || true
  fi
  if [ "$state" = up ]; then
    export ANTHROPIC_BASE_URL="$url"
  else
    # gateway unreachable → go direct so claude never breaks on our account.
    unset ANTHROPIC_BASE_URL 2>/dev/null || true
  fi
}

_cctl_gateway_route
