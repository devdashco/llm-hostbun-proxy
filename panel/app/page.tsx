"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

// The panel's canonical entry is /overview. Root `/` is served by the router too (it's in
// UI_ROUTES), so redirect there on the client after the static shell loads.
export default function Root() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/overview/");
  }, [router]);
  return <div className="p-10 text-center text-muted-foreground">…</div>;
}
