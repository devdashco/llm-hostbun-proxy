---
description: Put the `cccc` terminal dashboard on your PATH (uses the files the plugin already downloaded — no clone)
allowed-tools: Bash
---

Set up the single `cccc` terminal command (the live dashboard) for the user.
Refresh / doctor / sync are reached from inside the TUI, or headless as
`cccc refresh|doctor|sync`. The claudectl plugin already downloaded the full repo
to disk, so **do not clone anything** — just run the bundled installer.

Steps:

1. Find and run the installer from the marketplace clone:

   ```
   sh ~/.claude/plugins/marketplaces/claudectl/install.sh
   ```

   If that exact path doesn't exist, locate it and run it:

   ```
   sh "$(ls -d ~/.claude/plugins/marketplaces/*/install.sh 2>/dev/null | grep -m1 claudectl)"
   ```

   The installer is idempotent: it symlinks the single `cccc` command into
   `~/.local/bin` (removing any old `cccr`/`cccd`/`cccc-sync`) and adds that to
   the user's shell PATH.

2. Report the result to the user in one or two lines:
   - If it added `~/.local/bin` to their shell rc, tell them to **open a new
     terminal** (or `source` that rc) so `cccc` is found.
   - Then they run **`cccc`** to open the dashboard.

Keep it to those steps — don't launch `cccc` yourself (it's an interactive curses
app that needs the user's real terminal).
