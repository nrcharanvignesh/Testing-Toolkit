import type { NextConfig } from "next";

// A stable identifier for THIS build/deployment. On Vercel the git commit SHA is
// available at build time and uniquely identifies a deployment; locally we fall
// back to a timestamp. It is baked into the client bundle as NEXT_PUBLIC_BUILD_ID
// and also returned at runtime by /api/build-id, so the running app can detect
// when a newer web build has been deployed and reload itself.
const BUILD_ID =
  process.env.VERCEL_GIT_COMMIT_SHA ||
  process.env.VERCEL_DEPLOYMENT_ID ||
  `dev-${Date.now()}`;

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_BUILD_ID: BUILD_ID,
  },
  generateBuildId: async () => BUILD_ID,
  async headers() {
    // Baseline security hardening applied to every response. These are the
    // headers that are safe regardless of hosting context. Frame-blocking
    // headers (X-Frame-Options / CSP frame-ancestors) are intentionally
    // OMITTED: the app is embedded in the v0 preview iframe and may be embedded
    // in demo shells, so blocking framing would break those surfaces. Clickjack
    // risk is low for a localhost-agent tool with no destructive same-origin
    // GET side effects; revisit with a per-tenant allowlist at deploy time.
    const securityHeaders = [
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "X-DNS-Prefetch-Control", value: "on" },
      {
        key: "Permissions-Policy",
        value: "camera=(), microphone=(), geolocation=(), browsing-topics=()",
      },
      {
        key: "Strict-Transport-Security",
        value: "max-age=63072000; includeSubDomains",
      },
    ];
    return [
      {
        // Never let the HTML shell be served stale from a CDN/browser cache, so
        // a refresh always lands on the latest deployment's entrypoint. Hashed
        // JS/CSS assets remain immutable/cacheable (Next handles those).
        source: "/",
        headers: [
          {
            key: "Cache-Control",
            value: "no-cache, no-store, must-revalidate",
          },
        ],
      },
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
