#!/usr/bin/env bash
# cccc panes — the pretty (Charm/gum) pane refresher.
#
# The glam twin of the curses picker (panes_tui.py) and the ccc-pane MCP: all
# three drive the same cmux `surface.respawn` — kill a pane's process and rerun
# its stored launch/resume command (restart an MCP in a split, resume a claude
# session). This one just makes it nice: gum choose to pick, gum spin while it
# respawns, gum style for the frame. panes_tui.py execs this when `gum` is on
# PATH; without gum you get the stdlib curses picker instead.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CCCC=(python3 "$HERE/claudectl_tui.py" panes)

# Charm palette (256-color): purple accents, dim meta.
P_ACCENT=212; P_DIM=244; P_OK=42; P_ERR=203

header() {
  gum style --border rounded --border-foreground "$P_ACCENT" \
    --padding "0 2" --margin "1 0 0 0" --foreground "$P_ACCENT" --bold \
    "⟳  cccc panes" \
    "$(gum style --foreground "$P_DIM" --bold=false \
        'pick a pane · ↵ respawns it (restart an MCP / resume claude)')"
}

while :; do
  clear
  header

  # ref<TAB>label per refreshable pane (self excluded upstream)
  mapfile -t ROWS < <("${CCCC[@]}" --choices 2>/dev/null)
  if [ "${#ROWS[@]}" -eq 0 ]; then
    gum style --foreground "$P_DIM" --margin "1 0" "no refreshable panes found."
    gum confirm --affirmative "Rescan" --negative "Quit" \
      --prompt.foreground "$P_DIM" "" && continue || exit 0
  fi

  # Show only the label to gum; keep refs in a parallel array.
  LABELS=(); REFS=()
  for line in "${ROWS[@]}"; do
    REFS+=("${line%%$'\t'*}")
    LABELS+=("${line#*$'\t'}")
  done

  CHOICE="$(printf '%s\n' "${LABELS[@]}" | gum choose \
      --header "which pane?" \
      --header.foreground "$P_ACCENT" \
      --cursor "▸ " --cursor.foreground "$P_ACCENT" \
      --height 14)" || exit 0   # Esc/Ctrl-C -> quit

  # Map the chosen label back to its ref.
  REF=""
  for i in "${!LABELS[@]}"; do
    [ "${LABELS[$i]}" = "$CHOICE" ] && REF="${REFS[$i]}" && break
  done
  [ -z "$REF" ] && continue

  OUT="$(gum spin --spinner dot --title "respawning $REF …" \
      --spinner.foreground "$P_ACCENT" --show-output \
      -- "${CCCC[@]}" --refresh "$REF")"
  RC=$?

  if [ "$RC" -eq 0 ]; then
    gum style --border rounded --border-foreground "$P_OK" --padding "0 2" \
      --margin "1 0" --foreground "$P_OK" "✓ ${OUT:-done}"
  else
    gum style --border rounded --border-foreground "$P_ERR" --padding "0 2" \
      --margin "1 0" --foreground "$P_ERR" "✗ ${OUT:-respawn failed}"
  fi
  gum input --placeholder "↵ to pick another · Esc to quit" \
    --prompt "  " --prompt.foreground "$P_DIM" >/dev/null || exit 0
done
