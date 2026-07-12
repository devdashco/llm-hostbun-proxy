"use client";
import { Tabbed } from "@/components/panel/tabbed";
import { Crazyrouter, Secrets } from "@/components/panel/pages/settings";

export default function SettingsPage() {
  return (
    <Tabbed
      def="crazyrouter"
      items={[
        ["crazyrouter", "Crazyrouter", Crazyrouter],
        ["secrets", "Secrets & gate", Secrets],
      ]}
    />
  );
}
