# llm-hostbun-router

Node router (zero deps) behind `https://llm.hostbun.cc`. One OpenAI-compatible base URL —
`/v1` — picks the provider from the model id. Deployed on hostbun Coolify from
`devdashco/llm-hostbun-router`, branch `master`.

Renamed from `llm-hostbun-proxy` (2026-07-09). Old refs may linger in sibling repos.

## Layout

- `server.js` — the whole router: lanes, live `CFG`, admin API, SQLite call log.
- `translate.js` — OpenAI ↔ Anthropic request/response translation (`translate.test.js`).
- `admin/` — password-gated SPA (Preact + htm, vendored inline, no CDN). pw `ddash`.
- `docs/` — static docs, served at `docs.llm.hostbun.cc`.
- `headroom-svc/` — optional Python compression sidecar. Separate Coolify app, **same repo**
  (base dir `headroom-svc`). OFF unless `HEADROOM_URL` is set. Never applied to the
  `claudecode` lane — it rewrites the prompt and would miss the prompt cache.

## Lanes

`local` (LM Studio @ `llm.bofrid.dev`) · `crazyrouter` (cloud relay, key injected) ·
`claudecode` (Claude Max account pool → `api.anthropic.com`).

Legacy lane ids still migrate: `cloud`→`crazyrouter`, `claude`/`wrappy`/`anthropic`→`claudecode`.
The old claudebox/wrappy subprocess gateway at `claude.hostbun.cc` is **deleted** — the
router now talks to the real Anthropic API with a pinned account's `sk-ant-oat…` token.

Routing lives in a mutable `CFG` seeded from env, overlaid with `/data/config.json` on a
persistent volume, editable live from `/admin`. Changes apply without redeploy.

## Deploy

Pushing does **not** auto-build. Trigger the Coolify deploy for app uuid
`d11s05nc130l2kjzr6anpebr` (token in keyvault), then verify — don't stop at `git push`.
The headroom sidecar is app `i7pfies89s3maf390ye3rllk`.

## Connection to `devdashco/claudectl`

`claudectl` (local clone: `~/Documents/GitHub/claudectl`) is the **control plane** for this
router. It ships a Claude Code plugin (MCP tools + skills), the `cccc` terminal dashboard,
and is itself deployed as the `mcp-claudectl` Coolify app at `claudectl.hostbun.cc`.

Its `proxy_*` MCP tools drive this repo over the admin API — they log in at
`POST /admin/api/login` (password `ADMIN_PASSWORD`) and then hit `/admin/api/<sub>`:

| Tool | What it reads/writes here |
|------|---------------------------|
| `proxy_state`, `proxy_config`, `proxy_reset_config` | the live `CFG` (lanes, overrides, forceModel) |
| `proxy_health`, `proxy_models`, `proxy_resolve`, `proxy_test` | lane health, merged catalog, route a model id |
| `proxy_stats`, `proxy_calls`, `proxy_clear_calls` | the SQLite call log + per-project usage |
| `proxy_limits`, `accounts_*`, `live_limits`, `window_status` | the Claude Max account pool feeding the `claudecode` lane |

Consequences worth remembering:

- **Config changes made via `proxy_config` are the same writes as the `/admin` UI.** They land
  in `/data/config.json` and survive restarts. Don't hand-edit the volume.
- **The account pool is shared.** `accounts_*` tools and this router's `claudecodeAccountPool`
  describe the same Claude Max logins. Exhausting a 5h window in one shows up in the other.
- **`claudectl` docs are partly stale** — its README still describes `claude.hostbun.cc` as a
  separate claudebox gateway. It isn't; that lane collapsed into `claudecode` here.
- If you change an admin API route, lane id, or the `CFG` shape, **check
  `claudectl/server/claudectl_server.py`** — it hardcodes these paths and will break silently.
