import assert from "node:assert/strict";
import test from "node:test";
import { createApi } from "../worker/api.mjs";

const id = "11111111-1111-4111-8111-111111111111";
const env = { STOCK_COMP_SUPABASE_URL: "https://storage.example.com",
  STOCK_COMP_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  STOCK_COMP_PYTHON_API_URL: "https://compute.example.com" };
function request(path, body, token = "verified-test-session") {
  return new Request(`https://site.example.com/api/v1/${path}`, {
    method: body ? "POST" : "GET", headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}),
      "Content-Type": "application/json", Cookie: "private-site-cookie" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
}
test("job start and UUID status routes forward only the caller token", async () => {
  const calls = [];
  const api = createApi({ fetcher: async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/auth/v1/user")) return Response.json({ id });
    assert.equal(new Headers(options.headers).get("cookie"), null);
    assert.equal(options.headers.Authorization, "Bearer verified-test-session");
    assert.equal(options.redirect, "manual");
    return Response.json({ id, status: "RUNNING" });
  } });
  const input = { request_id: id, strategy: "Momentum V2" };
  assert.equal((await api(request("plan-jobs", input), env)).id, id);
  assert.equal((await api(request(`plan-jobs/${id}`), env)).id, id);
  assert.equal(calls[1].url, "https://compute.example.com/v1/plan-jobs");
  assert.equal(calls[3].url, `https://compute.example.com/v1/plan-jobs/${id}`);
  assert.deepEqual(JSON.parse(calls[1].options.body), input);
});
test("job routes require authentication and reject arbitrary status destinations", async () => {
  const api = createApi({ fetcher: async url => {
    assert.ok(url.endsWith("/auth/v1/user"));
    return Response.json({ id });
  } });
  await assert.rejects(api(request(`plan-jobs/${id}`, undefined, null), env), e => e.status === 401);
  await assert.rejects(api(request("plan-jobs/not-a-uuid"), env), e => e.status === 404);
});
test("proxy 524 is an explicit unconfirmed timeout, not generic success", async () => {
  const api = createApi({ fetcher: async url => url.endsWith("/auth/v1/user")
    ? Response.json({ id }) : new Response("provider debug body", { status: 524 }) });
  await assert.rejects(api(request("plan-jobs", { request_id: id }), env),
    e => e.status === 504 && /hosting proxy timed out/.test(e.message) && !e.message.includes("debug"));
});
