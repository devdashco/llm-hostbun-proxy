#!/usr/bin/env python3
"""cccc launcher — a Textual chooser: pick claudectl (Claude account switcher)
or localtools (dev tools TUI), then hand the terminal to the chosen tool.

Self-bootstraps Textual via `uv` if it isn't importable, so the wrapper can stay
a plain `python3 cccc_menu.py`. The tools it launches keep their own runtimes
(claudectl is pure-curses stdlib; localtools is its own uv tool).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# --- bootstrap textual (only for this chooser) ------------------------------
try:
    import textual  # noqa: F401
except ImportError:
    uv = shutil.which("uv")
    if uv:
        os.execvp(uv, [uv, "run", "--with", "textual", "python3", __file__, *sys.argv[1:]])
    sys.exit("cccc: need `textual` (install uv, or `pip install textual`)")

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Label, ListItem, ListView

_HERE = Path(__file__).resolve().parent
CLAUDECTL = str(_HERE / "claudectl_app.py")   # Textual version (curses = claudectl_tui.py)
LOCALTOOLS = shutil.which("localtools") or str(Path.home() / ".local/bin/localtools")

_ALL = [
    ("claudectl", "claudectl — Claude account switcher",
     "Pin/switch which Claude Max account this box uses; live 5h/7d limits, windows, plugins.",
     lambda: Path(CLAUDECTL).exists()),
    ("localtools", "localtools — dev tools",
     "GitHub issues → fix with Claude, caveman CLAUDE.md compressor, MCP tool-doc shrink.",
     lambda: bool(shutil.which("localtools")) or Path(LOCALTOOLS).exists()),
]
# only offer tools that actually exist on this box (localtools isn't on servers)
OPTIONS = [(k, n, d) for k, n, d, avail in _ALL if avail()]


class Chooser(App):
    CSS = """
    ListView { height: auto; border: round $primary; margin: 1 2; }
    ListItem { padding: 0 1; }
    .name { text-style: bold; }
    .desc { color: $text-muted; }
    """
    BINDINGS = [Binding("q", "quit", "Quit")]
    TITLE = "cccc"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("  Choose a tool — ↑/↓ then Enter:", classes="name")
        yield ListView(*[
            ListItem(Label(name, classes="name"), Label(desc, classes="desc"), id=key)
            for key, name, desc in OPTIONS
        ])
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.exit(event.item.id)


def _launch(choice: str) -> None:
    if choice == "claudectl":
        os.execvp("python3", ["python3", CLAUDECTL, *sys.argv[1:]])
    elif choice == "localtools":
        os.execvp(LOCALTOOLS, [LOCALTOOLS])


def main() -> None:
    if len(OPTIONS) <= 1:
        # only one tool on this box (e.g. servers have no localtools) → skip the menu
        if OPTIONS:
            _launch(OPTIONS[0][0])
        return
    choice = Chooser().run()
    if choice:
        _launch(choice)
    # else: user quit — do nothing


if __name__ == "__main__":
    main()
