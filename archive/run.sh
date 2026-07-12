#!/usr/bin/env bash
# Recurring incremental archive run — the scheduled entrypoint (pbox crontab / scriptbox).
#
# Loads S3 + router config, runs the incremental archiver (resume from the bucket cursor), appends to
# a rotating log, and — if BEACON_URL is set — reports start/finish/fail so the run shows up in
# scriptbox monitoring and fires an alert if it ever stops reporting.
#
# Config source, in order of preference:
#   1. keyvault key `llm-archive/config` (a JSON blob) — fetched at runtime, nothing secret on disk
#      but the ubiquitous `ddash` bearer.
#   2. ~/.config/llm-archive.env — a chmod-600 fallback for when keyvault is unreachable.
set -euo pipefail
cd "$(dirname "$0")/.."                       # repo root
REPO="$(pwd)"
LOG="${LLM_ARCHIVE_LOG:-$HOME/.llm-archive/archive.log}"
mkdir -p "$(dirname "$LOG")"
SLUG="llm-hostbun-archive"

# ── load config ──────────────────────────────────────────────────────────────
# Try keyvault first (JSON at llm-archive/config → export each key). Falls back to the env file.
KV_URL="${KEYVAULT_URL:-https://keyvault.hostbun.cc/mcp}"
KV_BEARER="${KEYVAULT_BEARER:-ddash}"
if command -v node >/dev/null; then
  KVJSON="$(KEYVAULT_URL="$KV_URL" KEYVAULT_BEARER="$KV_BEARER" node "$REPO/archive/kvfetch.js" llm-archive/config 2>/dev/null || true)"
  if [ -n "$KVJSON" ]; then
    while IFS='=' read -r k v; do [ -n "$k" ] && export "$k=$v"; done < <(
      node -e 'let d="";process.stdin.on("data",c=>d+=c).on("end",()=>{const o=JSON.parse(d);for(const k in o)console.log(k+"="+o[k])})' <<<"$KVJSON"
    )
  fi
fi
# Env-file fallback (only fills gaps keyvault did not provide).
if [ -f "$HOME/.config/llm-archive.env" ]; then set -a; . "$HOME/.config/llm-archive.env"; set +a; fi

if [ -z "${S3_ACCESS_KEY:-}" ] || [ -z "${S3_SECRET_KEY:-}" ]; then
  echo "$(date -u +%FT%TZ) FATAL: no S3 creds (keyvault llm-archive/config and ~/.config/llm-archive.env both empty)" >>"$LOG"
  exit 2
fi

# ── beacon: start ────────────────────────────────────────────────────────────
beacon() { [ -n "${BEACON_URL:-}" ] || return 0; curl -fsS -m 10 -X POST "$BEACON_URL/v1/event" \
  -H 'content-type: application/json' -d "{\"slug\":\"$SLUG\",\"event\":\"$1\",\"message\":$(node -e 'process.stdout.write(JSON.stringify(process.argv[1]||""))' "${2:-}")}" >/dev/null 2>&1 || true; }

beacon start ""
echo "$(date -u +%FT%TZ) === run start ===" >>"$LOG"

# ── run ──────────────────────────────────────────────────────────────────────
set +e
SUMMARY="$(node "$REPO/archive/archiver.js" 2>>"$LOG" | tail -1)"
RC=$?
set -e
echo "$(date -u +%FT%TZ) rc=$RC summary=$SUMMARY" >>"$LOG"

# rotate: keep the log bounded (last ~5000 lines)
if [ "$(wc -l <"$LOG" 2>/dev/null || echo 0)" -gt 6000 ]; then tail -5000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi

if [ "$RC" -eq 0 ]; then beacon finish "$SUMMARY"; else beacon fail "rc=$RC"; fi
exit "$RC"
