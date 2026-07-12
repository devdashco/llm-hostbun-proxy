#!/usr/bin/env python3
"""cccc gateway — route Claude Code through the llm.hostbun.cc router, cross-platform.

Why this beats the keychain swap: account selection is SERVER-SIDE (the router's
/api/pins map), so a device just needs ONE env var — no macOS `security`, no
~/.claude/.credentials.json juggling. Identical on Linux and macOS. Pure python3 stdlib.

  cccc-gateway check                 # prove the router is reachable
  cccc-gateway on    [--account X]   # activate (health-check FIRST); optionally pin X
  cccc-gateway off                   # deactivate → fall straight back to direct login
  cccc-gateway status                # show mode + a live check

Fail-safes:
  * `on` refuses to activate unless `check` passes (never point panes at a dead gateway).
  * The switch IS the env file's existence — `off` (or `rm ~/.claude-accounts/.cccc-gateway.env`)
    instantly reverts every new shell to the direct login.
  * The rc source line is guarded by `[ -f … ]`, so a missing env file is a clean no-op.
  * `check` is exit-code clean for cron/monitoring (0 = healthy, 1 = down).
  * NEVER writes ANTHROPIC_AUTH_TOKEN — the router discards inbound authorization and
    picks the pool account from its server-side pin; a token here would trip
    `cccc guard` (billed-path check) and break the direct-login fallback.

Account pinning (--account) goes through POST /api/pins on the router (merge-safe,
validates the name) — account headers like x-ccc-account are IGNORED by the router
by design (invariant: no header can override the pin).
"""
import json
import os
import sys
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
BASE = os.environ.get("CCCC_GATEWAY_BASE", "https://llm.hostbun.cc").rstrip("/")
ADMIN_PW = os.environ.get("CCTL_LLM_PW", "ddash")
ENV_FILE = os.path.join(HOME, ".claude-accounts", ".cccc-gateway.env")
MARK = "# claudectl gateway env"
SOURCE_LINE = ('[ -f "$HOME/.claude-accounts/.cccc-gateway.env" ] && '
               '. "$HOME/.claude-accounts/.cccc-gateway.env"  ' + MARK)


def _check(timeout=10, tries=3):
    """(ok, detail). GET {BASE}/v1/models — public, unauthenticated, served by the
    router itself — so a 200 proves the ROUTER is up without spending a token or
    needing a cookie. Retries transient network errors; an HTTP error is definitive."""
    import time
    req = urllib.request.Request(BASE + "/v1/models")
    last = "unknown"
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status == 200:
                    n = len((json.load(r) or {}).get("data") or [])
                    return True, f"router up at {BASE.split('://')[-1]} · {n} models"
                return False, f"unexpected HTTP {r.status} from /v1/models"
        except urllib.error.HTTPError as e:            # definitive — do not retry
            return False, f"HTTP {e.code} from /v1/models — router misconfigured?"
        except Exception as e:  # noqa: BLE001 — transient (reset/timeout/DNS) → retry
            last = str(e)
            if i < tries - 1:
                time.sleep(1.5)
    return False, f"unreachable after {tries} tries: {last}"


def _consumer_name():
    try:
        n = open(os.path.join(HOME, ".claude-accounts", ".cccc-machine")).read().strip()
    except OSError:
        n = ""
    if not n:
        import socket
        n = socket.gethostname().split(".")[0]
    return (n or "box").lower()


def _pin_account(account):
    """Pin this box's consumer (+ its -claude alias) to `account` via the router's
    merge-safe POST /api/pins. Shares the TUI's cached admin cookie (the login
    endpoint throttles per-IP and the fleet shares one egress). Returns (ok, detail)."""
    import http.cookiejar
    cookie_file = os.path.join(HOME, ".claude", ".cctl-admin-cookie")
    cj = http.cookiejar.MozillaCookieJar(cookie_file)
    try:
        cj.load(ignore_discard=True, ignore_expires=True)
    except (OSError, http.cookiejar.LoadError):
        pass
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def _post(sub, body):
        r = op.open(urllib.request.Request(f"{BASE}/api/{sub}",
                    data=json.dumps(body).encode(),
                    headers={"content-type": "application/json"}, method="POST"), timeout=10)
        return json.loads(r.read().decode() or "{}")

    def _pins():
        consumer = _consumer_name()
        for proj in (consumer, f"{consumer}-claude"):
            if not _post("pins", {"project": proj, "account": account}).get("ok"):
                return False
        return True

    try:
        try:
            okp = _pins()
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403):
                raise
            _post("login", {"password": ADMIN_PW})     # cookie expired → one login
            try:
                os.makedirs(os.path.dirname(cookie_file), exist_ok=True)
                cj.save(ignore_discard=True, ignore_expires=True)
                os.chmod(cookie_file, 0o600)
            except OSError:
                pass
            okp = _pins()
        return (okp, f"pinned {_consumer_name()} → {account}" if okp else "pin refused")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("error", "")
        except Exception:  # noqa: BLE001
            detail = ""
        return False, f"HTTP {e.code}" + (f": {detail}" if detail else "")
    except Exception as e:  # noqa: BLE001
        return False, str(e)


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


def _write_env():
    os.makedirs(os.path.dirname(ENV_FILE), exist_ok=True)
    lines = [
        "# claudectl gateway mode (auto-generated). Claude Code routes through the",
        "# llm.hostbun.cc router; account picked server-side (/api/pins). No keychain.",
        "# Delete this file (or `cccc-gateway off`) to fall back to the direct login.",
        f'export ANTHROPIC_BASE_URL="{BASE}"',
        # scrub anything a pre-router install may have exported (dead host / bearer):
        "unset ANTHROPIC_AUTH_TOKEN 2>/dev/null || true",
    ]
    tmp = ENV_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, ENV_FILE)


def cmd_on(account):
    ok, detail = _check()
    if not ok:
        print(f"✗ NOT activating — router health check failed: {detail}", file=sys.stderr)
        return 1
    if account:
        pok, pdetail = _pin_account(account)
        if not pok:
            print(f"✗ NOT activating — pin failed: {pdetail}", file=sys.stderr)
            return 1
        detail += f" · {pdetail}"
    _write_env()
    _ensure_source_line()
    print(f"✓ gateway ON ({'pin ' + account if account else 'server-side pin as-is'}) — {detail}")
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
    stale = False
    if on:
        for line in open(ENV_FILE):
            if line.startswith(("export", "unset")):
                print("  " + line.rstrip())
                if line.startswith("export") and (
                        "claude.hostbun.cc" in line or "ANTHROPIC_AUTH_TOKEN" in line):
                    stale = True
    if stale:
        print("  ⚠ STALE env file from a pre-router install (dead host / bearer) — run "
              "`cccc-gateway on` to rewrite it, or `off` to remove it.")
    ok, detail = _check()
    print(f"live router check: {'✓ ' + detail if ok else '✗ ' + detail}")
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
        ok, detail = _check()
        print(f"{'✓' if ok else '✗'} {detail}")
        return 0 if ok else 1
    return cmd_status()


if __name__ == "__main__":
    raise SystemExit(main())
