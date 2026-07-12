---
name: claudectl
description: >-
  Control everything behind claude.hostbun.cc and llm.hostbun.cc via the `claudectl`
  MCP + the `cccc` TUI: manage Claude Max subscription accounts (list, REAL 5h/7d
  limits, switch, add/rename/delete, prime windows, refresh ccc panes) AND the
  llm.hostbun.cc model router (lanes, routing, health, stats). Load whenever the user
  is "hitting the limit", asks to "switch Claude account", mentions "claude.hostbun.cc",
  "llm.hostbun.cc", "5h window", "7d limit", "which account", "cccc", "usage reset",
  "which lane / model routing", "crazyrouter", "local model", lane health, or an
  account shows an auth/limit error. CRITICAL: the binding limit is usually the 7-DAY
  window and the gateway's cached numbers lie — use `live_limits` for the truth.
---

# claudectl — accounts (claude.hostbun.cc) + LLM router (llm.hostbun.cc)

Both hosts are driven by the one local `claudectl` MCP (stdio, ships with this
plugin) and the `cccc` curses TUI. Static bearer / admin password: `ddash`.

- **`claude.hostbun.cc`** = the **claudebox** gateway (claude-code-openai-wrapper):
  load-balances several **Claude Max subscription** logins so you get N× the budget
  from one endpoint. → the account tools below.
- **`llm.hostbun.cc`** = the **llm-hostbun-proxy** model router. It *also* proxies
  `/v1/*` through to claude.hostbun.cc (the "wrappy" lane), which is why both hosts
  **share the same Claude accounts**. → the `proxy_*` tools below.

Every account tool takes `host` = `claude` (default) | `llm`.

---

## PART 1 — Accounts & limits

### ⚠️ The one thing that matters most: 7-day vs 5-hour

Claude Max accounts have **two** rolling limits, and either can block you:

| window | behaviour |
|---|---|
| **5-hour** | short-term burst cap; a fixed block that starts on your first message and resets 5h later. Resets often. |
| **7-day** | sustained weekly cap. When it maxes, the account is **dead for ~a day+** regardless of the 5h window. |

**The 7-DAY window is usually the real binding limit.** An account can show a fresh
5h (0%) yet be **rejected** because its 7d is at 100% (`representative-claim:
seven_day`, `retry-after` ~30-40h).

**The gateway's `/v1/accounts/limits` is CACHED and can badly understate the 7d**
(seen: reported 73% when the live value was 100%). Any UI reading it may think a dead
account is fine and route you there → "limit reached".

➡️ **Always trust `live_limits`** (probes Anthropic's real rate-limit headers per
account), not `accounts_limits` (cached). Route only to accounts where
`usable == true`.

Priming/staggering the 5h windows **cannot fix 7-day exhaustion** — that's a hard
weekly cap. More accounts = more weekly budget; otherwise use less.

### Account tools

Read:
- `accounts_list` — names, active flag, meta, wrapper-local usage tallies.
- `accounts_limits` — 5h/7d util + resets_at **(CACHED — can be wrong; prefer live_limits)**.
- `live_limits` — **GROUND TRUTH**: per account 5h & 7d utilization/status/reset-hours,
  which limit is binding, and a `usable` flag. The one to trust.
- `when_usable` — actionable "can I use it, and if not when" (back_in_h, blocked_by).
- `window_status` — 5h util + resets_at + cold flag; shows the stagger.
- `usage_today(window?)` — **"what got used + what drained each account"** in one call.
  Fuses both sources: `accounts` = who's drained (wrapper 5h/7d + lifetime totals);
  `by_model`/`by_project` = what ran (llm router, wrappy lane, default 24h). No
  per-account-per-model split exists — the LB never logs model→account.
- `fleet_presence` — who's on what account across every machine.
- `loadbalance_get` — LB config + live pick ranking. `models_list` — models on the host.

Write:
- `account_switch(name)` — make an account active (LB off ⇒ every request uses it).
- `account_add(name, token)` — add from a `claude setup-token` (`sk-ant-oat01…`).
- `account_reveal_token(name)` — copy the long-lived token out.
- `account_delete(name, force?)` · `account_test(name)` · `usage_reset(name?)`.
- `loadbalance_set(...)` — tune strategy/caps/include/preset.
- `prime(name?)` — send a 1-token message to start/keep an account's 5h window.

### Switching only affects NEW sessions

Switching (via `cccc` or `account_switch` + local keychain apply) changes the account
for **new** `claude`/`ccc` launches. Already-running sessions keep their token until
restarted. `cccc refresh --go` restarts running cmux `ccc` panes onto the current
account, resuming each conversation (`kill -TERM` → wait → `ccc --resume <id>`).

**Do NOT** set `ANTHROPIC_BASE_URL=https://claude.hostbun.cc` for real Claude Code —
the wrapper is a CLI-backed text shim and 422s on tool/array-system bodies. Account
switching for interactive Claude Code goes through the **macOS keychain**, not the
gateway. (`local_apply()` swaps the `claudeAiOauth` block; `★` in the TUI = who your
local `claude` launches as, `●` = the gateway's active account.)

### The 5h keeper & token owner

- Optional background keeper (`KEEPER_ENABLED=1`) primes the coldest-reset *usable*
  account each interval so 5h windows start staggered — smooths short-term 5h
  availability, does NOT add capacity or fix 7d exhaustion. It skips 7d-dead accounts.
- The real email owning a token is **not derivable** (inference-only setup-tokens;
  profile endpoint needs `user:profile` scope). The `owner` column is best-effort;
  rename accounts to any identity you like.

---

## PART 2 — LLM router (llm.hostbun.cc)

One OpenAI/Anthropic-compatible endpoint (`https://llm.hostbun.cc/v1`) that picks a
provider **by model name**:

| lane | select with `model` | upstream |
|---|---|---|
| **local** | `local` / `gemma` / `obliterated` | llama.cpp container @ pbox |
| **wrappy** | `claude*` (e.g. `claude-sonnet-4-6`) | claudebox @ `claude.hostbun.cc` |
| **crazyrouter** | anything else (`gemini-*`, `gpt-*`, …) | crazyrouter.com cloud relay |

Its own admin surface is `/admin/api/*`, cookie-gated by `ADMIN_PASSWORD` (default
`ddash`) — the `claudectl` MCP handles the login for you.

### `proxy_*` tools

- `proxy_state` — full live routing config: lanes, bases, `forceModel` (global
  override), `modelRoutes` (per-model pins), `projectRoutes`, `cloudPolicy`
  (open/allowlist/off) + allowlist, `defaultRoute`, masked secrets.
- `proxy_health` — probe each lane (local / wrappy / crazyrouter): up? ms? model count.
- `proxy_models` — merged model catalog per lane. `proxy_limits` — free per-account
  rate-limit snapshot harvested off real anthropic-lane traffic headers.
- `proxy_resolve(model, project?)` — **dry-run** which lane a model routes to (no call).
- `proxy_test(model, prompt?)` — route AND call a model end-to-end.
- `proxy_stats(window?)` — usage over 15m|1h|6h|24h|7d|30d|all: calls/tokens/errors/
  cost, byLane / byModel / byProject.
- `proxy_calls(...)` — recent call log (filter by lane/model/project/status/search).
- `proxy_config(patch)` — **live-edit routing** (applies instantly, persists):
  `{forceModel:{enabled,lane,model}}`, `{modelRoutes:{"<model>":{lane,rewriteModel?}}}`,
  `{cloudPolicy:"open|allowlist|off", cloudAllowlist:[...]}`, `{defaultRoute:"…"}`,
  `{bases:{…}}`, secrets (`crazyrouterKey`/`wrappyToken`/`oblitToken`/`adminPassword`).
- `proxy_reset_config` — reset routing to env defaults. `proxy_clear_calls` — wipe log.

### Common tasks

- "Which lane does model X use?" → `proxy_resolve(model=X)`.
- "Is the local model up?" → `proxy_health`.
- "Pin `gemini-2.5-pro` to crazyrouter" → `proxy_config({modelRoutes:{"gemini-2.5-pro":{lane:"crazyrouter"}}})`.
- "Force everything to a lane" → `proxy_config({forceModel:{enabled:true,lane:"wrappy",model:"claude-sonnet-4-6"}})`.
- "Usage/cost this week" → `proxy_stats(window="7d")`.

**Not over HTTP:** local model load/unload and the router container restart are
host-level ops on pbox — SSH to pbox for those. This router can only *list* local
models and route to whatever is loaded.

---

## The `cccc` TUI

`cccc` (`tui/claudectl_tui.py`) — live curses dashboard: auto-refresh every 2s,
colour-coded usage, per-account 5h/7d + reset countdown, owner column, `★` local
launch account, `●` gateway active account.

Keys: `↑↓` move · `enter` switch (local keychain **and** gateway) · `n` rename ·
`a` add · `d` delete · `t` test · `e` reveal token · `u` reset usage · `L` LB ·
`g` drain preset · `m` models · `p` llm-proxy dashboard · `R` resolve model · `q` quit.

Install: `git clone git@github.com:devdashco/claudectl ~/.claudectl && sh ~/.claudectl/install.sh` (private repo → SSH clone).

Beyond accounts + router, the same `claudectl` MCP also steers terminals across boxes
(`terminals_*`) and manages plugins/marketplaces/MCP on this box
(`plugins_available`, `plugin_install`, `marketplace_*`, `mcp_*`, `reload_apply`).
