import type { Metadata, Viewport } from "next";

import { AppShell } from "@/components/shell/app-shell";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: {
    default: "RealitySync",
    template: "%s · RealitySync",
  },
  description:
    "RealitySync reconciles observations from multiple data sources into a continuously verified reality state.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fcfcfd" },
    { media: "(prefers-color-scheme: dark)", color: "#0c0d10" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
