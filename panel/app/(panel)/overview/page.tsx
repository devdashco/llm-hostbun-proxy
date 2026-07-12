"use client";
import { Tabbed } from "@/components/panel/tabbed";
import { Overview } from "@/components/panel/pages/overview";
import { Stats } from "@/components/panel/pages/stats";

export default function OverviewPage() {
  return (
    <Tabbed
      def="health"
      items={[
        ["health", "Health", Overview],
        ["usage", "Usage", Stats],
      ]}
    />
  );
}
