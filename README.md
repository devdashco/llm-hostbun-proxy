# llm-hostbun-proxy

Caddy reverse proxy behind `llm.hostbun.cc` (deployed on hostbun Coolify).

- `https://llm.hostbun.cc/v1/*` → `crazyrouter.com` (full API; `CRAZYROUTER_KEY` injected server-side)
- `https://llm.hostbun.cc/local/v1/*` → `https://llm.bofrid.dev` (local LM Studio / pbox gemma)

Env: `CRAZYROUTER_KEY` (set in Coolify, never committed).
