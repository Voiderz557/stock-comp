import type { MetadataRoute } from "next";

export const dynamic = "force-static";

export default function robots(): MetadataRoute.Robots {
  const base = process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:4173";
  return { rules: { userAgent: "*", allow: ["/", "/privacy/", "/cookies/", "/terms/"] }, sitemap: `${base}/sitemap.xml` };
}
