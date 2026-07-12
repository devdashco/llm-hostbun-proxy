import type { NextConfig } from "next";

// Static export: the router (server.js) serves the built `out/` as files, exactly as it serves
// admin/ today. No Next runtime — the container stays pg-only. trailingSlash makes each route
// export as `<slug>/index.html`, which the router's enumerated UI_ROUTES map onto. Every page is a
// "use client" component that fetches same-origin /api/* on mount (SPA behaviour), so nothing runs
// on a server at request time.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  // The router repo has its own package-lock.json one level up; pin the workspace root to panel/
  // so Next doesn't infer the outer repo and resolve deps from the wrong tree.
  turbopack: { root: __dirname },
};

export default nextConfig;
