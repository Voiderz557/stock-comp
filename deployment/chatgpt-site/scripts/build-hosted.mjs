import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { canonicalSiteOrigin } from "./site-origin.mjs";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
// Set NEXT_PUBLIC_SITE_URL in the build environment for a custom canonical origin.
const siteOrigin = canonicalSiteOrigin(process.env.NEXT_PUBLIC_SITE_URL);
const env = { ...process.env, NEXT_PUBLIC_API_BASE_URL: "/api", NEXT_PUBLIC_DEMO_MODE: "false", NEXT_PUBLIC_HOSTED_RUNTIME_CONFIG: "true", NEXT_PUBLIC_SITE_URL: siteOrigin, NEXT_PUBLIC_SUPABASE_URL: "", NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "", NEXT_TELEMETRY_DISABLED: "1" };
// Hosted configuration comes from runtime env. Local secret files are excluded
// from source synchronization and not needed for a reproducible Site build.
const next = spawnSync(process.execPath, [path.join(root, "node_modules/next/dist/bin/next"), "build", "--webpack"], { cwd: root, env, stdio: "inherit" });
if (next.status !== 0) process.exit(next.status ?? 1);
const mime = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".xml": "application/xml", ".woff2": "font/woff2" };
const assets = {};
function collect(directory, prefix = "") {
  for (const name of readdirSync(directory).sort()) {
    const full = path.join(directory, name); const relative = `${prefix}/${name}`;
    if (statSync(full).isDirectory()) collect(full, relative);
    else if (name !== "_headers" && !name.endsWith(".map")) assets[relative] = { type: mime[path.extname(name)] || "application/octet-stream", body: readFileSync(full).toString("base64") };
  }
}
collect(path.join(root, "out"));
if (!assets["/index.html"] || !assets["/auth/callback/index.html"]) throw new Error("Required website routes were not built.");
const api = readFileSync(path.join(root, "worker/api.mjs"), "utf8").replace(/^export /gm, "");
const worker = readFileSync(path.join(root, "worker/index.mjs"), "utf8").replace(/^import .*;\r?\n/gm, "");
const strategyNames = JSON.parse(readFileSync(path.join(root, "worker/strategies.json"), "utf8"));
const source = `const assets = ${JSON.stringify(assets)};\nconst strategyNames = ${JSON.stringify(strategyNames)};\n${api}\n${worker}`;
mkdirSync(path.join(root, "dist/server"), { recursive: true });
mkdirSync(path.join(root, "dist/.openai"), { recursive: true });
writeFileSync(path.join(root, "dist/server/index.js"), source);
writeFileSync(path.join(root, "dist/.openai/hosting.json"), readFileSync(path.join(root, ".openai/hosting.json")));
console.log(`Built ${Object.keys(assets).length} public assets and hosted account gateway (${source.length} bytes).`);
