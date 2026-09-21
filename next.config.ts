import type { NextConfig } from "next";

// Content-Security-Policy is set per-request in proxy.ts instead (it
// needs a fresh nonce per request for Next.js's inline hydration scripts).
const SECURITY_HEADERS = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  // Baked into the client bundle at build time. Backs the bulk-audit
  // URL-limit inputs in app/technical-audit/page.tsx - see
  // modules/_http.py::bulk_url_cap for the matching backend-side cap.
  // Same value everywhere (production, preview, local) since both caps are
  // 5000; kept as an explicit env var rather than a bare literal so a future
  // cost-driven rollback only needs to change modules/_http.py's
  // BULK_URL_CAP_PROD and this one line, not hunt through the frontend.
  env: {
    NEXT_PUBLIC_BULK_URL_LIMIT: "5000",
  },
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};

export default nextConfig;
