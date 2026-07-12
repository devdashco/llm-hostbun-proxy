#!/usr/bin/env python3
"""ccc-refresh — restart running ccc/claude panes so they resume on the CURRENT
account (whatever the live macOS keychain holds), across ALL cmux workspaces.

Fast path: cmux surfaces are ghostty terminals and cmux natively tracks each
surface's launch/resume command. `surface.respawn` kills the process and reruns
that command in ONE native call (~0.04s) — it resumes the SAME claude session on
the new keychain account. So we no longer do the old per-pane dance (kill -TERM,
poll `ps` up to 12s, screen-scrape the `claude --resume <id>` exit hint, resend
keystrokes, poll 12s for the prompt). Respawn fires per pane instantly and every
pane then reloads claude CONCURRENTLY — minutes -> a few seconds.

- Detect claude panes by SURFACE (works even if a pane dropped to its shell):
  read a few lines of screen, look for the claude UI / statusline.
- Skip the caller's own surface (CMUX_SURFACE_ID) so we never kill this pane.

Usage:
  ccc_refresh.py            # dry-run: list claude panes that would be refreshed
  ccc_refresh.py --go       # do it
"""
import argparse, json, os, re, subprocess

CMUX = os.environ.get("CMUX_BUNDLED_CLI_PATH", "cmux")
SELF = os.environ.get("CMUX_SURFACE_ID", "")


def sh(a):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def rpc(method, params):
    try:
        return json.loads(sh([CMUX, "rpc", method, json.dumps(params)]) or "{}")
    except Exception:
        return {}


def screen(S, n=12):
    return sh([CMUX, "read-screen", "--surface", S, "--lines", str(n)])


def all_surfaces():
    out = []
    for ws in re.findall(r"workspace:\d+", sh([CMUX, "list-workspaces"])):
        for u in re.findall(r"[0-9A-Fa-f-]{36}",
                            sh([CMUX, "list-pane-surfaces", "--workspace", ws, "--id-format", "uuids"])):
            if u not in out:
                out.append(u)
    return out


def is_claude(sc):
    return ("bypass permissions" in sc or "👤" in sc
            or bool(re.search(r"claude --resume [0-9a-f-]{36}", sc)))


def acct_of(sc):
    m = re.search(r"👤\s*([^\s·✓✗]+)", sc)
    return m.group(1) if m else None


def respawn(S):
    """Native kill+relaunch of surface S — cmux reruns its stored command, which
    resumes the same claude session on the current keychain account. Instant."""
    r = rpc("surface.respawn", {"surface_id": S})
    return bool(r.get("surface_id") or r.get("surface_ref"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true")
    a = ap.parse_args()

    todo = []
    for S in all_surfaces():
        if not S or S == SELF or (SELF and S.startswith(SELF[:12])):
            continue
        if is_claude(screen(S, 8)):
            todo.append(S)

    print(f"{len(todo)} claude panes" + ("" if a.go else " (dry-run — pass --go)"))
    if not a.go:
        for S in todo:
            print(f"  {S[:8]}  {acct_of(screen(S, 6)) or '(exited)'}")
        return

    # Fire respawns back-to-back. Each call returns instantly; the panes then load
    # claude in parallel, so total wall time ~= a single claude resume.
    done = sum(respawn(S) for S in todo)
    for S in todo:
        print(f"  {S[:8]} respawned")
    print(f"\n{done}/{len(todo)} respawned — resuming on the current account")


if __name__ == "__main__":
    main()
