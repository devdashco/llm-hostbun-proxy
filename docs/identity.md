# Identity: projects, jobs, keys

## A consumer is WHO calls

Exactly two kinds, and they are not the same thing.

| kind | what it is | has an owner? |
|---|---|---|
| `dev` | a person's machine, or a daemon on it (`pmac`, `lprod-autofix`) | **yes**, a human |
| `app` | code we deployed (`promopilot`, `redbut`) | **no** |

Giving an app an owner is how "what do my developers cost" quietly starts including cron jobs. Posting
an owner for an app returns a 400, not a silent drop.

## Identity is a path: `<consumer>[:<job>]`

`promopilot:generatetext` is consumer `promopilot`, job `generatetext`. The split is on the **first**
colon only, so a job may contain colons and a consumer never can.

**Only the consumer is registered. Jobs are free.** A new workload needs no config change. Pins and
rules resolve exact-path first, then the consumer, so pinning `promopilot` covers every job under it
while one greedy job can still be split out.

## Where the router looks for your identity

In order:

1. A valid API key (`Authorization: Bearer sk-llm-…` or `x-api-key`). **This outranks anything you say
   about yourself.** Only the *job* half of `X-Project` (or an `X-Job` header) is still taken on trust.
2. `X-Project` header. Also accepts `X-Consumer`, `X-Project-Id`.
3. A top-level `"project"` field in the JSON body.
4. `"metadata": {"project": "…"}`.
5. The OpenAI `"user"` field.

An inference call with no identity is rejected:

```json
HTTP 400
{"error":{"message":"missing project attribution: send an 'X-Project' header (or a 'project' body field) identifying the calling app.",
          "type":"invalid_request_error","code":"project_required"}}
```

This applies to `/v1/chat/completions`, `/v1/messages`, `/v1/completions`, `/v1/responses`. It does
not apply to `/v1/images/generations`, `/v1/embeddings`, `/v1/rerank`, `/v1/audio/*`, or any GET.

## API keys

Wire format `sk-llm-<id>-<secret>`. The `id` is public (an 8-char handle, so lookup is a map hit
rather than a scan over every hash); the `secret` is never stored, only its sha256.

The consumer name is deliberately **not** in the key: it would leak who we are, and a name containing
a dash would make the key unparseable.

**Issuing a key IS registering.** One call creates the consumer if absent and returns the only copy of
the secret:

```bash
POST /admin/api/consumers/keys   {"name":"my-app","kind":"app"}
→ {"consumer":"my-app","keyId":"1a2b3c4d","key":"sk-llm-1a2b3c4d-…",
   "warning":"this is the only time the key is shown — store it in keyvault now"}
```

Revoke with `POST /admin/api/consumers/keys/revoke {name, id}`. The consumer, its pins and its history
survive; only that credential dies.

> `lastUsed` on a key is flushed on a five-minute timer, not per request. It is approximate. Never
> treat it as an audit trail.

## Two gates

- **`auth.mode`** — `off` | `optional` | `required`. The lock. `optional` is migration mode: a valid
  key wins, no key falls back to the header, and a key that is *presented and bad* is always a 401.
  Only `required` closes the hole. **Currently `optional`.**
- **`requireRegisteredConsumer`** — a spelling check, not a lock. Applies only to calls with no key,
  and refuses an unknown consumer with `403 unknown_consumer` so a typo cannot become a new consumer
  with its own bill. **Currently on.**

## Get pinned (only for `claude*` models)

`local` and `crazyrouter` work the moment you send an identity. `claudecode` does not: your project
must first be pinned to a Claude Max account, or every call is refused.

```json
HTTP 403
{"error":{"type":"no_account_for_project",
          "message":"project \"my-app\" is not pinned to a Claude Code account",
          "pinned_projects":[ … ]}}
```

One project maps to exactly one account, forever. Rotating accounts would blow the per-org prompt
cache (roughly 12× the cost) and make "who spent this?" unanswerable after the fact. The router never
guesses whose subscription to bill.

## Quota

A project can carry a rolling-window limit (tokens or calls). Usage is summed live from the call log.
Every response tells you where you stand:

| Header | Meaning |
|---|---|
| `x-usage-percent` | How far through the window's cap you are, 0–100+ |
| `x-usage-window`, `x-usage-limit` | The window (`24h`) and the cap |
| `x-usage-warning` | Past the warn threshold (default 80%). Nothing is slowed yet. |
| `x-usage-throttled-ms` | Past the slow threshold (default 95%). Your request was deliberately delayed by this many ms. |

Past the hard cap: `429 usage_limit_exceeded` with `retry-after: 60`. Limits ship off (cap `0` =
unlimited).
