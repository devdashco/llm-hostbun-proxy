"""claudectl MCP — control the claudebox wrapper's Claude subscription accounts
and models, on BOTH gateway hosts, from inside Claude.

`claude.hostbun.cc` and `llm.hostbun.cc` are the SAME app: the claudebox
wrapper (claude-code-openai-wrapper) that load-balances several Claude Max
subscription logins and, on the `llm` host, also fronts LM Studio local models
on pbox. The wrapper already exposes a full account REST API under
`/v1/accounts/*` (+ `/ui/accounts/*`); this server is a thin, typed MCP facade
over it so the model can list / add / edit / switch / delete accounts, read the
real 5h+7d rate-limit usage & reset times, reset the wrapper's local tallies,
tune the load balancer, and list models.

Every tool takes an optional `host` = "claude" (default) | "llm" so one server
drives both gateways.

Auth: the wrapper's mutation "switch password" gate is OFF unless its
`SWITCH_PASSWORD` env is set, so the static `Authorization: Bearer <UPSTREAM_BEARER>`
alone authorizes everything. Tools still accept an optional `password` that is
forwarded in case a host later enables the gate.

Two tool groups:
  - account tools (accounts_*, account_*, loadbalance_*, usage_reset, models_list)
    drive the claudebox subscription accounts on `host` (claude|llm).
  - proxy_* tools drive the llm.hostbun.cc ROUTER itself (repo llm-hostbun-proxy)
    via its /api — lanes (local LM Studio / wrappy claudebox / crazyrouter),
    live model-routing config, health, usage stats and the call log. Cookie-gated
    by ADMIN_PASSWORD (default ddash), handled internally.

Still NOT exposed anywhere over HTTP (so NOT here): LM Studio model load/unload
and the `llm-proxy.service` container restart — host-level ops, use `ssh-pbox`.

Env (Coolify):
  STATIC_BEARER       — bearer THIS server checks on inbound MCP calls (default ddash)
  UPSTREAM_BEARER     — bearer used to call the wrapper hosts (default ddash)
  CLAUDE_HOST         — default https://claude.hostbun.cc
  LLM_HOST            — default https://llm.hostbun.cc
  SWITCH_PASSWORD     — optional; forwarded on mutations if a host requires it
  CLAUDECTL_TRANSPORT — stdio | http (default stdio)
  PORT                — when http (default 8000)
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------- config
STATIC_BEARER = os.environ.get("STATIC_BEARER", "ddash")
UPSTREAM_BEARER = os.environ.get("UPSTREAM_BEARER", "ddash")
HOSTS = {
    "claude": os.environ.get("CLAUDE_HOST", "https://claude.hostbun.cc").rstrip("/"),
    "llm": os.environ.get("LLM_HOST", "https://llm.hostbun.cc").rstrip("/"),
}
SWITCH_PASSWORD = os.environ.get("SWITCH_PASSWORD", "").strip()
# llm.hostbun.cc is the llm-hostbun-proxy (Node router) — a SEPARATE app from the
# claudebox account API. Its /api/* control surface is cookie-gated by a
# password login (default ddash), not the bearer.
LLM_PROXY_BASE = os.environ.get("LLM_PROXY_BASE", "https://llm.hostbun.cc").rstrip("/")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ddash")
TRANSPORT = os.environ.get("CLAUDECTL_TRANSPORT", "stdio").lower()
HTTP_TIMEOUT = float(os.environ.get("CLAUDECTL_TIMEOUT", "45"))
# window keeper: keep every account's fixed 5h window started + staggered by
# priming the coldest reset account each interval (a 1-token message via its token).
KEEPER_ENABLED = os.environ.get("KEEPER_ENABLED", "0") == "1"
KEEPER_INTERVAL = int(os.environ.get("KEEPER_INTERVAL", "3600"))   # 1h -> 5 accts stagger ~1h apart
PRIME_MODEL = os.environ.get("PRIME_MODEL", "claude-haiku-4-5-20251001")
_CC_SYSTEM = "You are Claude Code, Anthropic's official CLI for Claude."

log = logging.getLogger("claudectl")
if not log.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("[claudectl] %(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
log.setLevel(logging.INFO)

mcp = FastMCP("claudectl", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))


# ---------------------------------------------------------------- upstream call
def _base(host: str) -> str:
    h = (host or "claude").strip().lower()
    if h not in HOSTS:
        raise ValueError(f"unknown host {host!r} — use 'claude' or 'llm'")
    return HOSTS[h]


async def _call(host: str, method: str, path: str,
                body: Optional[dict] = None) -> dict:
    """One request to a wrapper host. Returns the parsed JSON (or a wrapped
    error dict). Auto-attaches the switch password to mutations when set."""
    url = _base(host) + path
    headers = {"Authorization": f"Bearer {UPSTREAM_BEARER}",
               "Accept": "application/json"}
    if method.upper() == "POST":
        body = dict(body or {})
        if SWITCH_PASSWORD and "password" not in body:
            body["password"] = SWITCH_PASSWORD
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as c:
            if method.upper() == "GET":
                r = await c.get(url, headers=headers)
            else:
                r = await c.post(url, headers=headers, json=body or {})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300],
                "host": host, "path": path}
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = {"raw": r.text[:2000]}
    if isinstance(data, dict):
        data.setdefault("http_status", r.status_code)
        return data
    return {"http_status": r.status_code, "data": data}


# ---------------------------------------------------------------- read tools
@mcp.tool()
async def accounts_list(host: str = "claude") -> dict:
    """List every Claude subscription account the wrapper holds on `host`.

    Returns each account's name, whether it's the active/live login, its meta
    (subscriptionType, organizationId, token_login, expiresAt) and the wrapper's
    *local* usage tallies (requests/input/output/since/last). For the REAL
    subscription rate-limit % + reset times use `accounts_limits` instead.
    host = 'claude' (claude.hostbun.cc) | 'llm' (llm.hostbun.cc).
    """
    return await _call(host, "GET", "/v1/accounts")


@mcp.tool()
async def accounts_limits(host: str = "claude") -> dict:
    """Real subscription usage + reset times per account on `host`.

    This is the truthful quota surface: for each account, `five_hour` and
    `seven_day` each carry `utilization` (percent) and `resets_at` (ISO ts),
    plus `status`. Use this to answer "how much have I used / when does it
    reset". host = 'claude' | 'llm'.
    """
    return await _call(host, "GET", "/v1/accounts/limits")


@mcp.tool()
async def loadbalance_get(host: str = "claude") -> dict:
    """Show the load-balancer config + live pick ranking on `host`.

    Returns enabled/strategy/caps plus, per account, left5h/left7d, eligibility,
    urgency, cooldown and pick_rank — i.e. which account the LB will serve next
    and why. host = 'claude' | 'llm'.
    """
    return await _call(host, "GET", "/v1/accounts/loadbalance")


@mcp.tool()
async def models_list(host: str = "claude") -> dict:
    """List models available on `host` (GET /v1/models).

    On host='llm' this includes the LM Studio local models running on pbox
    (e.g. `local`, `qwen`, `imagegen`) alongside the proxied Claude models.
    host = 'claude' | 'llm'.
    """
    return await _call(host, "GET", "/v1/models")


# ---------------------------------------------------------------- write tools
@mcp.tool()
async def account_add(name: str, token: str, host: str = "claude",
                      skip_verify: bool = False,
                      password: Optional[str] = None) -> dict:
    """Add (or overwrite) an account from a `claude setup-token`.

    `token` MUST be a setup-token (`sk-ant-oat01-…`), obtained by running
    `claude setup-token` — NOT an API key and NOT a browser session. The token
    is verified against Anthropic before saving unless skip_verify=true. Passing
    an existing `name` overwrites it (this is also how you "edit"/rotate a
    token — there is no rename endpoint; delete + re-add to rename).
    Returns {ok, name, org, dup:[other profiles in the same Anthropic org]}.
    host = 'claude' | 'llm'.
    """
    body: dict[str, Any] = {"name": name, "token": token}
    if skip_verify:
        body["skip_verify"] = True
    if password is not None:
        body["password"] = password
    return await _call(host, "POST", "/v1/accounts/import-token", body)


@mcp.tool()
async def account_reveal_token(name: str, host: str = "claude",
                               password: Optional[str] = None) -> dict:
    """Reveal an account's stored OAuth access token so it can be copied out.

    For a setup-token login this is the long-lived `sk-ant-oat01…` value (paste
    it into another machine's CLAUDE_CODE_OAUTH_TOKEN or account_add). Sensitive
    — returns a bearer credential. host = 'claude' | 'llm'.
    """
    body: dict[str, Any] = {"name": name}
    if password is not None:
        body["password"] = password
    return await _call(host, "POST", "/v1/accounts/token", body)


@mcp.tool()
async def account_switch(name: str, host: str = "claude",
                         password: Optional[str] = None) -> dict:
    """Make `name` the active subscription on `host` (credential swap, no
    re-login). Disruptive: this changes which login the shared wrapper serves
    to every caller of that host — use deliberately. host = 'claude' | 'llm'.
    """
    body: dict[str, Any] = {"name": name}
    if password is not None:
        body["password"] = password
    return await _call(host, "POST", "/v1/accounts/switch", body)


@mcp.tool()
async def account_test(name: str, host: str = "claude") -> dict:
    """Test that account `name` on `host` still authenticates (live probe).
    host = 'claude' | 'llm'.
    """
    return await _call(host, "POST", "/v1/accounts/test", {"name": name})


@mcp.tool()
async def account_delete(name: str, host: str = "claude", force: bool = False,
                         password: Optional[str] = None) -> dict:
    """Forget account `name` on `host` — wipes its saved login.

    Refuses (HTTP 409) if `name` is the ACTIVE account unless force=true; switch
    to another account first, or pass force. Does not touch the live credential
    already in use until the next switch. host = 'claude' | 'llm'.
    """
    body: dict[str, Any] = {"name": name}
    if force:
        body["force"] = True
    if password is not None:
        body["password"] = password
    return await _call(host, "POST", "/v1/accounts/delete", body)


@mcp.tool()
async def usage_reset(name: Optional[str] = None, host: str = "claude",
                      password: Optional[str] = None) -> dict:
    """Reset the wrapper's LOCAL usage tallies (requests/input/output) on `host`.

    Pass `name` to clear one account, omit it to clear all. IMPORTANT: this only
    zeroes the wrapper's own counters shown by `accounts_list.usage`. It does
    NOT reset Anthropic's real 5h/7d limits — those reset on their own schedule
    (see `accounts_limits.resets_at`). host = 'claude' | 'llm'.
    """
    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if password is not None:
        body["password"] = password
    return await _call(host, "POST", "/ui/accounts/usage/reset", body)


@mcp.tool()
async def loadbalance_set(host: str = "claude",
                          enabled: Optional[bool] = None,
                          strategy: Optional[str] = None,
                          cap5h: Optional[float] = None,
                          cap7d: Optional[float] = None,
                          min_left: Optional[float] = None,
                          include: Optional[list[str]] = None,
                          preset: Optional[str] = None,
                          password: Optional[str] = None) -> dict:
    """Tune the load balancer on `host`.

    Pass only the fields you want to change. strategy ∈
    drain|binding|weekly|least5h|weighted. preset='drain' one-shots the
    recommended use-it-or-lose-it mode (enabled + drain + 95/95 caps). `include`
    is the whitelist of account names the LB may pick. Returns the new LB status.
    host = 'claude' | 'llm'.
    """
    body: dict[str, Any] = {}
    if preset is not None:
        body["preset"] = preset
    if enabled is not None:
        body["enabled"] = enabled
    if strategy is not None:
        body["strategy"] = strategy
    if cap5h is not None:
        body["cap5h"] = cap5h
    if cap7d is not None:
        body["cap7d"] = cap7d
    if min_left is not None:
        body["min_left"] = min_left
    if include is not None:
        body["include"] = include
    if password is not None:
        body["password"] = password
    return await _call(host, "POST", "/v1/accounts/loadbalance", body)


# ================================================================ llm-proxy
# The llm.hostbun.cc router (repo: llm-hostbun-proxy). Three lanes:
#   local  = LM Studio @ llm.bofrid.dev   (models: local/gemma/obliterated)
#   wrappy = claudebox @ claude.hostbun.cc (models: claude*)
#   crazyrouter = crazyrouter.com cloud relay (everything else)
# Control it via /api/*, cookie-gated by ADMIN_PASSWORD. We keep one session
# cookie module-wide and re-login lazily on 401.
_proxy_cookie: Optional[str] = None


async def _proxy_login(client: httpx.AsyncClient) -> Optional[str]:
    r = await client.post(f"{LLM_PROXY_BASE}/api/login",
                          json={"password": ADMIN_PASSWORD})
    if r.status_code == 200:
        return r.headers.get("set-cookie", "").split(";")[0] or None
    return None


async def _proxy_call(method: str, sub: str, body: Optional[dict] = None,
                      params: Optional[dict] = None) -> dict:
    """One call to the llm-proxy /api/<sub>, logging in if needed."""
    global _proxy_cookie
    url = f"{LLM_PROXY_BASE}/api/{sub}"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as c:
        for attempt in (1, 2):
            headers = {"Accept": "application/json"}
            if _proxy_cookie:
                headers["Cookie"] = _proxy_cookie
            try:
                if method == "GET":
                    r = await c.get(url, headers=headers, params=params or {})
                else:
                    r = await c.post(url, headers=headers, json=body or {})
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
            if r.status_code == 401 and attempt == 1:
                _proxy_cookie = await _proxy_login(c)
                if not _proxy_cookie:
                    return {"ok": False, "error": "admin login failed (bad ADMIN_PASSWORD?)"}
                continue
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {"raw": r.text[:2000]}
            if isinstance(data, dict):
                data.setdefault("http_status", r.status_code)
                return data
            return {"http_status": r.status_code, "data": data}
    return {"ok": False, "error": "unreachable"}


@mcp.tool()
async def proxy_state() -> dict:
    """Full routing config of the llm.hostbun.cc proxy (GET /api/state).

    Returns the live CFG: lanes, per-lane bases, localMap, forceModel (global
    override), modelRoutes (per-model lane pins), projectRoutes/projectGroups,
    projectLimits, cloudPolicy (open/allowlist/off) + cloudAllowlist,
    defaultRoute, jsonEnforce, logging, and masked secret flags. This is the
    'what does this proxy do right now' view.
    """
    return await _proxy_call("GET", "state")


@mcp.tool()
async def proxy_health() -> dict:
    """Live health of the proxy's three lanes (GET /api/health).
    Probes each upstream (local LM Studio / wrappy claudebox / crazyrouter) and
    returns {up,status,ms,count?,error?} per lane."""
    return await _proxy_call("GET", "health")


@mcp.tool()
async def proxy_models() -> dict:
    """Merged model catalog per lane (GET /api/models) — local + wrappy +
    crazyrouter — i.e. every model id the proxy can route, grouped by lane."""
    return await _proxy_call("GET", "models")


@mcp.tool()
async def proxy_resolve(model: str, project: Optional[str] = None) -> dict:
    """Dry-run: show exactly which lane `model` routes to, WITHOUT calling the
    upstream (POST /api/resolve). Returns lane, sentModel, reason, whether
    it's blocked/gated, and the target base. Use to debug routing."""
    return await _proxy_call("POST", "resolve", {"model": model, "project": project or ""})


@mcp.tool()
async def proxy_test(model: str, prompt: Optional[str] = None,
                     max_tokens: Optional[int] = None) -> dict:
    """Route AND call `model` through the proxy end-to-end (POST /api/test)
    — verifies the lane actually answers. Returns the routed lane + the reply."""
    body: dict[str, Any] = {"model": model}
    if prompt is not None:
        body["prompt"] = prompt
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    return await _proxy_call("POST", "test", body)


@mcp.tool()
async def proxy_stats(window: str = "24h") -> dict:
    """Usage stats over `window` (GET /api/stats) — window ∈
    15m|1h|6h|24h|7d|30d|all. Returns call/token/error/cost totals plus
    breakdowns byLane, byModel, byProject (with $ estimates for crazyrouter)."""
    return await _proxy_call("GET", "stats", params={"window": window})


@mcp.tool()
async def proxy_calls(limit: int = 30, q: Optional[str] = None,
                      lane: Optional[str] = None, model: Optional[str] = None,
                      project: Optional[str] = None,
                      status: Optional[str] = None) -> dict:
    """Recent proxy call log (GET /api/calls) — the real-time debug feed.
    Filters: q (search model/ip/ua/prompt/reply), lane, model, project,
    status ('ok'|'error'|code). Each row shows the full picture of a call:
    routing (lane, req_model→sent_model, key_label), which project, timing
    (duration_ms), model usage (prompt/completion/total_tokens), the request
    knobs (effort, thinking_tokens, max_tokens, temperature, stream), and
    req_preview/resp_preview content. limit≤500."""
    params: dict[str, Any] = {"limit": limit}
    for k, v in (("q", q), ("lane", lane), ("model", model),
                 ("project", project), ("status", status)):
        if v:
            params[k] = v
    return await _proxy_call("GET", "calls", params=params)


@mcp.tool()
async def proxy_limits() -> dict:
    """Live per-account Anthropic rate-limit snapshot (GET /api/limits),
    harvested for FREE off the `anthropic-ratelimit-unified-*` response headers of
    real anthropic-lane (local-dev Claude Code) traffic — NO probe, zero tokens,
    unlike `live_limits` on the claude host which spends a message per account.
    Returns one row per Anthropic organization id: {org_id, ts (last seen),
    u5/u7 (5h/7d utilization 0..1), reset5/reset7 (epoch s), status/s5/s7,
    project, model}. Rows go stale for accounts with no recent traffic (ts shows
    freshness). Map org_id→account name via `accounts_list` meta.organizationId."""
    return await _proxy_call("GET", "limits")


@mcp.tool()
async def proxy_config(patch: dict) -> dict:
    """Live-edit the proxy routing config (POST /api/config) — applies
    instantly, persists to /data/config.json, no redeploy.

    `patch` carries only the keys you want to change. Common ones:
      forceModel:{enabled,lane,model} — force EVERY request to one lane/model
      modelRoutes:{"<model>":{lane,rewriteModel?}} — pin a model to a lane
      cloudPolicy:"open"|"allowlist"|"off" + cloudAllowlist:[...] — crazyrouter gate
      defaultRoute:"local"|"wrappy"|"crazyrouter" — fallback lane
      projectRoutes / projectLimits — per-project routing + caps
      bases:{local,wrappy,crazyrouter} — upstream URLs
      jsonEnforce:bool, requireProject:bool, logging:{...}
      crazyrouterKey / wrappyToken / oblitToken / adminPassword — secrets
        ("" clears, omit keeps, value sets)
    Returns {ok, persisted, state}. Fetch proxy_state() first to see current values.
    """
    return await _proxy_call("POST", "config", patch or {})


@mcp.tool()
async def proxy_reset_config() -> dict:
    """Reset the proxy routing config to its env defaults (POST /api/reset)
    — deletes the /data/config.json overlay. Destructive to custom routing."""
    return await _proxy_call("POST", "reset")


@mcp.tool()
async def proxy_clear_calls() -> dict:
    """Wipe the proxy's SQLite call log (POST /api/calls/clear)."""
    return await _proxy_call("POST", "calls/clear")


# ================================================================ 5h window keeper
# Anthropic's 5-hour usage window is a FIXED block: it starts on the first message
# and resets at a locked time (more messages don't move it). You can't keep one
# window fresh — but you can control WHEN each account's window STARTS and re-prime
# right after each reset. Priming the 5 accounts staggered => their resets spread
# out => there's always an account with a freshly-started, full-headroom window
# (which the load balancer then routes to). Priming = a 1-token message via the
# account's own token; it does NOT add capacity, only spreads the resets.
from datetime import datetime, timezone


def _cold_score(fh: dict) -> Optional[float]:
    """How 'cold' (reset / low) an account's 5h window is — higher = colder /
    more in need of a fresh start. None if the window looks healthy-active."""
    if not isinstance(fh, dict):
        return 1e9
    util = fh.get("utilization")
    ra = fh.get("resets_at")
    if not ra:
        return 1e9                      # no active window -> coldest
    try:
        secs = (datetime.fromisoformat(ra.replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return None
    if secs <= 0:
        return 1e9                      # window already reset, not restarted
    # active window: colder = lower utilization + closer to reset
    return (100 - (util or 0)) + (300 - secs / 60) * 0.1


async def _prime(name: str, host: str = "claude") -> dict:
    """Send a 1-token message as `name` and read Anthropic's LIVE rate-limit headers
    (the authoritative truth — the wrapper's /v1/accounts/limits is cached and can
    badly understate the 7-day usage). Returns 5h + 7d util/status/reset + which
    limit is BINDING. This both starts/keeps the 5h window AND probes real state."""
    tok = await _call(host, "POST", "/v1/accounts/token", {"name": name})
    token = tok.get("token") if isinstance(tok, dict) else None
    if not token:
        return {"ok": False, "name": name, "error": tok.get("error", "no token")}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as c:
            r = await c.post("https://api.anthropic.com/v1/messages",
                             headers={"authorization": f"Bearer {token}",
                                      "anthropic-version": "2023-06-01",
                                      "anthropic-beta": "oauth-2025-04-20,claude-code-20250219",
                                      "content-type": "application/json"},
                             json={"model": PRIME_MODEL, "max_tokens": 1,
                                   "system": _CC_SYSTEM,
                                   "messages": [{"role": "user", "content": "."}]})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "name": name, "error": f"{type(e).__name__}: {e}"[:200]}
    h = r.headers

    def g(k):
        return h.get(f"anthropic-ratelimit-unified-{k}")

    def futs(k):
        try:
            import time as _t
            return round((int(g(k)) - _t.time()) / 3600, 1)
        except Exception:
            return None
    return {
        "ok": r.status_code in (200, 429), "name": name, "http": r.status_code,
        "binding": g("representative-claim"),
        "u5": float(g("5h-utilization") or 0) * 100, "reset5_h": futs("5h-reset"),
        "s5": g("5h-status"),
        "u7": float(g("7d-utilization") or 0) * 100, "reset7_h": futs("7d-reset"),
        "s7": g("7d-status"),
        "usable": g("status") != "rejected",
    }


@mcp.tool()
async def window_status(host: str = "claude") -> dict:
    """Per-account 5h window state: utilization, resets_at, and whether the window
    is 'cold' (reset / not started). Use to see the stagger of resets across
    accounts. host = 'claude' | 'llm'."""
    lim = await _call(host, "GET", "/v1/accounts/limits")
    byname = lim.get("limits", {}) if isinstance(lim, dict) else {}
    out = []
    for name, v in byname.items():
        fh = v.get("five_hour") or {}
        cs = _cold_score(fh)
        out.append({"name": name, "util": fh.get("utilization"),
                    "resets_at": fh.get("resets_at"),
                    "cold": cs is not None and cs >= 1e9})
    out.sort(key=lambda x: (x["util"] if isinstance(x["util"], (int, float)) else 999))
    return {"keeper_enabled": KEEPER_ENABLED, "keeper_interval_s": KEEPER_INTERVAL,
            "accounts": out}


@mcp.tool()
async def live_limits(host: str = "claude") -> dict:
    """GROUND TRUTH capacity for every account — probes Anthropic directly (1-token
    message each) and reads the live rate-limit headers, bypassing the wrapper's
    cached (often wrong) /v1/accounts/limits. Shows per account: binding limit
    (five_hour|seven_day), 5h & 7d utilization/status/reset-hours, and `usable`.
    IMPORTANT: the 7-DAY window is frequently the real binding limit — an account
    can have a fresh 5h but be dead for ~a day on its 7d cap. host = 'claude'|'llm'.
    """
    accts = await _call(host, "GET", "/v1/accounts")
    names = [a.get("name") for a in (accts.get("accounts") or []) if a.get("name")]
    rows = [await _prime(n, host) for n in names]
    usable = [r["name"] for r in rows if r.get("usable")]
    return {"accounts": rows, "usable": usable, "note":
            "7d is often the binding limit; wrapper /v1/accounts/limits is cached & can understate it"}


@mcp.tool()
async def when_usable(host: str = "claude") -> dict:
    """ACTIONABLE 'can I use it, and if not when' view — probes live (like live_limits)
    then adds, per account: `usable` now, `back_in_h` (hours until it re-enters the
    pool) and `blocked_by` ('five_hour'|'seven_day'|None) so you know WHICH window is
    the wall, plus `drain_score` = weekly_left / hours_to_7d_reset × 24 (higher =
    burn it sooner; this is exactly what autoswitch/the LB rank by). Returns
    `usable_now`, the soonest `next_back` account, and `next_pick` = the account
    autoswitch/gateway will drain next (highest drain_score among currently-usable).
    Beats a raw '% left' number: an account can show weekly headroom yet be unusable
    because its 5h is maxed. host = 'claude' | 'llm'."""
    accts = await _call(host, "GET", "/v1/accounts")
    names = [a.get("name") for a in (accts.get("accounts") or []) if a.get("name")]
    rows = [await _prime(n, host) for n in names]
    out = []
    for r in rows:
        if not r.get("ok"):
            out.append({"name": r.get("name"), "error": r.get("error"), "usable": False})
            continue
        u5, u7 = r.get("u5") or 0, r.get("u7") or 0
        r5, r7 = r.get("reset5_h"), r.get("reset7_h")
        usable = bool(r.get("usable"))
        # Which window walls it, and hours until it clears.
        blocked_by, back_in = None, 0.0
        if not usable:
            five_dead = (r.get("s5") == "rejected") or u5 >= 100
            seven_dead = (r.get("s7") == "rejected") or u7 >= 100
            if seven_dead and (not five_dead or (r7 or 0) >= (r5 or 0)):
                blocked_by, back_in = "seven_day", (r7 or 0)
            else:
                blocked_by, back_in = "five_hour", (r5 or 0)
        week_left = max(0.0, 100.0 - u7)
        drain = round((week_left / r7) * 24, 1) if r7 and r7 > 0 else None
        out.append({
            "name": r["name"], "usable": usable,
            "blocked_by": blocked_by, "back_in_h": round(back_in, 1),
            "week_left_pct": round(week_left), "reset7_h": r7,
            "u5": round(u5), "u7": round(u7), "drain_score": drain,
        })
    usable_now = [o["name"] for o in out if o.get("usable")]
    waiting = sorted([o for o in out if not o.get("usable") and "back_in_h" in o],
                     key=lambda o: o["back_in_h"])
    next_back = waiting[0] if waiting else None
    pickable = [o for o in out if o.get("usable") and o.get("drain_score") is not None]
    next_pick = max(pickable, key=lambda o: o["drain_score"]) if pickable else None
    return {
        "usable_now": usable_now,
        "next_pick": (next_pick or {}).get("name"),
        "next_back": {"name": (next_back or {}).get("name"),
                      "in_h": (next_back or {}).get("back_in_h")} if next_back else None,
        "accounts": sorted(out, key=lambda o: (not o.get("usable"), o.get("back_in_h", 0))),
        "note": "usable = BOTH 5h and 7d allow; a high week_left with usable=false means 5h is the wall",
    }


@mcp.tool()
async def prime(name: Optional[str] = None, host: str = "claude") -> dict:
    """Manually start/keep the 5h window for one account (`name`) or ALL accounts
    (omit name). Sends a 1-token message via each account's token. host default
    'claude'."""
    if name:
        return await _prime(name, host)
    accts = await _call(host, "GET", "/v1/accounts")
    names = [a.get("name") for a in (accts.get("accounts") or []) if a.get("name")]
    res = [await _prime(n, host) for n in names]
    return {"primed": res}


@mcp.tool()
async def usage_today(window: str = "24h", host: str = "claude") -> dict:
    """One-shot "what got used and what drained each account" view — fuses the two
    data sources, because NEITHER alone has the full picture:

      • `claude.hostbun.cc` wrapper knows WHICH ACCOUNT is drained (per-account
        5h/7d utilization + reset times + cumulative req/in/out totals) but has
        NO per-model / per-day / per-call breakdown.
      • `llm.hostbun.cc` router knows WHAT ran (per-model + per-project token
        breakdown over `window`) but sees the whole subscription pool as one
        'wrappy' lane — it can't attribute a call back to a single account.

    So true per-account-per-model attribution does NOT exist (the wrapper's load
    balancer picks the account per call and never logs the model→account map).
    This tool returns the closest possible: `accounts` (who's drained, from the
    wrapper) + `by_model` / `by_project` (what drained the pool, from the router,
    wrappy lane only). window ∈ 15m|1h|6h|24h|7d|30d|all (default 24h)."""
    # who is drained — wrapper windows + totals
    lim = await _call(host, "GET", "/v1/accounts/limits")
    accts = await _call(host, "GET", "/v1/accounts")
    totals = {a.get("name"): a.get("usage", {}) for a in (accts.get("accounts") or [])}
    accounts = []
    for name, v in (lim.get("limits", {}) if isinstance(lim, dict) else {}).items():
        fh = v.get("five_hour") or {}
        sd = v.get("seven_day") or {}
        u = totals.get(name, {})
        f5, s7 = fh.get("utilization"), sd.get("utilization")
        accounts.append({
            "name": name,
            "five_hour_pct": f5, "five_hour_resets_at": fh.get("resets_at"),
            "seven_day_pct": s7, "seven_day_resets_at": sd.get("resets_at"),
            "dead": (isinstance(f5, (int, float)) and f5 >= 100) or
                    (isinstance(s7, (int, float)) and s7 >= 100),
            "lifetime": {"requests": u.get("requests"), "input": u.get("input"),
                         "output": u.get("output")},
        })
    accounts.sort(key=lambda a: -(a["five_hour_pct"] or 0))

    # what ran — router per-model / per-project (wrappy lane = this subscription pool)
    stats = await _proxy_call("GET", "stats", params={"window": window})
    wrappy = None
    for l in (stats.get("byLane") or []) if isinstance(stats, dict) else []:
        if l.get("lane") == "wrappy":
            wrappy = l
            break
    return {
        "window": window,
        "note": "per-account totals from the wrapper; by_model/by_project from the "
                "router (wrappy lane only) — no per-account-per-model split exists",
        "accounts": accounts,
        "wrappy_lane": wrappy,
        "by_model": stats.get("byModel") if isinstance(stats, dict) else None,
        "by_project": stats.get("byProject") if isinstance(stats, dict) else None,
        "router_totals": {k: stats.get(k) for k in
                          ("windowCalls", "windowTokens", "windowPromptTokens",
                           "windowCompletionTokens", "windowErrors")}
        if isinstance(stats, dict) else None,
    }


async def _keeper_loop():
    import asyncio
    log.info("window keeper started (interval=%ss)", KEEPER_INTERVAL)
    while True:
        try:
            await asyncio.sleep(KEEPER_INTERVAL)
            # Use the cached limits only to pick 5h-cold CANDIDATES, then prime them
            # in coldest order until one lands on a 7d-USABLE account. _prime returns
            # live headers, so a 7d-dead account (like a maxed philip) is detected and
            # skipped instead of wasting the stagger slot on an unusable account.
            lim = await _call("claude", "GET", "/v1/accounts/limits")
            byname = lim.get("limits", {}) if isinstance(lim, dict) else {}
            cand = [(n, _cold_score(v.get("five_hour") or {})) for n, v in byname.items()]
            cand = [n for n, s in sorted(cand, key=lambda x: -(x[1] or 0)) if s and s >= 1e9]
            for name in cand:
                r = await _prime(name)
                if r.get("usable"):
                    log.info("keeper primed %s (5h=%.0f%% 7d=%.0f%%)", name, r.get("u5", 0), r.get("u7", 0))
                    break
                log.info("keeper skip %s — 7d binding/dead (7d=%.0f%% %s)", name, r.get("u7", 0), r.get("s7"))
        except Exception as e:  # noqa: BLE001
            log.warning("keeper error: %s", e)


# ---------------------------------------------------------------- fleet presence
# "Who is using what across the fleet." Each box's statusline knows, via a live
# Anthropic call, which account its LOCAL keychain token really is (the org-id ->
# account map). It POSTs that here; this server keeps the latest per-machine and
# renders it — joined with the wrapper's per-account limits — as a JSON feed (for
# the cccc TUI) and an HTML page (GET /fleet). The gateway itself CANNOT provide
# this: it only knows its own single active login, not which remote machine holds
# which token. This registry is that missing cross-machine view.
_PRESENCE_FILE = os.environ.get("PRESENCE_FILE", "/tmp/claudectl-presence.json")
_PRESENCE_STALE = int(os.environ.get("PRESENCE_STALE", "1800"))   # gray out after 30 min silent
_PRESENCE_EVICT = int(os.environ.get("PRESENCE_EVICT", "21600"))  # forget entirely after 6 h silent
_presence_lock = threading.Lock()


def _presence_load() -> dict:
    try:
        with open(_PRESENCE_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _presence_put(machine: str, account: str, org_id: str) -> None:
    machine = (machine or "").strip()[:64]
    if not machine:
        return
    with _presence_lock:
        d = _presence_load()
        now = time.time()
        d[machine] = {"account": (account or "").strip()[:64],
                      "org_id": (org_id or "").strip()[:64], "ts": now}
        # opportunistic prune: a box silent past the evict window is forgotten, so
        # decommissioned/renamed machines (e.g. an old hostname) drop off the page.
        d = {m: v for m, v in d.items() if now - (v.get("ts") or 0) <= _PRESENCE_EVICT}
        try:
            with open(_PRESENCE_FILE, "w") as f:
                json.dump(d, f)
        except OSError:
            pass


@mcp.tool()
async def fleet_presence(host: str = "claude") -> dict:
    """Who's using what across the fleet: every machine's last-published account
    (each box's statusline verifies its OWN keychain token against Anthropic and
    reports it here), joined with the wrapper's per-account 5h/7d limits. The
    gateway only knows its single active login — this is the cross-machine view it
    can't give. Returns {machines, accounts} where each account lists the machines
    currently on it."""
    pres = _presence_load()
    accts = await _call(host, "GET", "/v1/accounts")
    lim = await _call(host, "GET", "/v1/accounts/limits")
    acct_objs = [a for a in (accts.get("accounts") or []) if a.get("name")]
    active_name = accts.get("active") if isinstance(accts.get("active"), str) else None
    limits = lim.get("limits", {}) if isinstance(lim, dict) else {}
    now = time.time()
    machines = {m: {**v, "age_s": round(now - (v.get("ts") or 0)),
                    "stale": (now - (v.get("ts") or 0)) > _PRESENCE_STALE}
                for m, v in pres.items()
                if now - (v.get("ts") or 0) <= _PRESENCE_EVICT}   # forget long-silent boxes
    names = [a["name"] for a in acct_objs]
    by_acct = {}
    for m, v in machines.items():
        by_acct.setdefault(v.get("account") or "?", []).append(m)
    accounts = []
    for a in acct_objs:
        n = a["name"]
        lv = limits.get(n, {}) or {}
        fh, sd = lv.get("five_hour") or {}, lv.get("seven_day") or {}
        accounts.append({
            "name": n, "gateway_active": bool(a.get("active")) or n == active_name,
            "five_hour_pct": fh.get("utilization"), "five_hour_resets_at": fh.get("resets_at"),
            "seven_day_pct": sd.get("utilization"), "seven_day_resets_at": sd.get("resets_at"),
            "machines": sorted(by_acct.get(n, [])),
        })
    orphan = sorted(m for m, v in machines.items()
                    if (v.get("account") or "?") not in names)
    return {"machines": machines, "accounts": accounts, "orphan_machines": orphan,
            "note": "machine.account is API-verified by that box; limits are the wrapper's cached values"}


def _fleet_html(data: dict) -> str:
    """Render fleet_presence() as a self-contained auto-refreshing HTML page."""
    def esc(x):
        return _html.escape(str(x))

    def bar(pct):
        p = pct if isinstance(pct, (int, float)) else None
        if p is None:
            return '<span class="dim">—</span>'
        col = "g" if p < 50 else "y" if p < 80 else "r"
        return (f'<span class="bar"><span class="fill {col}" style="width:{min(100, p):.0f}%"></span></span>'
                f'<span class="pct">{p:.0f}%</span>')

    def ago(s):
        if not isinstance(s, (int, float)):
            return ""
        s = int(s)
        return f"{s}s" if s < 60 else f"{s // 60}m" if s < 3600 else f"{s // 3600}h"

    rows = []
    for a in data.get("accounts", []):
        star = " ★" if a.get("gateway_active") else ""
        machs = a.get("machines") or []
        mtags = " ".join(f'<span class="mach">{esc(m)}</span>' for m in machs) or '<span class="dim">— idle —</span>'
        rows.append(f"""
        <tr>
          <td class="acct">{esc(a['name'])}<span class="star">{star}</span></td>
          <td>{bar(a.get('five_hour_pct'))}</td>
          <td>{bar(a.get('seven_day_pct'))}</td>
          <td class="machs">{mtags}</td>
        </tr>""")
    orphans = data.get("orphan_machines") or []
    orow = ""
    if orphans:
        tags = " ".join(f'<span class="mach warn">{esc(m)}</span>' for m in orphans)
        orow = f'<p class="orphan">⚠ machines on an unknown/removed account: {tags}</p>'
    machines = data.get("machines", {})
    seen = (f'<p class="dim">{len(machines)} machine(s) reporting · '
            f'auto-refresh 15s · limits are the wrapper\'s cached values (7d may understate)</p>')
    # per-machine last-seen footnote
    foot = " · ".join(
        f'{esc(m)}<span class="dim">{("→" + esc(v.get("account"))) if v.get("account") else ""} '
        f'{ago(v.get("age_s"))} ago{" (stale)" if v.get("stale") else ""}</span>'
        for m, v in sorted(machines.items()))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>claudectl · fleet</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
         background:#0d1117; color:#e6edf3; margin:0; padding:28px; }}
  h1 {{ font-size:16px; margin:0 0 4px; font-weight:600; }}
  h1 .sub {{ color:#7d8590; font-weight:400; font-size:13px; }}
  table {{ border-collapse:collapse; width:100%; max-width:820px; margin-top:16px; }}
  th {{ text-align:left; font-weight:600; color:#7d8590; font-size:12px;
        text-transform:uppercase; letter-spacing:.04em; padding:6px 12px 6px 0; }}
  td {{ padding:9px 12px 9px 0; border-top:1px solid #21262d; vertical-align:middle; }}
  .acct {{ font-weight:600; white-space:nowrap; }}
  .star {{ color:#e3b341; }}
  .bar {{ display:inline-block; width:90px; height:8px; border-radius:4px;
          background:#21262d; overflow:hidden; vertical-align:middle; margin-right:8px; }}
  .fill {{ display:block; height:100%; }}
  .fill.g {{ background:#3fb950; }} .fill.y {{ background:#d29922; }} .fill.r {{ background:#f85149; }}
  .pct {{ font-variant-numeric:tabular-nums; color:#c9d1d9; }}
  .mach {{ display:inline-block; background:#1f6feb33; color:#79c0ff; border:1px solid #1f6feb55;
           border-radius:5px; padding:1px 8px; margin:2px 3px 2px 0; font-size:13px; }}
  .mach.warn {{ background:#f8514922; color:#ff7b72; border-color:#f8514955; }}
  .dim {{ color:#7d8590; }} .orphan {{ margin-top:14px; }}
  footer {{ margin-top:22px; color:#7d8590; font-size:12px; max-width:820px; }}
</style></head><body>
  <h1>claudectl · fleet <span class="sub">— who's using what</span></h1>
  {seen}
  <table>
    <tr><th>account</th><th>5h used</th><th>7d used</th><th>machines on it</th></tr>
    {''.join(rows)}
  </table>
  {orow}
  <footer>last seen — {foot or '<span class="dim">none</span>'}</footer>
</body></html>"""


def _install_fleet_routes(app) -> None:
    """Attach the presence feed + HTML page to the FastMCP Starlette app."""
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, JSONResponse

    async def presence_post(request: Request):
        try:
            b = await request.json()
        except Exception:  # noqa: BLE001
            b = {}
        if not (b.get("machine") or "").strip():
            return JSONResponse({"error": "machine required"}, status_code=400)
        _presence_put(b.get("machine", ""), b.get("account", ""), b.get("org_id", ""))
        return JSONResponse({"ok": True})

    async def presence_get(request: Request):
        return JSONResponse(await fleet_presence())

    async def fleet_page(request: Request):
        return HTMLResponse(_fleet_html(await fleet_presence()))

    app.add_route("/presence", presence_post, methods=["POST"])
    app.add_route("/presence", presence_get, methods=["GET"])
    app.add_route("/fleet", fleet_page, methods=["GET"])


if __name__ == "__main__":
    if TRANSPORT == "stdio":
        mcp.run(transport="stdio")
    else:
        import uvicorn
        import asyncio
        import threading
        from _auth import BearerMiddleware
        if KEEPER_ENABLED:
            threading.Thread(
                target=lambda: asyncio.new_event_loop().run_until_complete(_keeper_loop()),
                daemon=True).start()
        app = mcp.streamable_http_app()
        _install_fleet_routes(app)
        uvicorn.run(
            BearerMiddleware(app),
            host="0.0.0.0",
            port=int(os.environ.get("PORT", "8000")),
        )
