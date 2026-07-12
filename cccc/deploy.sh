#!/bin/sh
# deploy.sh — push cccc to GitHub AND propagate to the whole fleet, in one step.
# Run from the cccc/ subdir of the llm-hostbun-router checkout.
#
# Run this INSTEAD of a bare `git push` when you want the fleet current. Explicit and
# deterministic (you run it, the fleet updates now), no background jobs. Each box pulls
# on ITS OWN git credential — pbox as philip, wmac as Williamdevdash — so there's no
# scp and no borrowed tokens. Deploy clones mirror origin (`git reset --hard`) and
# re-run cccc/install.sh (which sets .cccc-machine etc), so their statusline/TUI reflect
# the push immediately (they run the repo files in place).
#
# Add a box: append "<sshhost>:<repo-path>" to FLEET (repo-path = the llm-hostbun-router
# checkout root). An unreachable box is reported, never silently skipped.
set -eu

FLEET="pbox:/home/philip/.llm-hostbun-router wmac:/Users/williamwiklund/.llm-hostbun-router"

cd "$(dirname "$0")"   # the cccc/ dir

# Keep the plugin's BUNDLED account-tool server in sync with the canonical one.
# The plugin ships as a git-subdir of only cccc/plugins/claudectl/, so cccc/server/
# isn't in the plugin cache — claudectl_local.py imports a bundled copy. Refresh it
# here so it can never drift from server/claudectl_server.py.
if ! cmp -s server/claudectl_server.py plugins/claudectl/mcp/claudectl_server.py; then
  cp server/claudectl_server.py plugins/claudectl/mcp/claudectl_server.py
  git add plugins/claudectl/mcp/claudectl_server.py
  git commit -q -m "chore(plugin): resync bundled claudectl_server.py" || true
  echo "→ resynced bundled claudectl_server.py"
fi

echo "→ push origin master"
git push origin master

for spec in $FLEET; do
  host="${spec%%:*}"; dir="${spec#*:}"
  printf '→ %s ' "$host"
  if ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" \
       "cd '$dir' && git fetch -q origin master && git reset --hard -q origin/master && sh cccc/install.sh >/dev/null 2>&1 && printf 'ok %s (%s)\n' \"\$(git rev-parse --short HEAD)\" \"\$(cat ~/.claude-accounts/.cccc-machine 2>/dev/null)\"" 2>/dev/null; then
    :
  else
    echo "UNREACHABLE / failed — pull it manually later"
  fi
done
echo "done — fleet on $(git rev-parse --short HEAD)"
