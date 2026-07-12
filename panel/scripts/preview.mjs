// Dev-only local preview: serve the static export in out/ AND proxy /api/* to the live router, so
// the real /api contract (login cookie, state, everything) can be exercised against the built panel
// without deploying. Strips Secure/Domain off the upstream Set-Cookie so the hb_admin cookie is
// stored for http://localhost. NOT shipped — purely a local smoke harness.
import http from "node:http";
import { readFile, stat } from "node:fs/promises";
import { join, extname, normalize } from "node:path";

const OUT = new URL("../out/", import.meta.url).pathname;
const UPSTREAM = process.env.UPSTREAM || "https://llm.hostbun.cc";
const PORT = +(process.env.PORT || 8123);
const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".json": "application/json", ".txt": "text/plain; charset=utf-8",
  ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2", ".map": "application/json",
};

async function serveStatic(res, path) {
  let rel = decodeURIComponent(path.split("?")[0]);
  if (rel.endsWith("/")) rel += "index.html";
  let file = normalize(join(OUT, rel));
  if (!file.startsWith(OUT)) return send(res, 403, "forbidden");
  try {
    await stat(file);
  } catch {
    // trailingSlash routes: /overview -> out/overview/index.html
    try {
      const alt = join(OUT, rel, "index.html");
      await stat(alt);
      file = alt;
    } catch {
      // SPA fallback for unknown paths
      file = join(OUT, "index.html");
    }
  }
  try {
    const buf = await readFile(file);
    send(res, 200, buf, TYPES[extname(file)] || "application/octet-stream");
  } catch {
    send(res, 404, "not found");
  }
}

function send(res, code, body, type = "text/plain") {
  res.writeHead(code, { "content-type": type });
  res.end(body);
}

async function proxy(req, res) {
  try {
    await proxyInner(req, res);
  } catch (e) {
    send(res, 502, JSON.stringify({ error: "preview proxy: " + (e?.message || e) }), "application/json");
  }
}

async function proxyInner(req, res) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const body = Buffer.concat(chunks);
  const up = await fetch(UPSTREAM + req.url, {
    method: req.method,
    headers: {
      "content-type": req.headers["content-type"] || "application/json",
      cookie: req.headers.cookie || "",
      "user-agent": "panel-preview",
    },
    body: ["GET", "HEAD"].includes(req.method) ? undefined : body,
    redirect: "manual",
  });
  const text = await up.text();
  const headers = { "content-type": up.headers.get("content-type") || "application/json" };
  const setC = up.headers.getSetCookie?.() || [];
  if (setC.length)
    res.setHeader(
      "set-cookie",
      setC.map((c) => c.replace(/;\s*Secure/gi, "").replace(/;\s*Domain=[^;]+/gi, "")),
    );
  res.writeHead(up.status, headers);
  res.end(text);
}

process.on("unhandledRejection", (e) => console.error("[preview] unhandledRejection", e?.message || e));
process.on("uncaughtException", (e) => console.error("[preview] uncaughtException", e?.message || e));

http
  .createServer((req, res) => {
    if (req.url.startsWith("/api/")) return proxy(req, res);
    return serveStatic(res, req.url).catch(() => send(res, 500, "static error"));
  })
  .listen(PORT, () => console.log(`panel preview → http://localhost:${PORT}  (api → ${UPSTREAM})`));
