// The panel's one API client — a faithful port of admin/ui/core.js `api()`. Same-origin `/api/*`,
// JSON, and a 401 drops the shell back to <Login/> via the registered onUnauth callback. The router
// (server.js) serves this static export AND the /api/* control plane on the same origin, so the
// cookie (`hb_admin`, HttpOnly) rides along automatically — nothing here touches it.

let onUnauth: () => void = () => {};
export function setOnUnauth(fn: () => void) {
  onUnauth = fn;
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, opts?: RequestInit): Promise<T> {
  const r = await fetch("/api/" + path, {
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    ...(opts || {}),
  });
  if (r.status === 401) {
    onUnauth();
    throw new ApiError("unauthorized", 401);
  }
  const t = await r.text();
  let j: any = null;
  try {
    j = JSON.parse(t);
  } catch {
    /* non-JSON body — leave j null */
  }
  if (!r.ok) throw new ApiError((j && j.error) || "HTTP " + r.status, r.status);
  return j as T;
}
