#!/usr/bin/env python3
"""cccc gateway — route Claude Code through the transparent proxy, cross-platform.

Why this beats the keychain swap: account selection is SERVER-SIDE, so a device
just needs three env vars — no macOS `security`, no ~/.claude/.credentials.json
juggling. Identical on Linux and macOS. Pure python3 stdlib.

  cccc-gateway check [--account X]   # prove /gw works (200 + real rate-limit headers)
  cccc-gateway on    [--account X]   # activate (health-check FIRST); pin X, else auto
  cccc-gateway off                   # deactivate → fall straight back to direct login
  cccc-gateway status                # show mode + a live check

Fail-safes:
  * `on` refuses to activate unless `check` passes (never point panes at a dead gateway).
  * The switch IS the env file's existence — `off` (or `rm ~/.claude-accounts/.cccc-gateway.env`)
    instantly reverts every new shell to the direct login.
  * The rc source line is guarded by `[ -f … ]`, so a missing env file is a clean no-op.
  * `check` is exit-code clean for cron/monitoring (0 = healthy, 1 = down).
"""
import json
import os
import sys
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
BASE = os.environ.get("CCCC_GATEWAY_BASE", "https://claude.hostbun.cc/gw").rstrip("/")
BEARER = os.environ.get("CCCC_GATEWAY_BEARER", "ddash")
ENV_FILE = os.path.join(HOME, ".claude-accounts", ".cccc-gateway.env")
MARK = "# claudectl gateway env"
SOURCE_LINE = ('[ -f "$HOME/.claude-accounts/.cccc-gateway.env" ] && '
               '. "$HOME/.claude-accounts/.cccc-gateway.env"  ' + MARK)


def _check(account=None, timeout=20, tries=3):
    """(ok, detail). Fires a tiny real request through /gw and asserts the
    anthropic-ratelimit-unified-* headers come back — that's what proves this is the
    TRANSPARENT proxy (fidelity + statusline), not the SDK-wrapper that strips them.
    Retries transient network errors (a single reset shouldn't fail activation), but
    NOT a real HTTP error (e.g. 400 unknown account) — that's a definitive answer."""
    import time
    body = json.dumps({"model": "claude-haiku-4-5-20251001", "max_tokens": 4,
                       "messages": [{"role": "user", "content": "hi"}]}).encode()
    headers = {"authorization": f"Bearer {BEARER}", "content-type": "application/json",
               "anthropic-version": "2023-06-01"}
    if account:
        headers["x-ccc-account"] = account
    req = urllib.request.Request(BASE + "/v1/messages", data=body, headers=headers, method="POST")
    last = "unknown"
    for i in range(tries):
        try:
            r = urllib.request.urlopen(req, timeout=timeout)
            util = r.headers.get("anthropic-ratelimit-unified-5h-utilization")
            if util is None:
                return False, "reachable but NO rate-limit headers — not the transparent /gw"
            org = (r.headers.get("anthropic-organization-id") or "?")[:8]
            return True, f"5h {float(util) * 100:.0f}% · org {org}"
        except urllib.error.HTTPError as e:            # definitive — do not retry
            return False, f"HTTP {e.code}: {e.read()[:120].decode('utf-8', 'ignore')}"
        except Exception as e:  # noqa: BLE001 — transient (reset/timeout/DNS) → retry
            last = str(e)
            if i < tries - 1:
                time.sleep(1.5)
    return False, f"unreachable after {tries} tries: {last}"


def _rc_files():
    """Shell rc files to carry the source line — cross-shell so `claude --resume`
    respawns (which inherit the pane shell env) pick it up too."""
    files = [os.path.join(HOME, ".zshenv")]           # zsh: sourced by EVERY zsh
    bashrc = os.path.join(HOME, ".bashrc")
    if os.path.exists(bashrc) or sys.platform.startswith("linux"):
        files.append(bashrc)
    return files


def _ensure_source_line():
    for rc in _rc_files():
        try:
            txt = open(rc).read() if os.path.exists(rc) else ""
        except OSError:
            txt = ""
        if MARK in txt:
            continue
        try:
            with open(rc, "a") as f:
                f.write(f"\n{SOURCE_LINE}\n")
        except OSError:
            pass


def _write_env(account=None):
    os.makedirs(os.path.dirname(ENV_FILE), exist_ok=True)
    lines = [
        "# claudectl gateway mode (auto-generated). Claude Code routes through the",
        "# transparent proxy; account picked server-side. Cross-platform, no keychain.",
        "# Delete this file (or `cccc-gateway off`) to fall back to the direct login.",
        f'export ANTHROPIC_BASE_URL="{BASE}"',
        f'export ANTHROPIC_AUTH_TOKEN="{BEARER}"',
    ]
    lines.append(f'export ANTHROPIC_CUSTOM_HEADERS="X-CCC-Account: {account}"' if account
                 else 'unset ANTHROPIC_CUSTOM_HEADERS 2>/dev/null || true')
    tmp = ENV_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, ENV_FILE)


def cmd_on(account):
    ok, detail = _check(account)
    if not ok:
        print(f"✗ NOT activating — gateway health check failed: {detail}", file=sys.stderr)
        return 1
    _write_env(account)
    _ensure_source_line()
    print(f"✓ gateway ON ({'pin ' + account if account else 'auto (server drain-LB)'}) — {detail}")
    print("  new shells/panes route through it · `cccc refresh --go` moves running panes")
    print("  fail-safe off: `cccc-gateway off`  (instant direct fallback)")
    return 0


def cmd_off():
    try:
        os.remove(ENV_FILE)
    except FileNotFoundError:
        pass
    print("✓ gateway OFF — env file removed; new shells use the direct login.")
    print("  (open a new pane, re-source your rc, or `cccc refresh --go`)")
    return 0


def cmd_status():
    on = os.path.exists(ENV_FILE)
    print(f"gateway: {'ON' if on else 'off'}   ({ENV_FILE})")
    if on:
        for line in open(ENV_FILE):
            if line.startswith(("export", "unset")):
                print("  " + line.rstrip())
    ok, detail = _check()
    print(f"live /gw check: {'✓ ' + detail if ok else '✗ ' + detail}")
    return 0 if (ok or not on) else 1


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv and not argv[0].startswith("-") else "status"
    account = argv[argv.index("--account") + 1] if "--account" in argv else None
    if cmd == "on":
        return cmd_on(account)
    if cmd == "off":
        return cmd_off()
    if cmd == "check":
        ok, detail = _check(account)
        print(f"{'✓' if ok else '✗'} {detail}")
        return 0 if ok else 1
    return cmd_status()


if __name__ == "__main__":
    raise SystemExit(main())
