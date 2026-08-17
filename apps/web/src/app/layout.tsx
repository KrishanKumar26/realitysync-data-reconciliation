import type { Metadata, Viewport } from "next";

import { AuthGate } from "@/components/auth/auth-gate";
import { SessionProvider } from "@/components/auth/session-provider";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: {
    default: "RealitySync",
    template: "%s · RealitySync",
  },
  description:
    "RealitySync compares what your data sources say and shows you what is actually true.",
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
        {/* The session is resolved once here and shared by every route, so a
            navigation never re-asks "who is signed in". */}
        <SessionProvider>
          <AuthGate>{children}</AuthGate>
        </SessionProvider>
      </body>
    </html>
  );
}
