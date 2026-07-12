# cccc — operate the Claude Max fleet behind llm.hostbun.cc

`cccc` is the control surface over the pool of Claude Max subscriptions that the
**llm.hostbun.cc router** (this repo) load-balances behind one gateway. It lives here,
in the `cccc/` subdir, because it drives the router's own `/api/*` admin surface —
account list, live 5h/7d limits, and the server-side account lock — so tool and
gateway ship together.

> Moved here from the old `devdashco/claudectl` repo. That repo now holds **only the
> cmux Dock** (its own `cmuxdock` plugin). `claude.hostbun.cc` (the old claudebox
> account wrapper) is retired; everything reads `llm.hostbun.cc/api/*` now.

## What's here

- **`tui/`** — `cccc`, the curses dashboard (`claudectl_tui.py`). Tabs: Accounts ·
  Windows · Plugins · Setup. Subcommands (headless / menu actions): `cccc refresh`,
  `cccc doctor`, `cccc sync`, `cccc panes`. Plus `cccc_gateway.py` (route the local
  `claude` through the gateway) and `cccc_sync.py` (cron pull + re-install).
- **`statusline/cccc-statusline.py`** — the one shared statusline (pure stdlib).
- **`server/`** — the MCP server source (account/limit/proxy tools, httpx →
  llm.hostbun.cc). Imported verbatim by the plugin's local stdio MCP.
- **`plugins/claudectl/`** — the Claude Code plugin: one local `claudectl` stdio MCP
  (~48 tools) + skills + the `/cccc` command.

## Install

```sh
sh cccc/install.sh      # puts cccc + cccp/cccd/cccr/cccs on ~/.local/bin, wires the
                        # statusline, installs the local claudectl MCP deps
```

The cmux Dock (`cmuxdock`/`cccl`) installs separately from the `devdashco/claudectl`
repo.

## Accounts / limits (read before touching limit logic)

Max accounts have a **5-hour** burst limit AND a **7-day** weekly limit; the 7-day is
usually the binding one. The router's `/api/limits` reports Anthropic's real
rate-limit headers per account (`u5`/`u7`/`status`, keyed by org-id) — trust that.
`/api/state:claudecodeAccountPool` maps account name ↔ org-id. `cccc` joins the two.

**Pinning is server-side now.** Tokens live in the router; there is no keychain
token to swap. `cccc` "switch" sets this box's consumer→account lock in the router's
`projectAccounts` map (via `/api/config`), and the box must route through the gateway
(`ANTHROPIC_BASE_URL=https://llm.hostbun.cc`) for the lock to bill.
