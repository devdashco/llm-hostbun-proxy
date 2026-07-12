#!/usr/bin/env python3
"""ccc-restart — a LOCAL stdio MCP server that refreshes cmux/ghostty panes.

Same problem as ccc_lsp_mcp.py: the DEPLOYED claudectl MCP runs in a container
and can't touch your machine. Refreshing a pane — respawning whatever process a
cmux surface runs (a `claude`/`ccc` session, an MCP server you launched in a
split, a `tail -f`, anything) — is inherently LOCAL: it drives the cmux CLI over
the app's Unix socket. So it needs a *local* server. This is it: newline-
delimited JSON-RPC 2.0 over stdio, pure stdlib, no deps (tui/ ethos).

The mechanism is one native call: cmux stores every surface's launch/resume
command, and `surface.respawn` kills the running process and reruns that stored
command in the SAME pane (~0.04s). For a stdio MCP server you started in a
split, that = "restart the MCP" — pick up new code without leaving the layout.
For a claude pane it resumes the same session on the current keychain account
(that's what `cccc refresh --go` fans out; `restart_all_claude` here wraps it).

Register it so Claude can refresh a local pane itself:

    claude mcp add ccc-restart -s user -- python3 <repo>/plugins/claudectl/mcp/ccc_restart_mcp.py

Tools:
  panes_list            — every cmux surface (ref, title, kind, cwd, command)
  restart_pane          — respawn ONE surface (by ref / id / title substring)
  restart_all_claude  — respawn every claude/ccc pane (wraps ccc_refresh --go)

`ccc_refresh.py` sits next to this file and does the claude-fleet respawn.
"""
# REQUIRED: cron and the cmux Dock run this via the SYSTEM python3 (3.9 on
# macOS), which cannot evaluate `str | None` annotations at import time.
from __future__ import annotations
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REFRESH = os.path.join(HERE, "ccc_refresh.py")
CMUX = os.environ.get("CMUX_BUNDLED_CLI_PATH", "cmux")
SELF = os.environ.get("CMUX_SURFACE_ID", "")

PROTOCOL = "2025-06-18"


# ---- cmux plumbing --------------------------------------------------------
def _sh(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=20).stdout
    except Exception:  # noqa: BLE001
        return ""


def _rpc(method: str, params: dict) -> dict:
    try:
        return json.loads(_sh([CMUX, "rpc", method, json.dumps(params)]) or "{}")
    except Exception:  # noqa: BLE001
        return {}


def _surfaces() -> list[dict]:
    return _rpc("surface.list", {}).get("surfaces", []) or []


def _command_of(s: dict) -> str:
    """The command cmux would rerun on respawn — resume_binding wins (that's what
    actually reruns), else the initial launch command."""
    rb = s.get("resume_binding") or {}
    return rb.get("command") or s.get("initial_command") or ""


def _kind_of(s: dict) -> str:
    if s.get("type") == "browser":
        return "browser"
    rb = s.get("resume_binding") or {}
    if rb.get("kind"):
        return rb["kind"]            # e.g. "claude"
    return "terminal" if _command_of(s) else "shell"


def _row(s: dict) -> dict:
    cmd = _command_of(s)
    return {
        "ref": s.get("ref"),
        "id": s.get("id"),
        "title": (s.get("title") or "").strip(),
        "kind": _kind_of(s),
        "cwd": (s.get("resume_binding") or {}).get("cwd")
               or s.get("requested_working_directory"),
        # a compact, single-line hint — the full command can be pages long
        "command_head": (cmd.split("\n", 1)[0][:160] if cmd else None),
        "refreshable": bool(cmd),
        "is_self": s.get("id") == SELF,
    }


def _resolve(needle: str) -> dict | None:
    """Find a surface by ref (surface:N), id (uuid), or a title/command substring.
    Exact ref/id match wins; else first case-insensitive substring hit."""
    surfaces = _surfaces()
    for s in surfaces:                                   # exact ref / id
        if needle in (s.get("ref"), s.get("id")):
            return s
    low = needle.lower()
    for s in surfaces:                                   # title / command contains
        if low in (s.get("title") or "").lower() or low in _command_of(s).lower():
            return s
    return None


# ---- operations -----------------------------------------------------------
def op_list(filter_: str | None = None, refreshable_only: bool = False) -> str:
    rows = [_row(s) for s in _surfaces()]
    if refreshable_only:
        rows = [r for r in rows if r["refreshable"]]
    if filter_:
        low = filter_.lower()
        rows = [r for r in rows
                if low in (r["title"] or "").lower()
                or low in (r["command_head"] or "").lower()
                or low in (r["kind"] or "").lower()]
    return json.dumps(rows, indent=1)


def op_refresh(surface: str) -> str:
    s = _resolve(surface)
    if not s:
        return f"no surface matched {surface!r} — call panes_list to see refs/titles"
    if s.get("id") == SELF:
        return ("refusing to respawn THIS pane (the one running ccc-restart) — that "
                "would kill the MCP mid-call")
    cmd = _command_of(s)
    if not cmd:
        return (f"{s.get('ref')} ({(s.get('title') or '').strip()!r}) has no stored "
                "command to rerun — nothing to refresh")
    r = _rpc("surface.respawn", {"surface_id": s.get("id")})
    ok = bool(r.get("surface_id") or r.get("surface_ref"))
    label = f"{s.get('ref')}  {(s.get('title') or '').strip()!r}"
    return (f"respawned {label} — reran its stored command"
            if ok else f"respawn failed for {label} (cmux returned {r})")


def op_refresh_claude(go: bool = True) -> str:
    rc = subprocess.run(["python3", REFRESH] + (["--go"] if go else []),
                        capture_output=True, text=True)
    return (rc.stdout + rc.stderr).strip() or "(no output)"


def op_screen(surface: str, lines: int = 12) -> str:
    """Read the last N lines a pane is showing — peek what a surface is running,
    or confirm a restart_pane actually took (the process/PID line changed)."""
    s = _resolve(surface)
    if not s:
        return f"no surface matched {surface!r} — call panes_list to see refs/titles"
    out = _sh([CMUX, "read-screen", "--surface", str(s.get("id") or ""),
               "--lines", str(max(1, min(int(lines), 200)))])
    head = f"{s.get('ref')}  {(s.get('title') or '').strip()!r}\n"
    return head + (out.rstrip() or "(blank / no screen output)")


# ---- tool registry --------------------------------------------------------
TOOLS = [
    {
        "name": "panes_list",
        "description": "List every cmux/ghostty surface (pane) with its ref, "
                       "title, kind (claude / terminal / browser / shell), cwd, "
                       "and a one-line head of the command cmux would rerun on "
                       "refresh. Use it to find the pane running an MCP server, a "
                       "claude session, or any process you want to restart. "
                       "Returns JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string",
                           "description": "Only surfaces whose title, command, or "
                                          "kind contains this substring (case-"
                                          "insensitive) — e.g. 'mcp', 'claude'."},
                "refreshable_only": {"type": "boolean",
                                     "description": "Drop surfaces with no stored "
                                                    "command to rerun."},
            },
        },
    },
    {
        "name": "restart_pane",
        "description": "Refresh ONE local pane: cmux kills the process running in "
                       "that surface and reruns its stored launch/resume command "
                       "in place (~instant). Restarts an MCP server you launched "
                       "in a split so it picks up new code, or resumes a claude "
                       "session — without disturbing the rest of the layout. "
                       "Refuses to respawn the pane hosting this server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "surface": {"type": "string",
                            "description": "Which pane: a ref ('surface:7'), a "
                                           "surface UUID, or a substring of its "
                                           "title/command ('mcp', a server path)."},
            },
            "required": ["surface"],
        },
    },
    {
        "name": "restart_all_claude",
        "description": "Respawn EVERY claude/ccc pane across all cmux workspaces "
                       "so each resumes its session on the current macOS-keychain "
                       "account (what `cccc refresh --go` does). Skips this pane. "
                       "Set dry_run to just list what would be refreshed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean",
                            "description": "List the claude panes instead of "
                                           "respawning them."},
            },
        },
    },
    {
        "name": "peek_pane",
        "description": "Read the last N lines a pane is currently showing. Peek "
                       "what a surface is running, or confirm a restart_pane "
                       "actually took effect (e.g. a PID/startup line changed).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "surface": {"type": "string",
                            "description": "Pane ref ('surface:7'), UUID, or a "
                                           "title/command substring."},
                "lines": {"type": "integer",
                          "description": "How many bottom lines to read (default "
                                         "12, max 200)."},
            },
            "required": ["surface"],
        },
    },
]

DISPATCH = {
    "panes_list": lambda a: op_list(a.get("filter"), a.get("refreshable_only", False)),
    "restart_pane": lambda a: op_refresh(a["surface"]),
    "restart_all_claude": lambda a: op_refresh_claude(not a.get("dry_run", False)),
    "peek_pane": lambda a: op_screen(a["surface"], a.get("lines", 12)),
}


# ---- minimal JSON-RPC / MCP stdio loop ------------------------------------
def send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def reply(rid, result=None, error=None) -> None:
    m = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        m["error"] = error
    else:
        m["result"] = result
    send(m)


def handle(req: dict) -> None:
    method = req.get("method")
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        reply(rid, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ccc-restart", "version": "1.0.0"},
        })
    elif method == "notifications/initialized" or rid is None:
        return  # notification: no response
    elif method == "tools/list":
        reply(rid, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = DISPATCH.get(name)
        if not fn:
            reply(rid, error={"code": -32601, "message": f"unknown tool {name}"})
            return
        try:
            text = fn(args)
        except Exception as e:  # noqa: BLE001
            reply(rid, {"content": [{"type": "text", "text": f"error: {e}"}],
                        "isError": True})
            return
        reply(rid, {"content": [{"type": "text", "text": text or "(no output)"}]})
    elif method == "ping":
        reply(rid, {})
    else:
        reply(rid, error={"code": -32601, "message": f"unknown method {method}"})


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(req)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
