import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createApi, ApiError, publicConfig, visibleSnapshot } from "../worker/api.mjs";

const userId = "11111111-1111-4111-8111-111111111111";
const accountId = "22222222-2222-4222-8222-222222222222";
const planId = "33333333-3333-4333-8333-333333333333";
const actionId = "44444444-4444-4444-8444-444444444444";
const env = { STOCK_COMP_SUPABASE_URL: "https://example.supabase.co", STOCK_COMP_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test" };
const strategies = ["All strategies — consensus", "Momentum V2"];
function snapshot(status = "CURRENT") {
  return { account: { portfolio_version: 1 }, portfolio: { cash: 95000, value: null }, positions: [{ ticker: "MSFT", price: null }], recommendations: [{ ticker: "AAPL" }], plan: { status }, regime: { name: "SIDEWAYS" } };
}
function request(path, method = "GET", body) {
  return new Request(`https://app.example/api/v1/${path}`, { method, headers: { Authorization: "Bearer user-token", "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) });
}
function harness(handler = () => snapshot(), options = {}) {
  const calls = [];
  const api = createApi({ strategyNames: strategies, fetcher: async (target, config) => {
    calls.push({ url: String(target), ...config });
    if (String(target).endsWith("/auth/v1/user")) return Response.json({ id: userId });
    const value = await handler(String(target), config);
    return value instanceof Response ? value : Response.json(value);
  }, ...options });
  return { api, calls };
}
test("native fetch retains its global receiver in Worker runtimes", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = function (target) {
    assert.equal(this, globalThis);
    calls += 1;
    return Promise.resolve(Response.json(String(target).endsWith("/auth/v1/user") ? { id: userId } : snapshot()));
  };
  try {
    const api = createApi({ strategyNames: strategies });
    assert.equal((await api(request("snapshot"), env)).portfolio.cash, 95000);
    assert.equal(calls, 2);
  } finally { globalThis.fetch = original; }
});
test("public config never exposes a server key, localhost compute, or fake demo", () => {
  assert.equal(publicConfig({ ...env, STOCK_COMP_SUPABASE_PUBLISHABLE_KEY: "sb_secret_no" }).storage_configured, false);
  assert.equal(publicConfig({ ...env, STOCK_COMP_PYTHON_API_URL: "http://127.0.0.1:8000" }).compute_configured, false);
  assert.equal(publicConfig(env).demo_mode, false);
  assert.equal(publicConfig(env).storage_configured, true);
  assert.equal(publicConfig({ ...env, STOCK_COMP_PYTHON_API_URL: "https://engine.onrender.com" }).compute_configured, true);
});
test("missing auth and storage fail before any account request", async () => {
  const { api, calls } = harness();
  await assert.rejects(api(new Request("https://app.example/api/v1/snapshot"), env), e => e.status === 401);
  await assert.rejects(api(request("snapshot"), {}), e => e.status === 503);
  assert.equal(calls.length, 0);
});
test("invalid Supabase user identity cannot access account rows", async () => {
  const { api, calls } = harness(() => null, { fetcher: async () => Response.json({ id: "bad" }) });
  await assert.rejects(api(request("snapshot"), env), e => e.status === 401);
  assert.equal(calls.length, 0);
});
test("snapshot uses caller token and preserves null prices and valuations", async () => {
  const { api, calls } = harness();
  const value = await api(request("snapshot"), env);
  assert.equal(value.portfolio.value, null);
  assert.equal(value.positions[0].price, null);
  assert.equal(calls[1].headers.Authorization, "Bearer user-token");
  assert.equal(calls[1].headers.apikey, env.STOCK_COMP_SUPABASE_PUBLISHABLE_KEY);
  assert.equal(JSON.parse(calls[1].body).p_account_id, null);
});
test("unavailable account gives onboarding 404, never demonstration data", async () => {
  const { api } = harness(() => null);
  await assert.rejects(api(request("snapshot"), env), e => e.status === 404);
});
test("stale portfolio suppresses current recommendations without changing saved source", () => {
  const original = snapshot("STALE");
  const value = visibleSnapshot(original);
  assert.deepEqual(value.recommendations, []);
  assert.equal(value.regime.name, "UNAVAILABLE");
  assert.equal(original.recommendations.length, 1);
});
test("account creation ignores caller ownership and reloads under caller RLS", async () => {
  const { api, calls } = harness(url => url.endsWith("create_or_get_competition_account") ? accountId : snapshot());
  await api(request("accounts", "POST", { name: "My account", starting_capital: 100000, cash: 100000, snapshot_as_of: "2026-10-10T12:00:00Z", user_id: "other" }), env);
  assert.deepEqual(Object.keys(JSON.parse(calls[1].body)).sort(), ["p_cash", "p_name", "p_snapshot_as_of", "p_starting_capital"]);
  assert.equal(JSON.parse(calls[2].body).p_account_id, accountId);
});
test("duplicate holdings rejected before snapshot replacement", async () => {
  const { api, calls } = harness();
  const position = { ticker: "MSFT", side: "LONG", quantity: 1, average_price: 100 };
  await assert.rejects(api(request("accounts/current", "PUT", { name: "My account", starting_capital: 100000, cash: 100000, snapshot_as_of: "2026-10-10T12:00:00Z", expected_portfolio_version: 1, positions: [position, position] }), env), e => e.status === 422);
  assert.equal(calls.length, 1);
});
test("cash, quantity, cost basis and version are validated before saving", async () => {
  const { api, calls } = harness();
  await assert.rejects(api(request("accounts/current", "PUT", { name: "My account", starting_capital: 100000, cash: -1, snapshot_as_of: "2026-10-10T12:00:00Z", expected_portfolio_version: 1, positions: [] }), env), e => e.status === 422);
  assert.equal(calls.length, 1);
});
test("paper actions preserve exact retry key and use checked database writer", async () => {
  const { api, calls } = harness(url => url.endsWith("record_recommendation_paper_action") ? actionId : snapshot());
  const result = await api(request("paper-actions", "POST", { recommendation_id: planId, quantity: 2, expected_portfolio_version: 1, idempotency_key: " key with  spaces " }), env);
  assert.equal(result.action_id, actionId);
  assert.equal(JSON.parse(calls[1].body).p_idempotency_key, " key with  spaces ");
  assert.equal(result.snapshot.portfolio.value, null);
});
test("too-short action retry identifiers and fractional paper shares fail", async () => {
  const { api, calls } = harness();
  for (const body of [{ quantity: 1, idempotency_key: "tiny" }, { quantity: 1.5, idempotency_key: "valid-key" }]) await assert.rejects(api(request("paper-actions", "POST", { recommendation_id: planId, expected_portfolio_version: 1, ...body }), env), e => e.status === 422);
  assert.equal(calls.length, 2);
});
test("database version conflict remains a conflict", async () => {
  const { api } = harness(() => Response.json({ code: "40001" }, { status: 400 }));
  await assert.rejects(api(request("snapshot"), env), e => e.status === 409);
});
test("missing compute blocks plan generation without saving or forging signals", async () => {
  const { api, calls } = harness();
  await assert.rejects(api(request("plans/current", "POST", { strategy: "Momentum V2" }), env), e => e.status === 503);
  assert.equal(calls.length, 1);
  assert.equal(calls.some(call => call.url.includes("save_generated")), false);
});
test("only fixed compute routes forward to HTTPS host and omit Site cookies", async () => {
  const { api, calls } = harness(() => ({ generated: true }));
  const result = await api(request("plans/current", "POST", { strategy: "Momentum V2" }), { ...env, STOCK_COMP_PYTHON_API_URL: "https://engine.onrender.com" });
  assert.equal(result.generated, true);
  assert.equal(calls[1].url, "https://engine.onrender.com/v1/plans/current");
  assert.deepEqual(Object.keys(calls[1].headers).sort(), ["Authorization", "Content-Type"]);
  assert.equal(calls[1].redirect, "manual");
});
test("hosted fetch works when runtime rejects redirect error mode", async () => {
  const api = createApi({ fetcher: async (target, options) => {
    if (options.redirect === "error") throw new TypeError("Unsupported redirect mode");
    assert.equal(options.redirect, "manual");
    return Response.json(String(target).endsWith("/auth/v1/user") ? { id: userId } : snapshot());
  } });
  assert.equal((await api(request("snapshot"), env)).portfolio.cash, 95000);
});
test("auth, storage and compute redirects are rejected without following credentials", async () => {
  for (const stage of ["auth", "storage", "compute"]) {
    let calls = 0;
    const api = createApi({ fetcher: async (target, options) => {
      calls++;
      assert.equal(options.redirect, "manual");
      const auth = String(target).endsWith("/auth/v1/user");
      return stage === "auth" || !auth
        ? new Response(null, { status: 302, headers: { Location: "https://untrusted.example" } })
        : Response.json({ id: userId });
    } });
    await assert.rejects(api(request(stage === "compute" ? "plans/current" : "snapshot", stage === "compute" ? "POST" : "GET", stage === "compute" ? {} : undefined), { ...env, STOCK_COMP_PYTHON_API_URL: "https://engine.onrender.com" }), e => e.status === 502 && e.message.includes("redirect"));
    assert.equal(calls, stage === "auth" ? 1 : 2);
  }
});
test("arbitrary paths never proxy or expose trusted plan writer", async () => {
  const { api, calls } = harness();
  await assert.rejects(api(request("rest/v1/rpc/save_generated_trading_plan", "POST", {}), env), e => e.status === 404);
  assert.equal(calls.length, 1);
});
test("saved settings compare timestamp and bind ownership to verified user", async () => {
  const { api, calls } = harness(() => [{ id: planId }]);
  const body = { name: "App settings", mode: "COMPETITION", rules: { strategy: "Momentum V2", app_settings: true } };
  await api(request("planning-profiles", "POST", { ...body, user_id: "forged" }), env);
  assert.equal(JSON.parse(calls[1].body).user_id, userId);
  await api(request(`planning-profiles/${planId}`, "PUT", { ...body, expected_updated_at: "2026-10-10T12:00:00Z" }), env);
  assert.equal(new URL(calls[3].url).searchParams.get("updated_at"), "eq.2026-10-10T12:00:00Z");
});
test("export contains full cloud table coverage and excludes user identifiers", async () => {
  const { api } = harness(() => [{ id: "row", user_id: userId }]);
  const result = await api(request("account-export"), env);
  assert.equal(Object.keys(result.tables).length, 11);
  assert.equal(result.counts.planning_profiles, 1);
  assert.equal("user_id" in result.tables.positions[0], false);
});
test("upstream failures, redirects and invalid JSON never turn into successes", async () => {
  const { api } = harness(() => new Response("secret-error-data", { status: 500 }));
  await assert.rejects(api(request("snapshot"), env), e => e instanceof ApiError && e.status === 502 && !e.message.includes("secret-error-data"));
});

test("registry names preserve the actual Unicode planning modes", () => {
  const names = JSON.parse(readFileSync(new URL("../worker/strategies.json", import.meta.url), "utf8"));
  assert.ok(names.includes("All strategies — consensus"));
  assert.ok(names.includes("Automatic — market regime"));
  assert.ok(names.every(name => !name.includes("\ufffd")));
});
test("non-JSON errors retain auth and host-unavailable status", async () => {
  const { api } = harness(() => new Response("private upstream content", { status: 401 }));
  await assert.rejects(api(request("snapshot"), env), e => e.status === 401 && !e.message.includes("private upstream"));
  const compute = harness(() => new Response("host starting", { status: 503 }));
  await assert.rejects(compute.api(request("plans/current", "POST", {}), { ...env, STOCK_COMP_PYTHON_API_URL: "https://engine.onrender.com" }), e => e.status === 503);
});
test("compute response-body timeout gives a bounded timeout with retry guidance", async () => {
  const { api } = harness(() => new Response(new ReadableStream({ start(controller) { controller.error(new DOMException("private details", "AbortError")); } })));
  await assert.rejects(api(request("plans/current", "POST", {}), { ...env, STOCK_COMP_PYTHON_API_URL: "https://engine.onrender.com" }), e => e.status === 504 && e.message.includes("refresh") && !e.message.includes("private details"));
});
test("export rejects cumulative size before returning a partial archive", async () => {
  let pages = 0;
  const { api } = harness(() => { pages++; return [{ id: "row", payload: "x".repeat(1_000_000) }]; });
  await assert.rejects(api(request("account-export"), env), e => e.status === 413 && e.message.includes("No partial"));
  assert.ok(pages < 11);
});
