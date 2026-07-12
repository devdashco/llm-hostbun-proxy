#!/usr/bin/env python3
"""cccc panes — a curses picker that refreshes cmux/ghostty panes.

The visible twin of the `ccc-pane` local MCP (ccc_pane_mcp.py): both drive the
same `surface.respawn` — kill the process a surface runs and rerun its stored
launch/resume command in place. Use it to restart an MCP server you launched in
a split (picks up new code, layout untouched) or resume a claude session.

Arrows pick a pane; Enter respawns it. The list live-refreshes. The pane running
THIS picker is marked and can't be respawned (that'd kill the picker mid-call).

Usage:
  cccc panes            # the TUI (arrows + Enter)
  cccc panes --list     # print refreshable panes (debug), no curses

Pure stdlib. Reuses ccc_pane_mcp for the cmux plumbing.
"""
from __future__ import annotations

import curses
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.realpath(__file__))
# the MCP servers live in the plugin (they ship via the marketplace); reuse the
# restart MCP's cmux plumbing from there.
_MCP_DIR = os.path.join(os.path.dirname(_HERE), "plugins", "claudectl", "mcp")
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)
import ccc_restart_mcp as pane   # _surfaces / _row / op_refresh / SELF

REFRESH_MS = 4000
_GUM_UI = os.path.join(_HERE, "panes_gum.sh")


# --- data -----------------------------------------------------------------
def refreshable_rows() -> list[dict]:
    """Every surface that has a stored command to rerun, this pane last."""
    rows = [pane._row(s) for s in pane._surfaces()]
    rows = [r for r in rows if r["refreshable"]]
    rows.sort(key=lambda r: (r["is_self"], r["kind"] != "claude", r["ref"] or ""))
    return rows


def _label(r: dict) -> str:
    kind = r["kind"]
    tag = {"claude": "◆", "terminal": "▸", "shell": "·"}.get(kind, "▸")
    title = (r["title"] or "").strip() or (r["command_head"] or "?")
    ref = (r["ref"] or "?").replace("surface:", "s")
    me = "  ← this pane" if r["is_self"] else ""
    return f"{tag} {ref:>4}  {title}{me}"


# --- curses helpers (self-contained) --------------------------------------
def _wrap(text, width):
    text = text or ""
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out or [""]


def _popup(stdscr, title, text):
    h, w = stdscr.getmaxyx()
    lines = []
    for para in text.split("\n"):
        lines += _wrap(para, min(w - 8, 66)) or [""]
    pw = min(max([len(l) for l in lines] + [len(title)]) + 6, w - 2)
    ph = min(len(lines) + 4, h - 2)
    win = curses.newwin(ph, pw, max(0, (h - ph) // 2), max(0, (w - pw) // 2))
    win.keypad(True)
    win.erase()
    win.box()
    win.addstr(0, 2, f" {title} "[: pw - 4], curses.A_BOLD)
    for i, l in enumerate(lines[: ph - 4]):
        win.addstr(2 + i, 3, l[: pw - 6])
    win.addstr(ph - 1, 2, " any key "[: pw - 4], curses.A_DIM)
    win.refresh()
    win.getch()


def _picker(stdscr, rows):
    """Draw the pane list; return the chosen row dict, or None on Esc."""
    curses.curs_set(0)
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    stdscr.addstr(0, 1, "cccc panes — ↵ respawns the selected pane", curses.A_BOLD)
    stdscr.addstr(1, 1, "restarts its process by rerunning its launch/resume command",
                  curses.A_DIM)
    if not rows:
        stdscr.addstr(3, 2, "no refreshable panes found.", curses.color_pair(1))
        stdscr.addstr(h - 1, 1, "[r] rescan   [esc] quit", curses.A_DIM)
        stdscr.refresh()
        return "__wait__"
    sel = getattr(_picker, "sel", 0) % len(rows)
    listmax = h - 5
    top = max(0, sel - listmax + 1)
    for i, r in enumerate(rows[top: top + listmax]):
        idx = top + i
        active = idx == sel
        line = _label(r).ljust(w - 2)[: w - 2]
        attr = curses.A_REVERSE if active else (
            curses.A_DIM if r["is_self"] else curses.A_NORMAL)
        stdscr.addstr(3 + i, 1, line, attr)
    stdscr.addstr(h - 1, 1,
                  "[↑↓] move   [↵] respawn   [r] rescan   [esc] quit"[: w - 2],
                  curses.A_DIM)
    stdscr.refresh()

    ch = stdscr.getch()
    if ch in (27, ord("q")):
        return None
    if ch in (ord("r"), -1):
        return "__rescan__"
    if ch == curses.KEY_DOWN:
        _picker.sel = (sel + 1) % len(rows)
        return "__rescan__"
    if ch == curses.KEY_UP:
        _picker.sel = (sel - 1) % len(rows)
        return "__rescan__"
    if ch in (10, 13):
        return rows[sel]
    return "__rescan__"


# --- run loop -------------------------------------------------------------
def run(stdscr):
    curses.curs_set(0)
    curses.use_default_colors()
    for i in range(1, 7):
        curses.init_pair(i, i, -1)
    stdscr.timeout(REFRESH_MS)   # auto-rescan when idle
    rows = refreshable_rows()
    while True:
        choice = _picker(stdscr, rows)
        if choice is None:
            return
        if choice in ("__rescan__", "__wait__"):
            rows = refreshable_rows()
            continue
        # a row was chosen
        if choice["is_self"]:
            _popup(stdscr, "nope", "That's THIS pane — respawning it would kill "
                                   "the picker. Pick another.")
            continue
        stdscr.timeout(-1)
        _popup(stdscr, "respawning", f"{choice['ref']}\n{(choice['title'] or '').strip()}")
        msg = pane.op_refresh(choice["id"])
        _popup(stdscr, "done", msg)
        stdscr.timeout(REFRESH_MS)
        rows = refreshable_rows()


def main():
    a = sys.argv[1:]
    if a and a[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if a and a[0] == "--list":
        rows = refreshable_rows()
        print(f"# {len(rows)} refreshable panes")
        for r in rows:
            print(f"  {r['ref']:>10}  {r['kind']:8}  {(r['title'] or '').strip()[:50]}"
                  + ("  (this pane)" if r["is_self"] else ""))
        return 0
    # --choices: one line per (non-self) pane for `gum choose`; ref is the 1st token
    if a and a[0] == "--choices":
        tag = {"claude": "◆", "terminal": "▸", "shell": "·"}
        for r in refreshable_rows():
            if r["is_self"]:
                continue
            title = (r["title"] or "").strip() or (r["command_head"] or "?")
            print(f"{r['ref']}\t{tag.get(r['kind'], '▸')} {title}  ({r['kind']})")
        return 0
    # --refresh <ref|id|substr>: respawn one pane headlessly (the gum UI calls this)
    if a and a[0] == "--refresh":
        if len(a) < 2:
            print("usage: cccc panes --refresh <ref|id|substring>")
            return 2
        msg = pane.op_refresh(a[1])
        print(msg)
        return 0 if msg.startswith("respawned") else 1
    # no args: prefer the pretty gum UI when gum is on PATH + we have a TTY;
    # otherwise fall back to the pure-stdlib curses picker.
    if (not a and shutil.which("gum") and os.path.exists(_GUM_UI)
            and sys.stdin.isatty() and sys.stdout.isatty()):
        os.execvp("bash", ["bash", _GUM_UI])
    curses.wrapper(run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
