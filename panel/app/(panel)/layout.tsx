"use client";
import { usePathname } from "next/navigation";
import { PanelGate } from "@/components/panel/shell";

// Legacy slugs collapse onto their canonical nav page for the active-highlight (they also redirect;
// see each legacy route). Mirrors admin/ui/core.js SLUG_ALIAS.
const ALIAS: Record<string, string> = {
  stats: "overview",
  consumers: "identity",
  accounts: "identity",
  models: "routing",
  crazyrouter: "settings",
  secrets: "settings",
};
const NAV = ["overview", "calls", "routing", "identity", "settings"];

export default function PanelLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname() || "/";
  let s = path.replace(/^\/+/, "").split("/")[0] || "overview";
  s = ALIAS[s] || s;
  const active = NAV.includes(s) ? s : "overview";
  return <PanelGate active={active}>{children}</PanelGate>;
}
