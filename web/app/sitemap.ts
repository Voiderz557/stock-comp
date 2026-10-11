import type { MetadataRoute } from "next";

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  const base = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:4173";
  return ["", "/privacy/", "/cookies/", "/terms/"].map((path) => ({ url: `${base}${path}`, changeFrequency: "monthly" }));
}
