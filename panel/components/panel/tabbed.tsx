"use client";
import * as React from "react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useTab } from "@/lib/use-tab";

// A page made of former pages: one tab strip, then the chosen sub-page (which keeps its own
// PageHead). The active tab is mirrored to ?t=. Port of admin/ui/app.js `Tabbed`.
export function Tabbed({
  def,
  items,
}: {
  def: string;
  items: [string, string, React.ComponentType][];
}) {
  const [tab, setTab] = useTab(def);
  const active = items.find(([v]) => v === tab) ? tab : items[0][0];
  return (
    <Tabs value={active} onValueChange={setTab}>
      <TabsList className="mb-4">
        {items.map(([v, l]) => (
          <TabsTrigger key={v} value={v}>
            {l}
          </TabsTrigger>
        ))}
      </TabsList>
      {items.map(([v, , C]) => (
        <TabsContent key={v} value={v}>
          <C />
        </TabsContent>
      ))}
    </Tabs>
  );
}
