"use client";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

// A small segmented toggle (metric/window switches inside a card header). shadcn Tabs without
// content — controlled. Port of the old inline <Tabs> used for non-page toggles.
export function Seg({
  value,
  onChange,
  items,
}: {
  value: string;
  onChange: (v: string) => void;
  items: [string, string][];
}) {
  return (
    <Tabs value={value} onValueChange={onChange}>
      <TabsList className="h-8">
        {items.map(([v, l]) => (
          <TabsTrigger key={v} value={v} className="text-xs">
            {l}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
