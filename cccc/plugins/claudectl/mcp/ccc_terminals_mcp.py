#!/usr/bin/env python3
"""ccc-terminals — a LOCAL stdio MCP that lists and steers EVERY terminal on
every box: this machine AND other computers, regardless of multiplexer.

Why it exists (and why it can't be the deployed container MCP): driving a
terminal is inherently local to whichever box the terminal lives on — you talk
to that box's cmux socket (macOS/ghostty) or its tmux server (the Linux boxes).
The deployed claudectl MCP runs in a container and can reach neither. So this is
a *local* server that fans OUT: local panes via cmux directly, remote panes by
ssh-ing to each box and running that box's own multiplexer. Everything is
normalized to one row shape so `terminals_list` reads the same whether a pane is a
ghostty surface on `ptop` or a tmux pane on `pbox`.

  {machine, target, kind, cwd, command_head, is_self}

`target` is the box-native address you pass back to peek/steer/restart:
  cmux  →  a surface ref ("surface:62") or uuid
  tmux  →  "session:window.pane"  (e.g. "pbox-1:0.0")

Transport to other boxes is plain `ssh <host>` (BatchMode, key auth) — the same
reach the ssh-* plugins use, no daemon, no cmux "remotes" wiring. A box is
reachable ⇒ its terminals are listable and steerable. Offline ⇒ that box's rows
are simply omitted (never fabricated), with an error note in the payload.

Inventory: ~/.config/claudectl/terminals.json if present, else a built-in default
(this machine as cmux + `pbox` as tmux over ssh):

  {"boxes": [
    {"machine": "ptop",  "local": true, "mux": "cmux"},
    {"machine": "pbox",  "ssh": "pbox", "mux": "tmux"}
  ]}

Terminal tools:
  terminals_list    — every terminal on every reachable box (JSON rows)
  terminals_peek     — read the last N lines a specific pane is showing
  terminals_send     — type text into a specific pane (no Enter)
  terminals_key      — send a key/chord to a pane (Enter, C-c, Escape, …)
  terminals_restart  — respawn a pane, resuming the same claude session
                       (cmux: stored resume-binding; tmux: `claude --continue`)

Plugin / marketplace / MCP tools (folded in from the old ccc-plugin server —
LOCAL cmux only, since they drive the `claude` CLI on this box and then relaunch
a pane to apply the change):
  marketplace_update/list/add/remove, plugins_list, plugin_install/update/
  toggle/uninstall, mcp_list/add/remove, reload_apply, reload

Pure stdlib, newline-delimited JSON-RPC 2.0 over stdio (tui/ ethos).
"""
# REQUIRED: claudectl_launch.sh may fall back to a bare `python3` (3.9 on macOS)
# when no interpreter has mcp+httpx. Without this, the terminals-only degrade
# path dies at import on `dict | None` and the whole stdio MCP fails to start.
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys

HOME = os.path.expanduser("~")
CMUX = os.environ.get("CMUX_BUNDLED_CLI_PATH", "cmux")
CLAUDE = os.environ.get("CLAUDECTL_CLAUDE_BIN") or shutil.which("claude") or "claude"
SELF = os.environ.get("CMUX_SURFACE_ID", "")          # this pane (never respawn it)
TERMINALS_CFG = f"{HOME}/.config/claudectl/terminals.json"
MACHINE_FILE = f"{HOME}/.claude-accounts/.cccc-machine"

PROTOCOL = "2025-06-18"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
       "-o", "StrictHostKeyChecking=accept-new"]

# tmux -F template — real TAB separators (preserved through the remote single
# quotes); one row per pane. Kept in one place so peek/list stay in sync.
_TMUX_FMT = "#{session_name}:#{window_index}.#{pane_index}\t#{pane_current_command}\t#{pane_current_path}"
_TMUX_LIST = f"tmux list-panes -a -F '{_TMUX_FMT}' 2>/dev/null"
_SHELLS = {"bash", "zsh", "sh", "fish", "-bash", "-zsh"}


# ---- machine identity / inventory -----------------------------------------
def _local_machine() -> str:
    """Stable name for THIS box — mirrors the statusline's _machine()."""
    name = os.environ.get("CLAUDECTL_MACHINE", "").strip()
    if not name:
        try:
            with open(MACHINE_FILE) as f:
                name = f.read().strip()
        except OSError:
            name = ""
    return name or socket.gethostname().split(".")[0] or "local"


def _local_aliases() -> set[str]:
    """Names/addresses that all mean 'this box' — used to short-circuit an SSH
    hop back to ourselves (which fails publickey on the common no-self-key box)."""
    al = {"localhost", "127.0.0.1", "127.0.1.1", "::1"}
    al.add(_local_machine().lower())
    try:
        host = socket.gethostname()
        al.add(host.lower())
        al.add(host.split(".")[0].lower())
    except OSError:
        pass
    al.discard("")
    return al


def _is_local_box(box: dict) -> bool:
    """True if this box IS the machine the server runs on. Query its multiplexer
    directly instead of SSH-ing — an ssh entry that points at our own hostname
    would hairpin back to us and be publickey-denied (issue #4). Honours an
    explicit local flag, and also detects self by machine name or ssh host."""
    if box.get("local"):
        return True
    al = _local_aliases()
    if (box.get("machine") or "").lower() in al:
        return True
    if (box.get("ssh") or "").lower() in al:
        return True
    return False


def _detect_local_mux() -> str:
    """The multiplexer running on THIS box — so the built-in default matches the
    machine the server actually runs on (a Linux tmux box, not just a mac cmux
    box). $TMUX is the reliable tmux signal; cmux env/binary the cmux one."""
    if os.environ.get("TMUX"):
        return "tmux"
    if os.environ.get("CMUX_SURFACE_ID") or os.environ.get("CMUX_WORKSPACE_ID"):
        return "cmux"
    if shutil.which(CMUX):
        return "cmux"
    out, _ = _sh(["tmux", "list-panes", "-a"], timeout=5)
    return "tmux" if out.strip() else "cmux"


def _load_boxes() -> list[dict]:
    """Raw box list: config file wins, else a sane default keyed to THIS box's
    real multiplexer (so running on the tmux box doesn't hardcode cmux, and
    doesn't add a duplicate ssh-to-self entry for pbox)."""
    try:
        with open(TERMINALS_CFG) as f:
            boxes = json.load(f).get("boxes") or []
        if boxes:
            return boxes
    except (OSError, json.JSONDecodeError):
        pass
    m = _local_machine()
    boxes = [{"machine": m, "local": True, "mux": _detect_local_mux()}]
    if m.lower() != "pbox":                     # don't ssh-to-self when we ARE pbox
        boxes.append({"machine": "pbox", "ssh": "pbox", "mux": "tmux"})
    return boxes


def _normalize_boxes(boxes: list[dict]) -> list[dict]:
    """Mark self-referential boxes local (drop their ssh route) and dedupe by
    machine, so a box that is the local host is never routed through SSH."""
    out: list[dict] = []
    seen: set[str] = set()
    for b in boxes:
        b = dict(b)
        if _is_local_box(b):
            b["local"] = True
            b.pop("ssh", None)                  # never SSH to ourselves
        m = (b.get("machine") or "").lower()
        if m and m in seen:
            continue
        if m:
            seen.add(m)
        out.append(b)
    return out


def _inventory() -> list[dict]:
    """The boxes to sweep, normalized so the local host is queried directly and
    never over SSH. Exactly one box should be local=true (this machine)."""
    return _normalize_boxes(_load_boxes())


def _find_box(machine: str) -> dict | None:
    m = (machine or "").lower()
    boxes = _inventory()
    for b in boxes:                                    # exact machine name
        if b.get("machine", "").lower() == m:
            return b
    if m in ("local", "", "here", "this"):             # convenience aliases
        for b in boxes:
            if b.get("local"):
                return b
    return None


# ---- transport ------------------------------------------------------------
def _sh(argv: list[str], timeout: int = 20) -> tuple[str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.stdout, p.stderr
    except Exception as e:  # noqa: BLE001
        return "", str(e)


def _remote(box: dict, remote_cmd: str, timeout: int = 20) -> tuple[str, str]:
    """Run a shell string on a box: locally if it's THIS machine, else over ssh.
    Locality covers an explicit local flag AND an ssh host that is really us
    (issue #4: never hairpin an SSH back to our own publickey-denied host)."""
    if _is_local_box(box):
        return _sh(["/bin/sh", "-c", remote_cmd], timeout)
    host = box.get("ssh")
    if not host:
        return "", f"box {box.get('machine')!r} has no ssh host and isn't local"
    return _sh(SSH + [host, remote_cmd], timeout)


def _rpc(method: str, params: dict) -> dict:
    """Local cmux rpc (this box only — that's where our cmux socket is)."""
    out, _ = _sh([CMUX, "rpc", method, json.dumps(params)])
    try:
        return json.loads(out or "{}")
    except json.JSONDecodeError:
        return {}


# ---- cmux surface enumeration ---------------------------------------------
# The `surface.list` rpc only ever returns the *active workspace's* surfaces
# (it ignores workspace_ref params), so it silently misses every pane the user
# isn't currently looking at. `cmux top --json` is the only call that walks ALL
# windows/workspaces/panes/surfaces — and it hands back each surface's live pids,
# which is exactly what we need to recover cwd/kind (cmux detaches the claude
# node from any controlling tty, so tty→process matching doesn't work).
def _cmux_surfaces() -> list[dict]:
    """Every local cmux surface, flattened, via `cmux top --json`."""
    out, _ = _sh([CMUX, "top", "--json"])
    try:
        data = json.loads(out or "{}")
    except json.JSONDecodeError:
        return []
    surfaces = []
    for w in data.get("windows", []) or []:
        wref = w.get("ref")
        for ws in w.get("workspaces", []) or []:
            wsname = ws.get("title") or ws.get("name") or ws.get("description")
            for p in ws.get("panes", []) or []:
                for s in p.get("surfaces", []) or []:
                    s = dict(s)
                    s["_workspace"] = wsname
                    s["_workspace_ref"] = ws.get("ref")
                    s["_window_ref"] = wref
                    surfaces.append(s)
    return surfaces


def _ps_cmd_map() -> dict:
    """pid -> command string, one `ps` sweep (for kind detection)."""
    out, _ = _sh(["ps", "-axo", "pid=,command="])
    m = {}
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        pid, _, cmd = ln.partition(" ")
        if pid.isdigit():
            m[int(pid)] = cmd
    return m


def _is_claude_cmd(cmd: str) -> bool:
    c = (cmd or "").lower()
    return ("claude" in c and "/mcp/ccc" not in c
            and "shell-snapshot" not in c and "ccc_terminals_mcp" not in c)


def _lsof_cwd(pid) -> str | None:
    if not pid:
        return None
    out, _ = _sh(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], timeout=6)
    for l in out.splitlines():
        if l.startswith("n"):
            return l[1:]
    return None


def _self_pids() -> set:
    """This server's own process ancestry — so we can mark is_self by seeing
    which surface's pid set contains one of our ancestors."""
    ppid_of = {}
    out, _ = _sh(["ps", "-axo", "pid=,ppid="])
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[0].isdigit():
            ppid_of[int(parts[0])] = int(parts[1])
    chain, p, seen = set(), os.getpid(), set()
    while p and p not in seen:
        seen.add(p); chain.add(p); p = ppid_of.get(p, 0)
    return chain


# ---- normalized rows per multiplexer --------------------------------------
def _kind_from_cmd(cmd: str) -> str:
    c = (cmd or "").strip()
    if "claude" in c:
        return "claude"
    if c in _SHELLS:
        return "shell"
    return c or "shell"


def _rows_cmux_local(box: dict) -> tuple[list[dict], str | None]:
    """EVERY local ghostty/cmux surface across ALL workspaces (not just the
    active one). cwd/kind are recovered from each surface's live pids, since
    cmux detaches the agent process from a controlling tty."""
    surfaces = _cmux_surfaces()
    if not surfaces:
        return [], None
    cmd_of = _ps_cmd_map()
    selfpids = _self_pids()

    def _pids(s: dict) -> list:
        return (s.get("top_level_pids") or s.get("root_pids")
                or s.get("resources", {}).get("pids", []) or [])

    rows = []
    for s in surfaces:
        typ = s.get("type")
        allpids = s.get("resources", {}).get("pids", []) or _pids(s)
        cpid = next((p for p in allpids if _is_claude_cmd(cmd_of.get(p, ""))), None)
        if typ == "browser":
            kind = "browser"
        elif typ == "markdown":
            kind = "markdown"
        elif cpid:
            kind = "claude"
        else:
            kind = "shell"
        # cwd from the agent pid, else the first top-level pid with a real cwd
        cwd = _lsof_cwd(cpid) if cpid else None
        if not cwd and kind != "browser":
            for p in _pids(s):
                c = _lsof_cwd(p)
                if c and c not in ("/", None) and "/Cryptexes/" not in c:
                    cwd = c
                    break
        cmd = cmd_of.get(cpid, "") if cpid else ""
        rows.append({
            "machine": box.get("machine"),
            "mux": "cmux",
            "target": s.get("ref"),
            "id": s.get("ref"),
            "kind": kind,
            "cwd": cwd,
            "command_head": (cmd[:140] if cmd else (s.get("title") or None)),
            "is_self": bool(selfpids & set(allpids)),
            "title": s.get("title"),
            "url": s.get("url"),
        })
    return rows, None


def _rows_tmux(box: dict) -> tuple[list[dict], str | None]:
    """tmux panes (local or over ssh) → normalized rows."""
    out, err = _remote(box, _TMUX_LIST, timeout=15)
    if not out.strip():
        return [], (err.strip() or None)
    rows = []
    for ln in out.splitlines():
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        target, cmd, cwd = parts[0], parts[1], parts[2]
        rows.append({
            "machine": box.get("machine"),
            "mux": "tmux",
            "target": target,
            "id": target,
            "kind": _kind_from_cmd(cmd),
            "cwd": cwd,
            "command_head": cmd or None,
            "is_self": False,
        })
    return rows, None


def _tmux_pane_info(box: dict, target: str) -> tuple[str, str]:
    """(current_command, current_path) for ONE tmux pane, or ('', '')."""
    q = shlex.quote(target)
    fmt = "#{pane_current_command}\t#{pane_current_path}"
    out, _ = _remote(box, f"tmux display-message -p -t {q} '{fmt}'", timeout=12)
    parts = out.strip().split("\t")
    return (parts[0] if parts and parts[0] else ""), (parts[1] if len(parts) > 1 else "")


def _rows_cmux_remote(box: dict) -> tuple[list[dict], str | None]:
    """A remote mac's cmux surfaces over ssh (future desktop). Best-effort."""
    out, err = _remote(box, f"{CMUX} rpc surface.list '{{}}'", timeout=15)
    try:
        surfaces = json.loads(out or "{}").get("surfaces", []) or []
    except json.JSONDecodeError:
        return [], (err.strip() or "unparseable cmux output")
    rows = []
    for s in surfaces:
        rb = s.get("resume_binding") or {}
        cmd = rb.get("command") or s.get("initial_command") or ""
        rows.append({
            "machine": box.get("machine"), "mux": "cmux",
            "target": s.get("ref"), "id": s.get("id"),
            "kind": ("browser" if s.get("type") == "browser"
                     else rb.get("kind") or _kind_from_cmd(cmd.split("\n", 1)[0])),
            "cwd": rb.get("cwd") or s.get("requested_working_directory"),
            "command_head": (cmd.split("\n", 1)[0][:140] if cmd else None),
            "is_self": False,
        })
    return rows, None


def _rows_for_box(box: dict) -> tuple[list[dict], str | None]:
    mux = box.get("mux")
    local = _is_local_box(box)
    if mux == "cmux":
        return _rows_cmux_local(box) if local else _rows_cmux_remote(box)
    if mux == "tmux":
        return _rows_tmux(box)                  # _remote runs local tmux directly
    # auto: try cmux locally, else tmux
    if local:
        r, e = _rows_cmux_local(box)
        return (r, e) if r else _rows_tmux(box)
    return _rows_tmux(box)


# ---- operations -----------------------------------------------------------
def op_panes(machine: str | None = None, kind: str | None = None) -> str:
    boxes = _inventory()
    if machine:
        b = _find_box(machine)
        boxes = [b] if b else []
        if not boxes:
            return json.dumps({"error": f"no box named {machine!r}",
                               "known": [x.get("machine") for x in _inventory()]})
    rows: list[dict] = []
    errors: dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(boxes))) as ex:
        for box, (r, err) in zip(boxes, ex.map(_rows_for_box, boxes)):
            rows.extend(r)
            if err:
                errors[box.get("machine")] = err
    if kind:
        k = kind.lower()
        rows = [r for r in rows if k in (r.get("kind") or "").lower()]
    payload: dict = {"count": len(rows), "rows": rows}
    if errors:
        payload["unreachable"] = errors            # a box down ≠ no terminals
    return json.dumps(payload, indent=1)


def _resolve_cmux_surface(target: str) -> dict | None:
    """Find a surface across ALL workspaces by ref or title substring."""
    surfaces = _cmux_surfaces()
    for s in surfaces:
        if target == s.get("ref"):
            return s
    low = target.lower()
    for s in surfaces:
        if low in (s.get("title") or "").lower():
            return s
    return None


def _resolve_cmux_id(target: str) -> str | None:
    """The surface ref for a target (ref/title) — read-screen/send/respawn-pane
    all accept a ref directly, across every workspace."""
    s = _resolve_cmux_surface(target)
    return s.get("ref") if s else None


def op_peek(machine: str, target: str, lines: int = 14) -> str:
    box = _find_box(machine)
    if not box:
        return f"no box named {machine!r} — call terminals_list for names"
    n = max(1, min(int(lines), 200))
    if box.get("mux") == "cmux" and _is_local_box(box):
        sid = _resolve_cmux_id(target) or target
        out, err = _sh([CMUX, "read-screen", "--surface", str(sid), "--lines", str(n)])
    else:
        # capture the WHOLE pane, not tail -n N — a tall tmux pane pads blank
        # lines at the BOTTOM, so a blind tail returns emptiness while the real
        # content sits higher. Trim trailing blanks, THEN take the last N.
        cmd = f"tmux capture-pane -p -t {shlex.quote(target)} 2>&1"
        out, err = _remote(box, cmd, timeout=15)
    rows = out.splitlines()
    while rows and not rows[-1].strip():
        rows.pop()
    body = "\n".join(rows[-n:]) or (err.strip() and f"(no output — {err.strip()})") or "(blank)"
    return f"{box.get('machine')} · {target}\n{body}"


def op_send(machine: str, target: str, text: str) -> str:
    box = _find_box(machine)
    if not box:
        return f"no box named {machine!r} — call terminals_list for names"
    if box.get("mux") == "cmux" and _is_local_box(box):
        _, err = _sh([CMUX, "send", "--surface", str(target), text])
    else:
        cmd = f"tmux send-keys -t {shlex.quote(target)} -l -- {shlex.quote(text)}"
        _, err = _remote(box, cmd)
    return (f"sent to {box.get('machine')} · {target}"
            + (f"\n{err.strip()}" if err.strip() else ""))


def op_key(machine: str, target: str, key: str) -> str:
    box = _find_box(machine)
    if not box:
        return f"no box named {machine!r} — call terminals_list for names"
    if box.get("mux") == "cmux" and _is_local_box(box):
        _, err = _sh([CMUX, "send-key", "--surface", str(target), key])
    else:
        cmd = f"tmux send-keys -t {shlex.quote(target)} {shlex.quote(key)}"
        _, err = _remote(box, cmd)
    return (f"key {key!r} → {box.get('machine')} · {target}"
            + (f"\n{err.strip()}" if err.strip() else ""))


def op_restart(machine: str, target: str) -> str:
    box = _find_box(machine)
    if not box:
        return f"no box named {machine!r} — call terminals_list for names"
    if box.get("mux") == "cmux" and _is_local_box(box):
        s = _resolve_cmux_surface(target)
        if not s:
            return f"no cmux surface matched {target!r} — call terminals_list"
        ref = s.get("ref")
        spids = set(s.get("resources", {}).get("pids", [])
                    or s.get("top_level_pids") or [])
        if _self_pids() & spids:
            return "refusing to respawn THIS pane (would kill ccc-terminals mid-call)"
        # respawn-pane replays the surface's launch/resume binding (the cmux
        # claude wrapper → resumes the same session). It resolves the ref only
        # within a workspace context, so pass the surface's own workspace/window
        # — otherwise it looks in the active workspace and 404s cross-workspace.
        argv = [CMUX, "respawn-pane", "--surface", str(ref)]
        if s.get("_workspace_ref"):
            argv += ["--workspace", s["_workspace_ref"]]
        if s.get("_window_ref"):
            argv += ["--window", s["_window_ref"]]
        _, err = _sh(argv)
        return (f"respawned {box.get('machine')} · {target} — resumed its session"
                + (f"\n{err.strip()}" if err.strip() else ""))
    # tmux has no stored resume-binding like cmux, so a bare `respawn-pane -k`
    # reruns the pane's start command = a FRESH claude, losing the conversation.
    # For a claude pane we instead respawn into `claude --continue` in the pane's
    # own cwd: --continue reopens the MOST RECENT session there (the one that was
    # running) on the current on-disk credentials — the tmux equivalent of cmux's
    # resume. Non-claude panes just rerun their command.
    pcmd, pcwd = _tmux_pane_info(box, target)
    if "claude" in pcmd.lower():
        resume = box.get("claude_resume_cmd") or "claude --continue --dangerously-skip-permissions"
        inner = f"cd {shlex.quote(pcwd)} && exec {resume}" if pcwd else f"exec {resume}"
        bashcmd = "bash -lc " + shlex.quote(inner)   # login shell → claude on PATH
        cmd = f"tmux respawn-pane -k -t {shlex.quote(target)} {shlex.quote(bashcmd)}"
        _, err = _remote(box, cmd)
        return (f"respawned {box.get('machine')} · {target} — resumed the latest "
                f"claude session in {pcwd or 'its cwd'} (claude --continue)"
                + (f"\n{err.strip()}" if err.strip() else ""))
    cmd = f"tmux respawn-pane -k -t {shlex.quote(target)}"
    _, err = _remote(box, cmd)
    return (f"respawned {box.get('machine')} · {target} (tmux reran the pane cmd)"
            + (f"\n{err.strip()}" if err.strip() else ""))


# ---- plugin / marketplace / MCP management (LOCAL cmux only) --------------
# Folded in from the former ccc-plugin server. These wrap the `claude` CLI on
# THIS box and then relaunch a pane so on-disk changes take effect (Claude Code
# reads plugin/MCP config at session start only — no in-process hot reload).
def _run(argv: list[str], timeout: int = 120) -> str:
    """Run a command, return combined stdout+stderr — `claude` prints useful
    status to either. Never raises; surfaces the failure as text."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return f"error: {argv[0]!r} not found on PATH"
    except subprocess.TimeoutExpired:
        return f"error: {' '.join(argv[:3])}… timed out after {timeout}s"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"
    out = ((p.stdout or "") + (p.stderr or "")).strip() or f"(exit {p.returncode}, no output)"
    return f"[exit {p.returncode}]\n{out}" if p.returncode else out


def _workspaces() -> list[str]:
    """Every workspace ref. `surface.list {}` is window-local, so we fan out."""
    out, _ = _sh([CMUX, "workspace", "list"])
    return re.findall(r"workspace:\d+", out)


def _surfaces_all() -> list[dict]:
    """All surfaces across ALL workspaces, deduped by id (unscoped surface.list
    misses panes in other workspaces, often including this one)."""
    seen: dict[str, dict] = {}
    caller = os.environ.get("CMUX_WORKSPACE_ID", "")
    for ws in ([caller] if caller else []) + _workspaces():
        for s in _rpc("surface.list", {"workspace_id": ws}).get("surfaces", []) or []:
            sid = s.get("id")
            if sid and sid not in seen:
                seen[sid] = s
    if not seen:
        for s in _rpc("surface.list", {}).get("surfaces", []) or []:
            if s.get("id"):
                seen[s["id"]] = s
    return list(seen.values())


def _command_of(s: dict) -> str:
    return (s.get("resume_binding") or {}).get("command") or s.get("initial_command") or ""


def _resolve(needle: str) -> dict | None:
    """Find a surface by ref (surface:N), uuid, or title/command substring.
    'self'/'me'/'this' resolves to the calling pane."""
    surfaces = _surfaces_all()
    if needle.lower() in ("self", "me", "this"):
        needle = SELF
    for s in surfaces:
        if needle and needle in (s.get("ref"), s.get("id")):
            return s
    low = needle.lower()
    for s in surfaces:
        if low in (s.get("title") or "").lower() or low in _command_of(s).lower():
            return s
    return None


def _detach(script: str) -> None:
    """Fire-and-forget a shell snippet, fully detached so it survives this tool
    returning (and survives the pane relaunch it triggers)."""
    subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def _q(s: str) -> str:
    """Single-quote for /bin/sh."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def op_marketplace_update(name: str | None = None) -> str:
    argv = [CLAUDE, "plugin", "marketplace", "update"] + ([name] if name else [])
    head = f"updating marketplace {name!r}" if name else "updating ALL marketplaces"
    return f"$ {' '.join(argv[1:])}\n{_run(argv)}\n\n{head} — done on disk. Changes " \
           "need a session restart: call reload_apply to relaunch a pane."


def op_marketplace_list() -> str:
    return _run([CLAUDE, "plugin", "marketplace", "list"])


def op_marketplace_add(source: str) -> str:
    return _run([CLAUDE, "plugin", "marketplace", "add", source])


def op_marketplace_remove(name: str) -> str:
    return _run([CLAUDE, "plugin", "marketplace", "remove", name])


def op_plugins_list() -> str:
    return _run([CLAUDE, "plugin", "list"])


def op_plugins_available(query: str | None = None, marketplace: str | None = None,
                         limit: int = 200) -> str:
    """Catalog of every plugin installable from the configured marketplaces
    (`claude plugin list --available --json`), grouped by marketplace, each row
    marked ✓ (already installed) or + (installable). `query` is a case-insensitive
    substring over name/description/id; `marketplace` filters to one marketplace."""
    raw = _run([CLAUDE, "plugin", "list", "--available", "--json"])
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 — surface the CLI text if it wasn't JSON
        return f"could not parse `plugin list --available --json`:\n{raw}"
    installed = {p.get("id") for p in (data.get("installed") or [])}
    avail = data.get("available") or []
    q = (query or "").lower().strip()
    mk = (marketplace or "").lower().strip()
    groups: dict[str, list[dict]] = {}
    total = 0
    for p in avail:
        name = p.get("name") or ""
        pid = p.get("pluginId") or name
        mname = p.get("marketplaceName") or "?"
        desc = (p.get("description") or "").strip()
        if mk and mk not in mname.lower():
            continue
        if q and q not in name.lower() and q not in desc.lower() and q not in pid.lower():
            continue
        total += 1
        groups.setdefault(mname, []).append(
            {"id": pid, "name": name, "desc": desc,
             "installed": pid in installed, "count": p.get("installCount")})
    if not total:
        scope = f" matching {query!r}" if q else ""
        scope += f" in {marketplace!r}" if mk else ""
        return f"no available plugins{scope}. (configured marketplaces via marketplace_list)"
    shown = 0
    lines = [f"{total} available plugin(s) across {len(groups)} marketplace(s) "
             f"(✓ = already installed):", ""]
    for mname in sorted(groups):
        rows = sorted(groups[mname], key=lambda r: r["name"].lower())
        lines.append(f"❯ {mname} ({len(rows)})")
        for r in rows:
            if shown >= limit:
                break
            mark = "✓" if r["installed"] else "+"
            d = r["desc"].replace("\n", " ")
            if len(d) > 100:
                d = d[:97] + "…"
            lines.append(f"  {mark} {r['name']} — {d}")
            shown += 1
        lines.append("")
        if shown >= limit:
            lines.append(f"… output capped at {limit} rows; narrow with `query`/`marketplace`.")
            break
    lines.append("install with plugin_install (use name@marketplace to pin the source).")
    return "\n".join(lines)


def op_plugin_install(plugin: str, scope: str | None = None) -> str:
    argv = [CLAUDE, "plugin", "install", plugin] + (["-s", scope] if scope else [])
    return _run(argv)


def op_plugin_update(plugin: str) -> str:
    out = _run([CLAUDE, "plugin", "update", plugin])
    return f"{out}\n\n(restart required to apply — reload_apply relaunches the pane)"


def op_plugin_toggle(plugin: str, enable: bool) -> str:
    return _run([CLAUDE, "plugin", "enable" if enable else "disable", plugin])


def op_plugin_uninstall(plugin: str) -> str:
    return _run([CLAUDE, "plugin", "uninstall", plugin])


def op_mcp_list() -> str:
    return _run([CLAUDE, "mcp", "list"])


def op_mcp_add(name: str, command_or_url: str, args: list[str] | None = None,
               transport: str | None = None, scope: str | None = None) -> str:
    argv = [CLAUDE, "mcp", "add"]
    if transport:
        argv += ["--transport", transport]
    if scope:
        argv += ["-s", scope]
    argv += [name, command_or_url]
    if args:
        argv += ["--"] + list(args) if transport in (None, "stdio") else list(args)
    out = _run(argv)
    return f"{out}\n\n(a new MCP server is only loaded at session start — " \
           "reload_apply relaunches the pane to connect it)"


def op_mcp_remove(name: str, scope: str | None = None) -> str:
    argv = [CLAUDE, "mcp", "remove", name] + (["-s", scope] if scope else [])
    return _run(argv)


def _reload_apply_tmux(surface: str, method: str, text: str | None,
                       delay: float) -> str:
    """reload_apply for a tmux-hosted pane (issue #4: pbox has no cmux, so 'self'
    can't resolve via cmux). Resolve THIS pane from $TMUX_PANE and respawn/send
    directly — no cmux, no SSH. Detached+delayed so this call returns first."""
    pane = os.environ.get("TMUX_PANE", "")
    if not pane:
        return ("inside tmux but $TMUX_PANE is unset — can't identify this pane; "
                "use terminals_restart with an explicit target from terminals_list")
    delay = max(0.0, float(delay))
    q = shlex.quote(pane)
    if method == "send":
        if not text:
            return "method=send needs `text` (what to type into the pane's REPL)"
        _detach(f"sleep {delay}; tmux send-keys -t {q} -l -- {shlex.quote(text)}; "
                f"tmux send-keys -t {q} Enter")
        return (f"queued: in {delay:g}s, type {text!r} into THIS tmux pane "
                f"({pane}) via tmux send-keys")
    # respawn: relaunch THIS pane into `claude --continue` (resume the latest
    # session there) on the current on-disk credentials — the tmux equivalent of
    # the cmux resume path. Preserve the pane's cwd.
    box = _find_box("local") or {}
    _, pcwd = _tmux_pane_info(box, pane)
    resume = box.get("claude_resume_cmd") or "claude --continue --dangerously-skip-permissions"
    inner = f"cd {shlex.quote(pcwd)} && exec {resume}" if pcwd else f"exec {resume}"
    bashcmd = "bash -lc " + shlex.quote(inner)
    _detach(f"sleep {delay}; tmux respawn-pane -k -t {q} {shlex.quote(bashcmd)}")
    return (f"queued: in {delay:g}s, respawn THIS tmux pane ({pane}) into "
            f"`claude --continue`{f' in {pcwd}' if pcwd else ''} to reload "
            "plugins/MCP (no cmux, no SSH)")


def op_reload_apply(surface: str = "self", method: str = "respawn",
                    text: str | None = None, delay: float = 2.0) -> str:
    # A tmux-hosted pane keeps 'self' in tmux, not cmux — resolve it there
    # directly (issue #4). Named cmux refs still fall through to cmux below.
    if os.environ.get("TMUX") and surface.lower() in ("self", "me", "this", ""):
        return _reload_apply_tmux(surface, method, text, delay)
    s = _resolve(surface)
    if not s:
        return f"no surface matched {surface!r} — call terminals_list for refs"
    sid = str(s.get("id") or "")
    ref = s.get("ref")
    label = f"{ref} {(s.get('title') or '').strip()!r}"
    is_self = sid == SELF
    delay = max(0.0, float(delay))

    if method == "send":
        if not text:
            return "method=send needs `text` (what to type into the pane's REPL)"
        payload = text if text.endswith(("\n", "\r")) else text + "\r"
        script = f"sleep {delay}; {_q(CMUX)} send --surface {_q(sid)} -- {_q(payload)}"
        _detach(script)
        return (f"queued: in {delay:g}s, pipe {text!r} into {label} via `cmux send`"
                + (" (this is THIS pane)" if is_self else ""))

    if not _command_of(s):
        return f"{label} has no stored command to rerun — nothing to respawn"
    ref = str(s.get("ref") or "")
    # NON-DESTRUCTIVE respawn: replay the surface's stored launch/resume binding
    # via `cmux respawn-pane --surface` (the same mechanism terminals_restart
    # uses). It reruns ONLY this one pane's command — siblings and the window
    # layout are untouched. The old `cmux rpc surface.respawn` tore down other
    # panes / closed the window; it must never be used. Re-resolve to attach
    # workspace/window refs so the ref resolves cross-workspace.
    enriched = _resolve_cmux_surface(ref) or s
    argv = [CMUX, "respawn-pane", "--surface", ref]
    if enriched.get("_workspace_ref"):
        argv += ["--workspace", str(enriched["_workspace_ref"])]
    if enriched.get("_window_ref"):
        argv += ["--window", str(enriched["_window_ref"])]
    script = f"sleep {delay}; " + " ".join(_q(a) for a in argv)
    _detach(script)
    warn = (" — THIS pane only; its session resumes via the stored binding after "
            "relaunch (sibling panes are left alone)") if is_self else ""
    return f"queued: in {delay:g}s, respawn {label} via respawn-pane (reload plugins/MCP){warn}"


def op_reload(scope: str = "marketplaces", apply: bool = True,
              surface: str = "self", delay: float = 2.0) -> str:
    """One-shot: pull latest, then (optionally) relaunch the pane onto it."""
    lines = [op_marketplace_update(None).split("\n\n")[0]]
    if scope == "all":
        lines.append("plugin list:\n" + _run([CLAUDE, "plugin", "list"]))
    lines.append(op_reload_apply(surface=surface, method="respawn", delay=delay)
                 if apply else
                 "(apply=false — changes are on disk only; nothing relaunched)")
    return "\n\n".join(lines)


def op_selftest() -> str:
    """A trivial, side-effect-free tool whose only job is to prove a freshly
    added tool reached this session after a refresh. Returns a fixed marker
    plus this box's identity + the checkout's git sha."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        sha = subprocess.run(["git", "-C", here, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=8).stdout.strip()
    except Exception:  # noqa: BLE001
        sha = "?"
    return json.dumps({
        "marker": "NEWTOOL_OK",
        "server": "ccc-terminals",
        "machine": socket.gethostname(),
        "sha": sha or "?",
        "python": sys.version.split()[0],
    })


# ---- tool registry --------------------------------------------------------
_SCOPE = {"type": "string", "enum": ["user", "project", "local"],
          "description": "Install/config scope (default user)."}

TOOLS = [
    {
        "name": "terminals_list",
        "description": "List EVERY terminal on every box — this machine "
                       "AND other computers (ssh) — normalized to one shape "
                       "regardless of multiplexer (cmux/ghostty on macs, tmux on "
                       "the Linux boxes). Each row: machine, target (the address "
                       "you pass to peek/send/restart), kind (claude/shell/browser/"
                       "…), cwd, command head, is_self. A box that's unreachable is "
                       "reported under 'unreachable', never silently dropped. JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "machine": {"type": "string",
                            "description": "Only this box (e.g. 'pbox', 'ptop', or "
                                           "'local'). Omit for every box."},
                "kind": {"type": "string",
                         "description": "Only panes whose kind contains this — e.g. "
                                        "'claude' for just the agent sessions."},
            },
        },
    },
    {
        "name": "terminals_peek",
        "description": "Read the last N lines a specific pane is showing, on any "
                       "box. Use it to see what a terminal is running / which "
                       "account a claude session is on before you steer it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "machine": {"type": "string", "description": "Box name from terminals_list."},
                "target": {"type": "string",
                           "description": "The pane address: cmux ref ('surface:62') "
                                          "or tmux target ('pbox-1:0.0')."},
                "lines": {"type": "integer", "description": "Bottom lines (default 14, max 200)."},
            },
            "required": ["machine", "target"],
        },
    },
    {
        "name": "terminals_send",
        "description": "Type text into a specific pane on any box (no Enter — pair "
                       "with terminals_key 'Enter' to submit). Steer a remote claude "
                       "session, feed a command to a shell, etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "machine": {"type": "string", "description": "Box name."},
                "target": {"type": "string", "description": "Pane address."},
                "text": {"type": "string", "description": "Literal text to type."},
            },
            "required": ["machine", "target", "text"],
        },
    },
    {
        "name": "terminals_key",
        "description": "Send a single key or chord to a pane on any box: 'Enter', "
                       "'Escape', 'C-c', 'Up', 'Tab', etc. (cmux key names / tmux "
                       "key names respectively).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "machine": {"type": "string", "description": "Box name."},
                "target": {"type": "string", "description": "Pane address."},
                "key": {"type": "string", "description": "Key/chord, e.g. 'Enter', 'C-c'."},
            },
            "required": ["machine", "target", "key"],
        },
    },
    {
        "name": "terminals_restart",
        "description": "Respawn a specific pane on any box, resuming the SAME "
                       "claude session on the current account. cmux: reruns the "
                       "stored resume-binding. tmux: a claude pane comes back via "
                       "`claude --continue` in its cwd (reopens the latest session "
                       "there); a non-claude pane just reruns its command. Refuses "
                       "to respawn this pane.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "machine": {"type": "string", "description": "Box name."},
                "target": {"type": "string", "description": "Pane address."},
            },
            "required": ["machine", "target"],
        },
    },
    {
        "name": "ccc_selftest",
        "description": "Prove this server is live and current: returns a fixed "
                       "marker (NEWTOOL_OK) plus this box's hostname, the checkout's "
                       "git sha, and the python version. No side effects.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    # ---- plugin / marketplace / MCP (LOCAL cmux only) ----
    {"name": "marketplace_update",
     "description": "Update Claude Code marketplace(s) from their git source — the "
                    "'/update marketplace' action. Omit `name` to update ALL. Pulls "
                    "latest on disk; a session restart (reload_apply) loads the changes.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "One marketplace; omit for all."}}}},
    {"name": "marketplace_list",
     "description": "List configured marketplaces (`claude plugin marketplace list`).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "marketplace_add",
     "description": "Add a marketplace from a URL, path, or GitHub repo.",
     "inputSchema": {"type": "object", "properties": {
         "source": {"type": "string", "description": "URL / path / owner/repo."}},
         "required": ["source"]}},
    {"name": "marketplace_remove",
     "description": "Remove a configured marketplace by name.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}}, "required": ["name"]}},
    {"name": "plugins_list",
     "description": "List installed plugins (`claude plugin list`).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "plugins_available",
     "description": "Browse the CATALOG of every plugin installable from the "
                    "configured marketplaces (`claude plugin list --available`), "
                    "grouped by marketplace, each row marked ✓ already-installed or + "
                    "installable. Use `query` (substring over name/description/id) or "
                    "`marketplace` to filter, then plugin_install to add one.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string",
                   "description": "Case-insensitive substring over name/description/id."},
         "marketplace": {"type": "string", "description": "Only this marketplace."},
         "limit": {"type": "integer",
                   "description": "Max rows to print (default 200)."}}}},
    {"name": "plugin_install",
     "description": "Install a plugin — `claude plugin install <plugin>`. Use "
                    "plugin@marketplace to pin the source. Restart to load.",
     "inputSchema": {"type": "object", "properties": {
         "plugin": {"type": "string", "description": "name or plugin@marketplace"},
         "scope": _SCOPE}, "required": ["plugin"]}},
    {"name": "plugin_update",
     "description": "Update an installed plugin to its latest version "
                    "(`claude plugin update`). Restart required to apply.",
     "inputSchema": {"type": "object", "properties": {
         "plugin": {"type": "string"}}, "required": ["plugin"]}},
    {"name": "plugin_toggle",
     "description": "Enable or disable an installed plugin.",
     "inputSchema": {"type": "object", "properties": {
         "plugin": {"type": "string"},
         "enable": {"type": "boolean", "description": "true=enable, false=disable"}},
         "required": ["plugin", "enable"]}},
    {"name": "plugin_uninstall",
     "description": "Uninstall an installed plugin.",
     "inputSchema": {"type": "object", "properties": {
         "plugin": {"type": "string"}}, "required": ["plugin"]}},
    {"name": "mcp_list",
     "description": "List configured MCP servers (`claude mcp list`), health-checked.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "mcp_add",
     "description": "Register an MCP server. stdio: command_or_url=the command, "
                    "args=its argv. http/sse: set transport and pass the URL as "
                    "command_or_url. A new server only connects after a session "
                    "restart — follow with reload_apply.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "command_or_url": {"type": "string",
                            "description": "stdio command, or the URL for http/sse."},
         "args": {"type": "array", "items": {"type": "string"},
                  "description": "argv for a stdio command."},
         "transport": {"type": "string", "enum": ["stdio", "http", "sse"]},
         "scope": _SCOPE}, "required": ["name", "command_or_url"]}},
    {"name": "mcp_remove",
     "description": "Remove a configured MCP server by name.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "scope": _SCOPE}, "required": ["name"]}},
    {"name": "reload_apply",
     "description": "APPLY on-disk plugin/MCP changes by relaunching a pane — the "
                    "'/reload' half. Works on THE CURRENT pane too: it runs DETACHED "
                    "+ DELAYED, so this tool returns first and the pane reloads ~delay "
                    "seconds later. method=respawn (default) reruns ONLY the target "
                    "pane via `cmux respawn-pane` (a claude pane resumes its session "
                    "with fresh plugins/MCP) — NON-DESTRUCTIVE: sibling panes and the "
                    "window layout are left untouched (it does NOT close other "
                    "terminals). method=send pipes `text` (a slash command / prompt) "
                    "into the pane's REPL without relaunching at all. Target with a "
                    "ref ('surface:7'), uuid, title substring, or 'self'.",
     "inputSchema": {"type": "object", "properties": {
         "surface": {"type": "string",
                     "description": "'self' (default), a ref, uuid, or title substring."},
         "method": {"type": "string", "enum": ["respawn", "send"],
                    "description": "respawn=full reload (default); send=type text in."},
         "text": {"type": "string",
                  "description": "For method=send: what to type into the REPL."},
         "delay": {"type": "number",
                   "description": "Seconds to wait before acting (default 2)."}}}},
    {"name": "reload",
     "description": "One-shot 'pull latest + reload me': update ALL marketplaces "
                    "(scope='all' also lists plugins), then (apply=true, default) "
                    "reload_apply the target pane so the new versions take effect. "
                    "The everyday '/reload plugins' + '/update marketplace' combo.",
     "inputSchema": {"type": "object", "properties": {
         "scope": {"type": "string", "enum": ["marketplaces", "all"],
                   "description": "'marketplaces' (default) or 'all' (also list plugins)."},
         "apply": {"type": "boolean", "description": "Relaunch the pane (default true)."},
         "surface": {"type": "string", "description": "Pane to reload (default 'self')."},
         "delay": {"type": "number", "description": "Delay before relaunch (default 2)."}}}},
]

DISPATCH = {
    "terminals_list": lambda a: op_panes(a.get("machine"), a.get("kind")),
    "terminals_peek": lambda a: op_peek(a["machine"], a["target"], a.get("lines", 14)),
    "terminals_send": lambda a: op_send(a["machine"], a["target"], a["text"]),
    "terminals_key": lambda a: op_key(a["machine"], a["target"], a["key"]),
    "terminals_restart": lambda a: op_restart(a["machine"], a["target"]),
    "ccc_selftest": lambda a: op_selftest(),
    "marketplace_update": lambda a: op_marketplace_update(a.get("name")),
    "marketplace_list": lambda a: op_marketplace_list(),
    "marketplace_add": lambda a: op_marketplace_add(a["source"]),
    "marketplace_remove": lambda a: op_marketplace_remove(a["name"]),
    "plugins_list": lambda a: op_plugins_list(),
    "plugins_available": lambda a: op_plugins_available(
        a.get("query"), a.get("marketplace"), int(a.get("limit", 200))),
    "plugin_install": lambda a: op_plugin_install(a["plugin"], a.get("scope")),
    "plugin_update": lambda a: op_plugin_update(a["plugin"]),
    "plugin_toggle": lambda a: op_plugin_toggle(a["plugin"], bool(a["enable"])),
    "plugin_uninstall": lambda a: op_plugin_uninstall(a["plugin"]),
    "mcp_list": lambda a: op_mcp_list(),
    "mcp_add": lambda a: op_mcp_add(a["name"], a["command_or_url"], a.get("args"),
                                    a.get("transport"), a.get("scope")),
    "mcp_remove": lambda a: op_mcp_remove(a["name"], a.get("scope")),
    "reload_apply": lambda a: op_reload_apply(a.get("surface", "self"),
                                              a.get("method", "respawn"),
                                              a.get("text"), a.get("delay", 2.0)),
    "reload": lambda a: op_reload(a.get("scope", "marketplaces"),
                                  a.get("apply", True), a.get("surface", "self"),
                                  a.get("delay", 2.0)),
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
        reply(rid, {"protocolVersion": params.get("protocolVersion", PROTOCOL),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ccc-terminals", "version": "1.0.0"}})
    elif method == "notifications/initialized" or rid is None:
        return
    elif method == "tools/list":
        reply(rid, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name") or ""
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
