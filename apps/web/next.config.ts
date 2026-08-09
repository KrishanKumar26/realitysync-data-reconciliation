import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Emits a minimal server bundle for container deployment.
  output: "standalone",
  // Pin the tracing root to this app. Without it, Next walks up looking for a
  // lockfile and can pick a directory outside the project.
  outputFileTracingRoot: path.join(import.meta.dirname, "."),
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
