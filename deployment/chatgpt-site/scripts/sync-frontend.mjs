// Copy only public source. Never copy .env, local databases, caches or builds.
import { copyFileSync, cpSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { spawnSync } from "node:child_process";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const project = path.resolve(root, "../..");
const frontend = path.join(project, "web");
for (const folder of ["app", "components", "lib", "public"]) cpSync(path.join(frontend, folder), path.join(root, folder), { recursive: true });
for (const filename of ["tsconfig.json", "next-env.d.ts", "next.config.ts", "package-lock.json", "AGENTS.md"]) copyFileSync(path.join(frontend, filename), path.join(root, filename));
const pkg = JSON.parse(readFileSync(path.join(frontend, "package.json"), "utf8"));
pkg.type = "module";
pkg.scripts = { build: "node scripts/build-hosted.mjs", test: "node --test tests/hosted-api.test.mjs", preview: "node scripts/preview-hosted.mjs" };
writeFileSync(path.join(root, "package.json"), JSON.stringify(pkg, null, 2) + "\n");
mkdirSync(path.join(root, "worker"), { recursive: true });
const executable = process.env.STOCK_COMP_PYTHON_EXECUTABLE || (process.platform === "win32" ? path.join(project, ".venv/Scripts/python.exe") : "python");
const result = spawnSync(executable, ["-c", "import json; from paper_trading.trading_plan import plan_mode_options; print(json.dumps(plan_mode_options(), ensure_ascii=True))"], { cwd: project, encoding: "utf8" });
if (result.status !== 0) throw new Error("Could not read strategy names from the existing Python registry.");
const strategies = JSON.parse(result.stdout);
writeFileSync(path.join(root, "worker/strategies.json"), JSON.stringify(strategies, null, 2) + "\n");
console.log("Copied public frontend source and current Python strategy names. No account data or environment files were copied.");
