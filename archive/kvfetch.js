// Fetch ONE keyvault value and print it to stdout. Stdlib only. Mirrors kvclient.py's transport:
// JSON-RPC over streamable-HTTP (initialize → notifications/initialized → tools/call kv_get),
// tolerating both plain-JSON and text/event-stream responses. Exits non-zero (silently) on any
// failure so the caller can fall back to another config source.
//
//   node archive/kvfetch.js llm-archive/config
//
// Env: KEYVAULT_URL (default https://keyvault.hostbun.cc/mcp), KEYVAULT_BEARER (default ddash).
const https = require("node:https");
const http = require("node:http");
const { URL } = require("node:url");

const KV_URL = process.env.KEYVAULT_URL || "https://keyvault.hostbun.cc/mcp";
const BEARER = process.env.KEYVAULT_BEARER || "ddash";
const KEY = process.argv[2];
if (!KEY) { process.exit(2); }

const u = new URL(KV_URL);
const agent = u.protocol === "http:" ? http : https;
let session = "";
let idc = 0;

function post(payload, wantResponse = true) {
  const body = Buffer.from(JSON.stringify(payload));
  const headers = {
    authorization: `Bearer ${BEARER}`,
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
    "content-length": String(body.length),
  };
  if (session) headers["mcp-session-id"] = session;
  return new Promise((resolve, reject) => {
    const req = agent.request(
      { method: "POST", host: u.hostname, port: u.port || (u.protocol === "http:" ? 80 : 443), path: u.pathname + u.search, headers },
      (res) => {
        const sid = res.headers["mcp-session-id"]; if (sid) session = sid;
        const chunks = []; res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          if (!wantResponse) return resolve(null);
          const raw = Buffer.concat(chunks).toString();
          const ctype = (res.headers["content-type"] || "").toLowerCase();
          if (ctype.includes("text/event-stream")) {
            // last `data:` line carries the JSON-RPC result
            let out = null;
            for (const line of raw.split("\n")) { const s = line.trim(); if (s.startsWith("data:")) { try { out = JSON.parse(s.slice(5).trim()); } catch {} } }
            return resolve(out);
          }
          try { resolve(JSON.parse(raw)); } catch (e) { reject(new Error("bad json")); }
        });
      });
    req.on("error", reject);
    req.write(body); req.end();
  });
}

(async () => {
  await post({ jsonrpc: "2.0", id: ++idc, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "kvfetch", version: "1" } } });
  await post({ jsonrpc: "2.0", method: "notifications/initialized" }, false);
  const r = await post({ jsonrpc: "2.0", id: ++idc, method: "tools/call", params: { name: "kv_get", arguments: { key: KEY } } });
  // tool result content: [{type:'text', text:'<json>'}]
  const c = r && r.result && r.result.content && r.result.content[0] && r.result.content[0].text;
  if (!c) process.exit(1);
  let val = c;
  try { const j = JSON.parse(c); val = (j && typeof j === "object" && "value" in j) ? j.value : c; } catch {}
  process.stdout.write(typeof val === "string" ? val : JSON.stringify(val));
})().catch(() => process.exit(1));
