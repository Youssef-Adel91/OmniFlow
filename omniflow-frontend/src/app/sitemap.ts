import type { MetadataRoute } from "next";
import { absoluteUrl, LOCALES, PUBLIC_PATHS } from "@/lib/public/site";

export default function sitemap(): MetadataRoute.Sitemap {
  return LOCALES.flatMap((l) => PUBLIC_PATHS.map((p) => ({
    url: absoluteUrl(`/${l}${p}`),
    changeFrequency: p === "" || p === "/pricing" ? ("weekly" as const) : ("monthly" as const),
    priority: p === "" ? 1 : p === "/pricing" ? 0.8 : 0.4,
    alternates: { languages: { ar: absoluteUrl(`/ar${p}`), en: absoluteUrl(`/en${p}`) } },
  })));
}
