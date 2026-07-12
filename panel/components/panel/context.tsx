"use client";
import { createContext, useContext } from "react";

// App context — the port of admin/ui/app.js's Ctx. Every page reads `state` (from /api/state) and
// can trigger a reload, open the call drawer, or drill into the call log with a filter.
export interface PanelCtx {
  state: any;
  reload: (s?: any) => void | Promise<void>;
  openCall: (id: number | null) => void;
  gotoCalls: (params: Record<string, string | undefined>) => void;
  go: (slug: string, tab?: string) => void;
}

export const Ctx = createContext<PanelCtx | null>(null);

export function useApp(): PanelCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useApp() used outside the panel provider");
  return c;
}
