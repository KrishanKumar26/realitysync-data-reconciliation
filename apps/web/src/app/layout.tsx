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
      <head>
        {/* Runs before paint. Without it the page renders in the system theme
            and then snaps to the stored one once React mounts — a white flash
            on every load for anyone who chose dark. Deliberately tiny and
            deliberately inline: an external file would be a second round trip
            in front of first paint. Wrapped in try/catch because a browser
            that refuses localStorage must not take the page down with it. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem("realitysync-theme");if(t==="dark"||t==="light"){document.documentElement.setAttribute("data-theme",t)}}catch(e){}`,
          }}
        />
      </head>
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
