import type { MetadataRoute } from "next";
import { absoluteUrl, LOCALES, PRIVATE_PREFIXES } from "@/lib/public/site";

export default function robots(): MetadataRoute.Robots {
  const disallow = ["/api/", ...LOCALES.flatMap((l) => PRIVATE_PREFIXES.map((p) => `/${l}/${p}`))];
  const sitemap = absoluteUrl("/sitemap.xml");
  return { rules: [{ userAgent: "*", allow: "/", disallow }], ...(sitemap.startsWith("http") ? { sitemap } : {}) };
}
