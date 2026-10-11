import { createApi, ApiError } from "./api.mjs";
import strategyNames from "./strategies.json" with { type: "json" };

const api = createApi({ strategyNames });
function secured(response) {
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  return response;
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      try {
        const result = await api(request, env);
        return secured(Response.json(result, { headers: { "Cache-Control": "no-store" } }));
      } catch (error) {
        return secured(Response.json({ detail: error instanceof ApiError ? error.message : "The application could not complete the request." }, { status: error instanceof ApiError ? error.status : 500, headers: { "Cache-Control": "no-store" } }));
      }
    }
    if (!["GET", "HEAD"].includes(request.method)) return secured(new Response("Method not allowed", { status: 405 }));
    let pathname;
    try { pathname = decodeURIComponent(url.pathname); } catch { return new Response("Bad path", { status: 400 }); }
    const asset = assets[pathname] ?? assets[`${pathname.replace(/\/$/, "")}/index.html`];
    if (!asset) return secured(new Response("Page not found", { status: 404 }));
    const bytes = Uint8Array.from(atob(asset.body), char => char.charCodeAt(0));
    return secured(new Response(request.method === "HEAD" ? null : bytes, { headers: { "Content-Type": asset.type, "Cache-Control": pathname.startsWith("/_next/static/") ? "private, max-age=31536000, immutable" : "no-store" } }));
  },
};
