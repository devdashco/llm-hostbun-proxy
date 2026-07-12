"""E2E probe: hit GET /v1/accounts on the claude host with the upstream bearer.
Verifies the wrapper is reachable AND the bearer is accepted (not just liveness),
so bad wiring => Coolify rolls back instead of shipping a broken server."""
import os
import httpx

CLAUDE_HOST = os.environ.get("CLAUDE_HOST", "https://claude.hostbun.cc").rstrip("/")
UPSTREAM_BEARER = os.environ.get("UPSTREAM_BEARER", "ddash")


def probe() -> dict:
    try:
        r = httpx.get(f"{CLAUDE_HOST}/v1/accounts",
                      headers={"Authorization": f"Bearer {UPSTREAM_BEARER}",
                               "Accept": "application/json"},
                      timeout=20.0)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{CLAUDE_HOST} unreachable: {type(e).__name__}: {e}"[:200]}
    if r.status_code != 200:
        return {"ok": False, "status": r.status_code,
                "error": f"/v1/accounts returned {r.status_code} (bearer rejected?)"}
    try:
        accts = r.json().get("accounts", [])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"bad json: {e}"[:150]}
    return {"ok": True, "n_accounts": len(accts),
            "names": [a.get("name") for a in accts]}
