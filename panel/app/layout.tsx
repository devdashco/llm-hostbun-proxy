import type { Metadata } from "next";
import "./globals.css";

// No next/font — the panel makes ZERO external requests. Fonts are the system stack defined in
// globals.css (--font-sans / --font-mono), matching the old admin panel. `dark` is forced on
// <html>: this control surface is dark-only.
export const metadata: Metadata = {
  title: "hostbun · llm router",
  description: "Control panel for the llm.hostbun.cc router.",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark h-full antialiased">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
