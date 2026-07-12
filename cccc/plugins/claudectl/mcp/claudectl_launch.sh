#!/bin/sh
# Launch the single local `claudectl` MCP under a python that actually has the
# `mcp` + `httpx` deps (so the account/proxy tools load, not just terminals).
# Tries a list of interpreters; the FIRST one that can import the deps wins. If
# none has them, falls back to plain `python3` — claudectl_local.py then degrades
# to the stdlib terminals-only server on its own. install.sh installs the deps so
# the merged server is the norm.
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="$HERE/claudectl_local.py"

# Probe the REAL import the merged server needs — `mcp.server.fastmcp` — NOT a
# bare `import mcp`. This repo ships a directory literally named `mcp/`, so a
# bare `import mcp` can succeed as an empty NAMESPACE package (e.g. under a
# Homebrew python3 with no real `mcp` installed) — a false positive that makes us
# exec a python which then fails `from mcp.server.fastmcp import FastMCP` and
# silently degrades to terminals-only instead of falling through to python3.11.
for py in "$CLAUDECTL_PYTHON" python3 python3.13 python3.12 python3.11 python; do
  [ -n "$py" ] || continue
  command -v "$py" >/dev/null 2>&1 || continue
  if "$py" -c "import mcp.server.fastmcp, httpx" >/dev/null 2>&1; then
    exec "$py" "$SERVER"
  fi
done

# No interpreter with deps — run under python3 (terminals-only fallback path).
exec python3 "$SERVER"
