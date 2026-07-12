#!/usr/bin/env python3
"""cccc sync — keep this machine's claudectl checkout current.

Background/cron entry point. Pulls the repo so the statusline + TUI (which are
plain scripts run fresh each invocation — NOT plugins, so they need no reload)
track the shared version, then re-runs install.sh idempotently to re-vendor the
statusline registration and symlinks.

Two modes, decided per checkout:
- DEV clone (pmac's ~/Documents/GitHub/llm-hostbun-router): conservative. Skips the
  pull while the tree is dirty, and refuses to reset — never clobbers local work.
- DEPLOY clone (marked with a `.cccc-deploy` file or CLAUDECTL_DEPLOY=1, e.g.
  wmac / pbox's ~/.llm-hostbun-router): always converges to the upstream and can NEVER get
  wedged — fast-forwards when it can, hard-resets to the upstream when history
  has diverged (stray local commit, origin force-push).
- Never touches PATH (NO_MODIFY_PATH=1) and never restarts panes unless asked.

  cccc_sync.py            # pull (if clean) + refresh install, log to ~/.claude/.cctl-sync.log
  cccc sync --restart     # also restart ccc panes onto the new code
  cccc_sync.py --quiet    # log only, no stdout (cron default)

The claudectl *plugin* now ships the local MCP servers (ccc-restart/-lsp/-plugin
in plugins/claudectl/mcp/), so keeping a box current means BOTH: pull this repo
AND keep the installed plugin up to date from the marketplace. This does both,
and — when a new plugin version actually lands — pops a desktop notification so
you know to reload (Claude loads plugins/MCP only at session start). Pure stdlib.
"""
# REQUIRED: cron and the cmux Dock run this via the SYSTEM python3 (3.9 on
# macOS), which cannot evaluate `str | None` annotations at import time.
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
LOG = f"{HOME}/.claude/.cctl-sync.log"


def _git_root(start: str) -> str:
    """The checkout to pull. The cccc tool now lives in a `cccc/` SUBDIR of the
    llm-hostbun-router repo (it used to be the claudectl repo root), so walk up to
    the enclosing .git rather than assuming a fixed depth. Falls back to parent-of-
    tui if no .git is found (e.g. a plugin-cache copy)."""
    d = os.path.dirname(os.path.abspath(start))
    while d and d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.dirname(os.path.abspath(start)))


REPO = _git_root(__file__)
INSTALLED = f"{HOME}/.claude/plugins/installed_plugins.json"
# cron/launchd hand us a bare PATH — make sure git + friends are findable.
os.environ["PATH"] = os.environ.get("PATH", "") + ":/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"


def notify(title: str, msg: str) -> None:
    """Best-effort desktop notification (macOS `osascript`, Linux `notify-send`).
    Never raises — a missing notifier just means no popup."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["osascript", "-e",
                            f'display notification "{msg}" with title "{title}"'],
                           capture_output=True, timeout=8)
        else:
            subprocess.run(["notify-send", title, msg], capture_output=True, timeout=8)
    except Exception:  # noqa: BLE001
        pass


# the plugin ships from the devdash marketplace; pin the id so `plugin update`
# is unambiguous even if a second claudectl@… install lingers.
PLUGIN_ID = "claudectl@devdash"


def _plugin_version(pid_pref: str = PLUGIN_ID) -> str | None:
    """Installed version of the plugin, from ~/.claude's registry. Prefer the
    exact id (claudectl@devdash), else any claudectl@… entry."""
    try:
        reg = json.load(open(INSTALLED))
    except Exception:  # noqa: BLE001
        return None
    plugins = reg.get("plugins", {})
    for key in (pid_pref, *[k for k in plugins if k.split("@")[0] == "claudectl"]):
        entries = plugins.get(key)
        for e in (entries if isinstance(entries, list) else [entries] if entries else []):
            if isinstance(e, dict) and e.get("version"):
                return str(e["version"])
    return None


def plugin_autoupdate(quiet: bool) -> None:
    """Refresh the devdash marketplace + update the claudectl plugin; notify on a
    NEW version. 'auto refresh when there's a new update, and it tells us.'"""
    if not _has("claude"):
        return
    before = _plugin_version()
    subprocess.run(["claude", "plugin", "marketplace", "update", "devdash"],
                   capture_output=True, text=True, timeout=60)
    subprocess.run(["claude", "plugin", "update", PLUGIN_ID],
                   capture_output=True, text=True, timeout=120)
    after = _plugin_version()
    if after and after != before:
        log(f"plugin claudectl updated {before} -> {after}", quiet)
        notify("claudectl updated",
               f"plugin {before or '?'} → {after} — reload a pane (cccc panes / "
               f"ccc-plugin reload) to load the new MCP/skills")
    else:
        log(f"plugin up to date at {after or '?'}", quiet)


def _has(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


def log(msg: str, quiet: bool):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}  {msg}"
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if not quiet:
        print(line)


def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restart", action="store_true", help="relaunch ccc panes after sync")
    ap.add_argument("--quiet", action="store_true", help="log only, no stdout")
    ap.add_argument("--no-plugin", action="store_true",
                    help="skip the marketplace/plugin auto-update + notify")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(REPO, ".git")):
        log(f"skip: {REPO} is not a git checkout", a.quiet)
        return 0

    before = git("rev-parse", "--short", "HEAD").stdout.strip()
    # A DEPLOY clone (wmac / pbox — marked by a `.cccc-deploy` file or
    # CLAUDECTL_DEPLOY=1) must ALWAYS converge to the upstream and can never get
    # wedged: fast-forward when possible, else hard-reset to the upstream when
    # history has diverged (a stray commit, an origin force-push). A DEV clone
    # (pmac) stays protective — it skips while dirty and refuses to reset.
    deploy = (os.environ.get("CLAUDECTL_DEPLOY") == "1"
              or os.path.exists(os.path.join(REPO, ".cccc-deploy")))
    upstream = (git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").stdout.strip()
                or "origin/master")
    git("fetch", "--quiet", upstream.split("/")[0])          # learn the remote tip
    remote = git("rev-parse", "--short", upstream).stdout.strip()
    dirty = git("status", "--porcelain", "-uno").stdout.strip()

    if deploy:
        # a deploy clone must MIRROR the upstream — whether it's behind, ahead, or
        # diverged. reset --hard lands exactly on it in every case (untracked files
        # like .cccc-deploy are kept), so this clone can never wedge.
        if remote and remote != before:
            r = git("reset", "--hard", upstream)
            if r.returncode != 0:
                log(f"self-heal reset failed: {r.stderr.strip()[-160:] or '?'}", a.quiet)
            else:
                log(f"synced to upstream {before} -> {remote}", a.quiet)
    elif dirty:
        log(f"skip pull: local edits present (at {before}) — dev clone, sync it by hand", a.quiet)
    elif remote != before and git("merge", "--ff-only", upstream).returncode != 0:
        log(f"pull failed: dev clone diverged from {upstream} (at {before}) — resolve by hand", a.quiet)

    after = git("rev-parse", "--short", "HEAD").stdout.strip()
    if after != before:
        log(f"updated {before} -> {after}", a.quiet)
    elif deploy or not dirty:
        log(f"up to date at {after}", a.quiet)
    # ALWAYS re-run install.sh — statusline registration, wrappers and the fast-mode
    # lock must converge on every sync, not only when the sha moved (a hand-edited
    # settings.json or stale statusline path is otherwise never healed). install.sh
    # VERIFIES itself (settings points at this checkout, statusline executes) and
    # exits nonzero on any failed check — surfaced loudly here, never swallowed.
    env = {**os.environ, "NO_MODIFY_PATH": "1"}
    inst = subprocess.run(["sh", os.path.join(REPO, "cccc", "install.sh")],
                          capture_output=True, text=True, env=env)
    if inst.returncode == 0:
        log("install.sh refreshed + verified ✓", a.quiet)
    else:
        detail = (inst.stderr.strip() or inst.stdout.strip())[-300:]
        log(f"install.sh FAILED VERIFICATION: {detail}", a.quiet)

    # bust the statusline version cache so the new sha shows immediately.
    try:
        os.remove(f"{HOME}/.claude/.cctl-version")
    except OSError:
        pass

    # keep the INSTALLED plugin (and its shipped MCPs) current, and tell us on a
    # new version — this is the multi-device auto-update path.
    if not a.no_plugin:
        plugin_autoupdate(a.quiet)

    if a.restart:
        refresh_py = os.path.join(REPO, "cccc", "plugins", "claudectl", "mcp", "ccc_refresh.py")
        subprocess.run(["python3", refresh_py, "--go"], capture_output=True, text=True)
        log("refresh --go: panes relaunched on current code", a.quiet)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
