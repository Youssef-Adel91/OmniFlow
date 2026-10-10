import createNextIntlPlugin from "next-intl/plugin";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

// ── Security headers ─────────────────────────────────────────────────────────
// Enforced now (cannot break the app): clickjacking, MIME sniffing, referrer, permissions, plugins, <base>/<form> hijack.
// REPORT-ONLY for now: the full script/connect/frame allow-list. It was written from Clerk's documented origins but has NOT
// been exercised against a real Clerk session; promote it to `Content-Security-Policy` after a staging pass shows no violations.
const apiOrigin = (() => {
  try { return new URL(process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").origin; } catch { return "http://localhost:8000"; }
})();
const isProd = process.env.NODE_ENV === "production";
const clerkHosts = "https://*.clerk.accounts.dev https://*.clerk.com https://*.clerk.dev";
const fullCsp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isProd ? "" : " 'unsafe-eval'"} ${clerkHosts} https://challenges.cloudflare.com`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  `connect-src 'self' ${apiOrigin} ${clerkHosts} https://clerk-telemetry.com`,
  `frame-src ${clerkHosts} https://challenges.cloudflare.com`,
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "object-src 'none'",
  "form-action 'self'",
].join("; ");
const securityHeaders = [
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'" },
  { key: "Content-Security-Policy-Report-Only", value: fullCsp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  ...(isProd ? [{ key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" }] : []),
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  output: "standalone",
  async redirects() {
    return [{ source: "/test", destination: "/ar/dashboard", permanent: false }];
  },
  reactStrictMode: true,
  images: {
    remotePatterns: [
      { protocol: "http", hostname: "localhost", port: "8000" },
    ],
  },
  // Fix webpack cache for paths with non-ASCII characters (Arabic folder names)
  webpack: (config, { dev }) => {
    if (dev) {
      // Disable file-system cache to avoid rename errors on Arabic-named paths
      config.cache = false;
    }
    return config;
  },
};

export default withNextIntl(nextConfig);
