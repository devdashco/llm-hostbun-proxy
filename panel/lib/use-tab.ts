"use client";
import { useState, useCallback } from "react";

// Tab-within-a-page state, mirrored to ?t= so a tab survives reload and is linkable. Read on mount
// only (via window.location, so no useSearchParams Suspense boundary is needed) — a faithful port of
// admin/ui/core.js `useTab`.
export function useTab(def: string): [string, (v: string) => void] {
  const [tab, setTab] = useState<string>(() => {
    if (typeof window === "undefined") return def;
    try {
      return new URL(location.href).searchParams.get("t") || def;
    } catch {
      return def;
    }
  });
  const set = useCallback((v: string) => {
    try {
      const u = new URL(location.href);
      u.searchParams.set("t", v);
      history.replaceState({}, "", u);
    } catch {
      /* ignore */
    }
    setTab(v);
  }, []);
  return [tab, set];
}
