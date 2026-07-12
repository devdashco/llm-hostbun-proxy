"use client";
import { Tabbed } from "@/components/panel/tabbed";
import { Consumers } from "@/components/panel/pages/consumers";
import { Accounts } from "@/components/panel/pages/accounts";

export default function IdentityPage() {
  return (
    <Tabbed
      def="consumers"
      items={[
        ["consumers", "Consumers", Consumers],
        ["accounts", "Accounts", Accounts],
      ]}
    />
  );
}
