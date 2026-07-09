# Endpoints

| Method | Path | Provider | Purpose |
|---|---|---|---|
| POST | `/v1/chat/completions` | all three | Chat, routed by model. Streaming, tools, structured output, vision. Needs identity. |
| POST | `/v1/messages` | `claudecode` | Anthropic-native shape, forwarded byte-for-byte. Needs identity. |
| POST | `/v1/images/generations` | image | Text-to-image on the pbox GPU. No identity required. |
| GET | `/v1/templates`, `/v1/loras` | image | Image templates and named-LoRA catalog |
| POST | `/v1/completions`, `/v1/embeddings`, `/v1/audio/*`, `/v1/rerank` | `crazyrouter` | Rest of the OpenAI-compatible surface, key injected |
| GET | `/v1/models` | merged | What each provider currently advertises. Not the priced list; see `/prices.json`. |
| POST | `/local/v1/chat/completions` | `local` | Legacy explicit local path |
| GET | `/prices.json`, `/prices` | meta | Computed crazyrouter prices, refreshed every 6h |
| GET | `/docs` | meta | These docs |
| GET | `/` | meta | Control panel, password-gated |

`/admin` 308s to `/`: the site root **is** the panel.

## Structured / JSON output, enforced

Send `response_format: {"type":"json_object"}` (or `{"type":"json_schema", …}`) and the router verifies
the model actually returned valid JSON before handing it back. It does not trust the model to honour
the flag.

- Reply parses → returned unchanged.
- Reply is wrapped in a ```` ```json ```` fence → fence stripped, no extra round trip.
- Still invalid → the router re-prompts the same model with the parse error (default 2 retries).
- Never complies → `422 json_validation_failed`, with the last raw content, instead of a malformed
  body that explodes your `JSON.parse`.

Anthropic has no `response_format` field at all. For `claudecode` the router strips it and instructs
the model in-prompt instead (including your JSON Schema), then validates the same way.

Because the whole reply must be buffered to validate it, `stream: true` plus a JSON `response_format`
returns the content in a single reconstructed SSE chunk rather than token by token. Validation is
structural JSON, not full JSON-Schema.

## Everything else passes through

The router rewrites only `model` (and `response_format`, for JSON enforcement). Every other field is
forwarded untouched: `reasoning_effort`, `thinking`, `temperature`, `top_p`, `tools`, `tool_choice`,
`stop`, `seed`, `max_tokens`. No default effort is set; omit the field and the upstream's own default
applies.

## Image generation — `model: "imagegen"`

Text-to-image on the pbox GPU (SDXL + Lightning), token injected server-side. OpenAI-compatible
`/v1/images/generations`, always base64 (`{"data":[{"b64_json": …}]}`); we host no URLs.

| Field | Meaning |
|---|---|
| `prompt` | Text prompt. Optional if `template` is given. |
| `template` | Named prompt template (`GET /v1/templates`) |
| `vars` | Pin specific template slots, e.g. `{"hair":"jet black"}`; the rest stay random |
| `lora` | A registered LoRA name (`GET /v1/loras`), an HF repo id, or a local path |
| `lora_scale` | Override LoRA strength |
| `n`, `size`, `seed`, `steps` | Count (1–8), WxH (default `1024x1024`), seed, steps (default 8) |

Sizes are floored to a multiple of 8: the SDXL VAE downsamples ×8, and a non-multiple crashes upstream
with a bare 500.

```bash
curl https://llm.hostbun.cc/v1/images/generations -H "Content-Type: application/json" \
  -d '{"model":"imagegen","lora":"watercolor","prompt":"a cozy reading nook by a window"}'
```

Asking for `imagegen` on a chat endpoint is refused with a 400 that tells you to POST it here instead.
