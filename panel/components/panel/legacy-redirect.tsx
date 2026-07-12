"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Old slugs live on as redirects (bookmarks + muscle memory predate the consolidation). Mirrors
// admin/ui/core.js SLUG_ALIAS: each maps to [new slug, tab within it].
export function LegacyRedirect({ slug, tab }: { slug: string; tab: string }) {
  const router = useRouter();
  useEffect(() => {
    router.replace(`/${slug}/?t=${tab}`);
  }, [router, slug, tab]);
  return <div className="p-10 text-center text-muted-foreground">…</div>;
}
