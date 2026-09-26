import createNextIntlPlugin from "next-intl/plugin";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

/** @type {import('next').NextConfig} */
const nextConfig = {
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
