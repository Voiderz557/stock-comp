import http from "node:http";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";

const worker = (await import(pathToFileURL(path.resolve("dist/server/index.js")))).default;
const env = {};
// Load only public account connection values for localhost verification.
const configPath = path.resolve("../../web/.env.local");
for (const line of readFileSync(configPath, "utf8").split(/\r?\n/)) {
  const match = line.match(/^(NEXT_PUBLIC_SUPABASE_URL|NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY)=(.*)$/);
  if (match) env[match[1].replace("NEXT_PUBLIC_", "STOCK_COMP_")] = match[2].trim().replace(/^['"]|['"]$/g, "");
}
const server = http.createServer(async (incoming, outgoing) => {
  try {
    const body = []; for await (const chunk of incoming) body.push(chunk);
    const request = new Request(`http://127.0.0.1:4175${incoming.url}`, { method: incoming.method, headers: incoming.headers, ...(body.length ? { body: Buffer.concat(body) } : {}) });
    const response = await worker.fetch(request, env);
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch { outgoing.writeHead(500); outgoing.end("Preview failed"); }
});
server.listen(4175, "127.0.0.1", () => console.log("Hosted application preview: http://127.0.0.1:4175"));
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.close(() => process.exit(0)));
